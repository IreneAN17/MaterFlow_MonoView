"""SAM2 image/video segmentation with DINOv2 appearance matching.

Independent implementation inspired by SG-Ego ecf24c3 (no source copied).
All history conversion happens here, before observations can cite frames.
"""
from collections import defaultdict
from copy import deepcopy
import numpy as np
from PIL import Image
from ..perception.grounding import box_iou
from ..schema import stable_id, validate_box


def history_frame(frames, local_index):
    if not isinstance(local_index, int) or not 0 <= local_index < len(frames):
        raise ValueError(f"Invalid window frame index: {local_index}")
    frame = frames[local_index]
    return {"source_id": frame.source_id, "frame_id": frame.frame_id,
            "source_frame_index": frame.source_frame_index, "source_time_s": frame.source_time_s,
            "image_path": frame.image_path, "window_frame_idx": local_index}


def mask_box(mask):
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    height, width = mask.shape
    return [float(xs.min()/width), float(ys.min()/height), float((xs.max()+1)/width), float((ys.max()+1)/height)]


def cosine(a, b):
    a, b = np.asarray(a), np.asarray(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / denom) if denom else -1.0


class TrackingModels:
    def __init__(self, config):
        import torch
        from transformers import Sam2Model, Sam2Processor, Sam2VideoModel, Sam2VideoProcessor, AutoImageProcessor, Dinov2Model
        self.torch, self.config = torch, config
        self.device = config["device"]
        self.dtype = torch.bfloat16 if self.device.startswith("cuda") else torch.float32
        kwargs = {"revision": config["sam2_revision"], "local_files_only": config["local_files_only"], "cache_dir": config.get("model_cache_dir")}
        self.image_processor = Sam2Processor.from_pretrained(config["sam2_model"], **kwargs)
        self.image_model = Sam2Model.from_pretrained(config["sam2_model"], **kwargs).to(self.device, dtype=self.dtype).eval()
        self.video_processor = Sam2VideoProcessor.from_pretrained(config["sam2_model"], **kwargs)
        self.video_model = Sam2VideoModel.from_pretrained(config["sam2_model"], **kwargs).to(self.device, dtype=self.dtype).eval()
        kwargs = {"revision": config["dinov2_revision"], "local_files_only": config["local_files_only"], "cache_dir": config.get("model_cache_dir")}
        self.dino_processor = AutoImageProcessor.from_pretrained(config["dinov2_model"], **kwargs)
        self.dino_model = Dinov2Model.from_pretrained(config["dinov2_model"], **kwargs).to(self.device).eval()
        self.version = {"sam2": {"model_name": config["sam2_model"], "resolved_commit": getattr(self.image_model.config, "_commit_hash", None)},
                        "dinov2": {"model_name": config["dinov2_model"], "resolved_commit": getattr(self.dino_model.config, "_commit_hash", None)}}

    def segment_and_embed(self, image, detection):
        box = validate_box(detection["box_xyxy"])
        width, height = image.size
        pixels = [v * (width if i % 2 == 0 else height) for i, v in enumerate(box)]
        inputs = self.image_processor(images=image, input_boxes=[[pixels]], return_tensors="pt").to(self.device)
        inputs["pixel_values"] = inputs["pixel_values"].to(self.dtype)
        with self.torch.inference_mode():
            output = self.image_model(**inputs, multimask_output=False)
        mask = self.image_processor.post_process_masks(output.pred_masks.cpu(), inputs["original_sizes"].cpu())[0][0, 0].numpy().astype(bool)
        crop = image.crop(pixels).convert("RGB")
        crop_mask = Image.fromarray(mask).crop(pixels).resize(crop.size)
        isolated = Image.new("RGB", crop.size, (127, 127, 127))
        isolated.paste(crop, mask=crop_mask)
        inputs = self.dino_processor(images=isolated, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            features = self.dino_model(**inputs).last_hidden_state[:, 0]
        vector = features[0].float().cpu().numpy()
        vector /= max(np.linalg.norm(vector), 1e-12)
        return mask, vector.tolist()

    def propagate(self, images, seed_index, mask):
        session = self.video_processor.init_video_session(video=images, inference_device=self.device,
                    video_storage_device="cpu", inference_state_device=self.device, dtype=self.dtype)
        self.video_processor.add_inputs_to_inference_session(session, frame_idx=seed_index, obj_ids=1, input_masks=[mask])
        boxes = {}
        with self.torch.inference_mode():
            self.video_model(inference_session=session, frame_idx=seed_index)
            for output in self.video_model.propagate_in_video_iterator(session, start_frame_idx=seed_index):
                masks = self.video_processor.post_process_masks([output.pred_masks],
                    original_sizes=[[session.video_height, session.video_width]], binarize=True)[0]
                binary = masks[0, 0].cpu().numpy().astype(bool)
                boxes[output.frame_idx] = mask_box(binary)
        return boxes


def consolidate_object_tracks(detections, context, models=None):
    models = models or TrackingModels(context["config"])
    context.setdefault("model_versions", {}).update(models.version)
    frames = context["frames"]
    graph_by_id = {g["frame"]["frame_id"]: g for g in detections}
    windows = []
    for window in context["windows"]:
        selected = frames[window["start"]:window["end"]]
        images = []
        for frame in selected:
            with Image.open(frame.image_path) as image:
                images.append(image.convert("RGB"))
        tracks = []
        for index, (frame, image) in enumerate(zip(selected, images)):
            frame_detections = graph_by_id[frame.frame_id]["detections"]
            # Compute all candidates first. Reject ties in either direction.
            prepared = [(d, *models.segment_and_embed(image, d)) for d in frame_detections]
            matches = {}
            scores = {}
            for di, (det, mask, feature) in enumerate(prepared):
                for ti, track in enumerate(tracks):
                    predicted = track["predicted_boxes"].get(index)
                    if track["object_class"] != det["object_class"] or predicted is None:
                        continue
                    similarity = cosine(feature, track["feature"])
                    if similarity >= .75 and box_iou(predicted, det["box_xyxy"]) >= .25:
                        scores[di, ti] = similarity + .2 * box_iou(predicted, det["box_xyxy"])
            for di in range(len(prepared)):
                ranked = sorted(((score, ti) for (d, ti), score in scores.items() if d == di), reverse=True)
                if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .08):
                    continue
                best_score, ti = ranked[0]
                alternatives = [score for (d, t), score in scores.items() if t == ti and d != di]
                if alternatives and best_score - max(alternatives) < .08:
                    continue
                matches[di] = ti
            for di, (det, mask, feature) in enumerate(prepared):
                if di in matches:
                    track = tracks[matches[di]]
                else:
                    track = {"local_track_id": stable_id("WT", window, det["detection_id"]), "object_class": det["object_class"],
                             "feature": feature, "history": [], "predicted_boxes": {}, "object_id": None}
                    # Unknown image timing permits spatial features, not temporal propagation.
                    if all(f.source_time_s is not None for f in selected) and len({im.size for im in images}) == 1:
                        track["predicted_boxes"] = models.propagate(images, index, mask)
                    tracks.append(track)
                track["history"].append({**history_frame(selected, index), "detection_id": det["detection_id"],
                                         "box_xyxy": det["box_xyxy"], "mask_box_xyxy": mask_box(mask), "detection_score": det["score"]})
        # Predicted history is separate from observations of relations, OCR and flows.
        for track in tracks:
            track["predicted_history"] = [{**history_frame(selected, i), "box_xyxy": box} for i, box in track.pop("predicted_boxes").items() if box]
        windows.append({"window": window, "tracks": tracks})
        print(f"consolidate {window['source_id']}: window {len(windows)}/{len(context['windows'])}, {len(tracks)} tracks", flush=True)
        for image in images:
            image.close()
    return {"windows": windows, "frame_graphs": detections}


