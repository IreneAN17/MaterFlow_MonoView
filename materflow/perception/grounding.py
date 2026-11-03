"""Stage 2: generic physical categories only; OCR text is never a detector query."""
from collections import defaultdict
from bisect import bisect_left
from dataclasses import asdict
from PIL import Image
from ..schema import stable_id, validate_box


def box_iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - area
    return area / union if union else 0


class GroundingDINO:
    def __init__(self, config):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.torch, self.config = torch, config
        kwargs = {"revision": config["grounding_revision"], "local_files_only": config["local_files_only"], "cache_dir": config.get("model_cache_dir")}
        self.processor = AutoProcessor.from_pretrained(config["grounding_model"], **kwargs)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(config["grounding_model"], **kwargs).to(config["device"]).eval()
        self.version = {"model_name": config["grounding_model"], "resolved_commit": getattr(self.model.config, "_commit_hash", None)}

    def detect(self, image, label):
        inputs = self.processor(images=image, text=label + ".", return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            output = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            output, inputs.input_ids, threshold=self.config.get("box_threshold", .35),
            text_threshold=self.config.get("text_threshold", .25), target_sizes=[image.size[::-1]])[0]
        width, height = image.size
        records = []
        for pixels, score in zip(result["boxes"].tolist(), result["scores"].tolist()):
            box = [max(0, min(1, v / (width if i % 2 == 0 else height))) for i, v in enumerate(pixels)]
            if box[2] > box[0] and box[3] > box[1]:
                records.append({"object_class": label, "box_xyxy": box, "box_px": pixels, "score": score})
        return records


def merge_frame_detections(detections):
    """NMS only nearly identical same-class boxes, never all same-class objects."""
    merged = []
    for item in sorted(detections, key=lambda d: -d["score"]):
        duplicate = next((d for d in merged if d["object_class"] == item["object_class"] and box_iou(d["box_xyxy"], item["box_xyxy"]) > .85), None)
        if duplicate is None:
            merged.append(dict(item))
    return merged


def ground_relation_objects(frames, relations, context, detector=None):
    detector = detector or GroundingDINO(context["config"])
    context.setdefault("model_versions", {})["grounding"] = detector.version
    by_frame = defaultdict(list)
    for entry in relations:
        by_frame[entry["frame"]["frame_id"]].append(entry)
    keyframes = sorted(by_frame)
    source_classes = {obj["object_class"] for entries in by_frame.values() for e in entries
                      for obj in e["response"]["objects"] if obj["object_class"]}
    sparse = context["config"].get("caption_fps") is not None and context["config"]["caption_fps"] < context["config"].get("sample_fps", 5)
    graphs = []
    for frame in frames:
        entries = by_frame[frame.frame_id]
        query_frames = [frame.frame_id] if entries else []
        classes = set(obj["object_class"] for e in entries for obj in e["response"]["objects"] if obj["object_class"])
        if sparse and not classes:
            position = bisect_left(keyframes, frame.frame_id)
            query_frames = [keyframes[i] for i in (position-1, position) if 0 <= i < len(keyframes)]
            classes = {obj["object_class"] for fid in query_frames for e in by_frame[fid]
                       for obj in e["response"]["objects"] if obj["object_class"]} or source_classes
        # Neighbor captions supply category queries only. Every box is inferred
        # from this frame; no label, relation, state or flow is copied here.
        with Image.open(frame.image_path) as original:
            image = original.convert("RGB")
            detections = merge_frame_detections([d for label in sorted(classes) for d in detector.detect(image, label)])
        for index, det in enumerate(detections):
            det["detection_id"] = stable_id("D", frame.source_id, frame.frame_id, index, det["object_class"], det["box_xyxy"])
            validate_box(det["box_xyxy"])
        assignments = {}
        for entry in entries:
            used = set()
            for obj in entry["response"]["objects"]:
                options = [d for d in detections if d["object_class"] == obj["object_class"] and d["detection_id"] not in used]
                hint = obj.get("region_hint_xyxy")
                chosen = None
                if hint and options:
                    ranked = sorted(((box_iou(hint, d["box_xyxy"]), d) for d in options), key=lambda pair: -pair[0])
                    if ranked[0][0] > .2 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] > .15):
                        chosen = ranked[0][1]
                elif len(options) == 1 and sum(o["object_class"] == obj["object_class"] for o in entry["response"]["objects"]) == 1:
                    chosen = options[0]
                key = entry["request_id"] + ":" + obj["local_id"]
                assignments[key] = chosen["detection_id"] if chosen else None
                if chosen:
                    used.add(chosen["detection_id"])
        graphs.append({"frame": asdict(frame), "detections": detections, "assignments": assignments,
                       "captions": entries, "relations": [r for e in entries for r in e["response"]["parsed"]],
                       "detector_query_classes": sorted(classes), "query_prior_frames": query_frames,
                       "caption_sampled": bool(entries)})
        if len(graphs) % 25 == 0:
            print(f"ground {frame.source_id}: {len(graphs)}/{len(frames)} frames", flush=True)
    return graphs
