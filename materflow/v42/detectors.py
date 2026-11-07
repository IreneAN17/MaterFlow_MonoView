"""Real detector adapters with explicit model/class contracts."""
from pathlib import Path
import re
import importlib.metadata
from ..schema import stable_id, file_hash, read_json, write_json
from ..perception.grounding import GroundingDINO, merge_frame_detections, box_iou
from .io import pixel_hash, internal

PHRASES = {
    "balance": ["laboratory balance", "digital scale"],
    "bottle": ["reagent bottle"], "pipette": ["pipette", "dropper"],
    "container": ["sample container"], "oven": ["drying oven"],
    "stirrer": ["magnetic stirrer"], "hand": ["hand", "glove"],
}
DESCRIPTORS = {"a", "an", "the", "laboratory", "digital", "electronic", "reagent",
               "sample", "drying", "magnetic", "glass", "transparent", "plastic"}

def map_phrase(raw, phrases):
    words = set(re.findall("[a-z]+", str(raw).lower())) - DESCRIPTORS
    if not words:
        return None
    candidates = {category for category, terms in phrases.items()
                  if any(words <= set(re.findall("[a-z]+", term.lower())) for term in terms)}
    return next(iter(candidates)) if len(candidates) == 1 else None

def quality_filter(candidates, c):
    accepted, rejected = [], []
    for item in candidates:
        category = item.get("object_class")
        score = item["score"]
        box = item["box_xyxy"]
        reason = None
        if category is None:
            reason = "ambiguous_detector_phrase"
        elif score < c.get("class_thresholds", {}).get(category, c.get("box_threshold", .3)):
            reason = "below_class_threshold"
        elif box[2] <= box[0] or box[3] <= box[1]:
            reason = "invalid_box"
        else:
            roi = c["work_roi"]
            x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
            if not roi[0] <= x <= roi[2] or not roi[1] <= y <= roi[3]:
                reason = "outside_work_roi"
        if reason:
            rejected.append({**item, "reason": reason})
        else:
            accepted.append(item)
    ordered = merge_frame_detections(accepted)
    vessels = {"container", "beaker", "weighing cup", "bottle"}
    removed = set()
    for i, a in enumerate(ordered):
        for j in range(i+1, len(ordered)):
            b = ordered[j]
            if a["object_class"] == b["object_class"]:
                continue
            if not {a["object_class"], b["object_class"]} <= vessels or box_iou(a["box_xyxy"], b["box_xyxy"]) < .9:
                continue
            if "container" in (a["object_class"], b["object_class"]):
                removed.add(i if a["object_class"] == "container" else j)
            elif abs(a["score"]-b["score"]) < .04:
                removed.update((i, j))
            else:
                removed.add(i if a["score"] < b["score"] else j)
    rejected += [{**ordered[i], "reason": "exclusive_vessel_class_conflict"} for i in removed]
    return [d for i,d in enumerate(ordered) if i not in removed], rejected

class GroundedDetector(GroundingDINO):
    def __init__(self, context):
        super().__init__(context["config"])
        self.torch.set_num_threads(context["config"].get("torch_num_threads", 4))
        self.phrases = {name: PHRASES.get(name, [name]) for name in context["classes"]}
        self.phrases["hand"] = PHRASES["hand"]
        self.version.update(
            backend="grounding_dino", transformers=importlib.metadata.version("transformers"),
            preprocessing=self.processor.image_processor.to_dict(),
            implementation=file_hash(Path(__file__)), dtype=str(self.model.dtype))
        self.cache = internal(context) / "cache/detection"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls, self.hits, self.corrupt = 0, 0, 0

    def query(self, image):
        key = stable_id("GD42", pixel_hash(image), self.version, self.phrases,
                        self.config.get("text_threshold", .25))
        path = self.cache / (key + ".json")
        if path.is_file():
            try:
                item = read_json(path)
                if item.get("key") == key and item.get("digest") == stable_id("", item.get("raw")):
                    self.hits += 1
                    return quality_filter(item["raw"], self.config)
            except (ValueError, OSError, KeyError):
                pass
            self.corrupt += 1
        text = ". ".join(term for terms in self.phrases.values() for term in terms) + "."
        inputs = self.processor(images=image, text=text, return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            output = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            output, inputs.input_ids, threshold=.10,
            text_threshold=self.config.get("text_threshold", .25),
            target_sizes=[image.size[::-1]])[0]
        self.calls += 1
        raw = []
        w,h = image.size
        for pixels, score, label in zip(result["boxes"].tolist(), result["scores"].tolist(), result["text_labels"]):
            box = [max(0., min(1., v/(w if i%2==0 else h))) for i,v in enumerate(pixels)]
            raw.append({"object_class": map_phrase(label, self.phrases), "raw_label": str(label),
                        "score": float(score), "box_xyxy": box, "evidence_kind": "detector"})
        write_json(path, {"key": key, "raw": raw, "digest": stable_id("", raw), "model": self.version})
        return quality_filter(raw, self.config)

class CustomDetector:
    def __init__(self, context):
        c = context["config"]
        path = c.get("custom_detector_weights")
        mapping_path = c.get("custom_class_map")
        if not path or not path.is_file() or not mapping_path or not mapping_path.is_file():
            raise FileNotFoundError("Custom weights and explicit custom_class_map are required; no trained detector supplied")
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError("Install the pinned optional detector environment; existing GPU environment is not silently changed") from e
        self.config = c
        self.model = YOLO(str(path))
        self.mapping = read_json(mapping_path)
        if set(self.mapping) != set(self.model.names.values()):
            raise ValueError("Model class names and declared custom mapping differ")
        if any(v not in context["classes"] and v != "hand" for v in self.mapping.values()):
            raise ValueError("Custom class maps outside registry")
        self.version = {"backend": "custom_yolo", "weights_sha256": file_hash(path),
                        "mapping_sha256": file_hash(mapping_path),
                        "ultralytics": importlib.metadata.version("ultralytics")}
        self.calls, self.hits, self.corrupt = 0,0,0

    def query(self, image):
        r = self.model.predict(image, conf=.1, verbose=False, device=self.config["device"])[0]
        self.calls += 1
        raw = [{"object_class": self.mapping[self.model.names[int(k)]], "score": float(s),
                "box_xyxy": list(map(float,b)), "evidence_kind": "detector"}
               for b,s,k in zip(r.boxes.xyxyn.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())]
        return quality_filter(raw, self.config)

def create_detector(context):
    c = context["config"]
    if c["detector_backend"] == "grounding_dino":
        return GroundedDetector(context)
    try:
        return CustomDetector(context)
    except (FileNotFoundError, RuntimeError):
        if c["detector_fallback"] != "grounding_dino":
            raise
        model = GroundedDetector(context)
        model.version["explicit_fallback_reason"] = "custom_backend_not_available"
        return model