def link_window_tracks(window_tracks, context):
    """Adjacent windows link only through unambiguous shared detections or overlap + DINOv2."""
    groups = []
    previous = []
    for item in window_tracks["windows"]:
        current = []
        for track in item["tracks"]:
            candidates = []
            actual = {h["detection_id"] for h in track["history"]}
            for group in previous:
                if group["object_class"] != track["object_class"]:
                    continue
                shared = actual & {h["detection_id"] for h in group["history"]}
                by_frame = {h["frame_id"]: h for h in group["history"]}
                conflicts = any(h["frame_id"] in by_frame and h["detection_id"] != by_frame[h["frame_id"]]["detection_id"] for h in track["history"])
                if conflicts:
                    continue
                # Exact detection IDs are the same physical localization in overlap frames.
                if shared and cosine(group["feature"], track["feature"]) >= .75:
                    candidates.append(group)
            if len(candidates) == 1 and candidates[0] not in current:
                group = candidates[0]
                seen = {h["detection_id"] for h in group["history"]}
                group["history"].extend(h for h in track["history"] if h["detection_id"] not in seen)
                group["window_track_ids"].append(track["local_track_id"])
                group["predicted_history"].extend(track["predicted_history"])
            else:
                group = deepcopy(track)
                group["window_track_ids"] = [track["local_track_id"]]
                group["track_id"] = stable_id("T", context["config"]["run_id"], track["history"][0]["detection_id"])
                groups.append(group)
            current.append(group)
        previous = current
    detection_to_track = {}
    for group in groups:
        for entry in group["history"]:
            old = detection_to_track.get(entry["detection_id"])
            if old and old != group["track_id"]:
                # Preserve ambiguous overlap as a separate unbound per-detection track.
                detection_to_track[entry["detection_id"]] = stable_id("T", entry["detection_id"])
            else:
                detection_to_track[entry["detection_id"]] = group["track_id"]
    mapping = {}
    for graph in window_tracks["frame_graphs"]:
        for local, detection_id in graph["assignments"].items():
            mapping[local] = detection_to_track.get(detection_id) if detection_id else None
    return {**window_tracks, "tracks": groups, "local_to_track": mapping, "detection_to_track": detection_to_track}
