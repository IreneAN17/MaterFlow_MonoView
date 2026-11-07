"""Read only declared inputs. Packaging knowledge never fills visual evidence."""
from pathlib import Path
import csv
import re
from openpyxl import load_workbook
from ..schema import read_json, normalize
from . import VERSION

DEFAULTS = {
    "detector_backend": "grounding_dino", "tracker_backend": "klt",
    "detector_fallback": None, "detection_idle_fps": 1.0, "detection_active_fps": 2.5,
    "track_gap_s": .8, "flow_min_points": 6, "flow_inlier_fraction": .65,
    "scene_interval_s": 12.0, "active_scene_interval_s": 4.0,
    "semantic_cooldown_s": 2.0, "local_motion_threshold": .045,
    "display_change_threshold": .025, "clock_interval_s": 1.0,
    "max_clock_anchor_gap_s": 1.3, "clock_score_threshold": .85,
    "ocr_score_threshold": .85, "ocr_retry_interval_s": 8.0,
    "ocr_label_views": 3, "ocr_display_interval_s": 2.0,
    "ocr_batch_size": 8, "semantic_batch_size": 2,
    "semantic_max_attempts": 2, "joint_review": True, "joint_review_min_gap_s": 4.,
    "plateau_min_samples": 3, "measurement_resolutions": {},
    "state_gap_s": 3.0, "event_gap_s": .4,
    "catalog_path": None, "classes_path": None, "gallery_index_path": None,
    "custom_detector_weights": None, "custom_class_map": None,
    "catalog_match_margin": .15, "ocr_python": ".venv-ocr/Scripts/python.exe",
    "segmentation_backend": "off", "segmentation_classes": ["filter funnel"],
    "reference_device": "cpu", "optical_flow_width": 640,
    "work_roi": [0., .08, 1., 1.], "clock_roi": [.78, .93, .997, .985],
    "video_crf": 21, "video_preset": "fast", "chunk_seconds": 60.,
    "semantic_budget": None, "max_clip_images": 3,
}
PATHS = ("catalog_path", "classes_path", "gallery_index_path", "custom_detector_weights",
         "custom_class_map", "ocr_python")

def load_context(config):
    c = dict(config)
    for key, value in DEFAULTS.items():
        c.setdefault(key, value)
    for key in PATHS:
        if c.get(key):
            c[key] = (c["repo_root"] / c[key]).resolve()
    from .validation import validate_config
    validate_config(c)
    manifest = read_json(c["manifest_path"])
    if manifest.get("schema_version") != VERSION or manifest.get("run_id") != c["run_id"]:
        raise ValueError("V4.2 manifest/schema/run_id mismatch")
    book = load_workbook(c["registry_path"], read_only=True, data_only=True)
    registry = {}
    try:
        for name in ("Protocol", "Object Classes", "Reagents", "Event Types", "Relations", "输入词表"):
            rows = book[name].iter_rows(values_only=True)
            headers = next(rows)
            registry[name] = [{k: v for k, v in zip(headers, row) if k is not None}
                              for row in rows if any(x is not None for x in row)]
    finally:
        book.close()
    protocol = {r["field"]: r["value"] for r in registry["Protocol"]}
    if protocol.get("schema_version") != VERSION:
        raise ValueError("Use a separately generated V4.2 registry, not modified V4.1 inputs")
    classes = {r["object_class"]: r for r in registry["Object Classes"]}
    if c.get("classes_path"):
        with c["classes_path"].open(encoding="utf-8-sig", newline="") as f:
            declared = list(csv.DictReader(f))
        if not declared:
            raise ValueError("Configured class list is empty")
        names = [r["object_class"] for r in declared]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate declared class")
        classes = {r["object_class"]: r for r in declared}
    if not classes or any(r.get("id_policy") not in ("auto_instance", "class_only") for r in classes.values()):
        raise ValueError("Every actual class needs explicit id_policy")
    if c["detector_backend"]=="custom_yolo" and c.get("custom_class_map") and c["custom_class_map"].is_file():
        declared=read_json(c["custom_class_map"])
        if set(classes)-set(declared.values()):
            raise ValueError("Custom detector mapping does not cover configured target classes")
    actions = {r["action"] for r in registry["Event Types"]}
    if not actions or not actions <= {"stir", "transfer", "hold", "filter"}:
        raise ValueError("Supported actions: stir, transfer, hold, filter")
    sources = {s["source_id"]: s for s in manifest["capture"]["sources"]}
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+",sid) or sid in (".","..") for sid in sources):
        raise ValueError("Source IDs must be safe single path components")
    paths = {sid: (c["repo_root"] / s["file"]).resolve() for sid, s in sources.items()}
    if len(sources) != len(manifest["capture"]["sources"]):
        raise ValueError("Duplicate source IDs")
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    aliases = {"object_class": {}, "scene_relation": {}, "reagent": {}}
    for space, rows, column in (("object_class", classes.values(), "object_class"),
                                ("scene_relation", registry["Relations"], "relation")):
        for row in rows:
            for alias in [row[column], *str(row.get("aliases") or "").split(";")]:
                if alias.strip():
                    targets = aliases[space].setdefault(normalize(alias), [])
                    if row[column] not in targets:
                        targets.append(row[column])
    visual_context = {
        "objects": [{"class": k, "meaning": r.get("name_zh") or r.get("definition", k)}
                    for k, r in classes.items()],
        "relations": [{"relation": r["relation"], "definition": r["definition"]}
                      for r in registry["Relations"]],
        "actions": [{"action": r["action"], "evidence": r["primary_evidence"]}
                    for r in registry["Event Types"]],
        "signals": [r["value"] for r in registry["输入词表"] if r["field"] == "signal_type"],
    }
    return {"config": c, "manifest": manifest, "registry": registry, "classes": classes,
            "actions": actions, "aliases": aliases, "sources": sources,
            "source_paths": paths, "visual_context": visual_context, "objects": {}}
