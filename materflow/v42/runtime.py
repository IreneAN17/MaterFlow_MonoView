"""Stage-specific dependency signatures and atomic process status."""
from pathlib import Path
import importlib.metadata
import time
from ..schema import file_hash, stable_id, read_json
from ..v41.runtime import Progress, Ids, process_info, gpu_sample, utc, verified

STAGES = ("prepare", "scan", "ocr", "identify", "semantic", "aggregate", "derive", "export")
# Explicit DAG: catalog/presentation changes do not invalidate visual evidence.
UPSTREAM = {
    "prepare": (), "scan": ("prepare",), "ocr": ("prepare", "scan"),
    "identify": ("scan", "ocr"), "semantic": ("prepare", "scan", "ocr"),
    "aggregate": ("prepare", "scan", "ocr", "identify", "semantic"),
    "derive": ("aggregate",), "export": ("prepare", "scan", "aggregate", "derive"),
}
FILES = {
    "prepare": ("io.py", "clock.py"),
    "scan": ("scan.py", "tracking.py", "detectors.py", "segmentation.py", "../perception/grounding.py"),
    "ocr": ("ocr.py", "../../runs/v42/ocr_worker.py"),
    "identify": ("catalog.py", "gallery.py"),
    "semantic": ("semantic.py", "../perception/relations.py", "../backends/transformers_backend.py",
                 "../backends/json_constraints.py"),
    "aggregate": ("aggregate.py", "intervals.py", "clock.py", "catalog.py", "schema_v4_2.json", "../v41/runtime.py"),
    "derive": ("derive.py", "timeline.py", "../v41/domain.py", "../materials/events.py"),
    "export": ("export.py", "video.py", "schema_v4_2.json", "clock.py", "timeline.py", "time_refine.py"),
}
KEYS = {
    "prepare": ("sample_fps", "ranges_s", "clock_roi", "clock_interval_s",
                "clock_score_threshold", "cut_threshold", "timezone"),
    "scan": ("detector_backend", "detector_fallback", "custom_detector_weights", "custom_class_map",
             "grounding_model", "grounding_revision", "device", "box_threshold", "text_threshold",
             "class_thresholds", "work_roi", "detection_idle_fps", "detection_active_fps",
             "local_motion_threshold", "tracker_backend", "track_gap_s", "flow_min_points",
             "flow_inlier_fraction", "optical_flow_width", "segmentation_backend", "segmentation_classes"),
    "ocr": ("ocr_python", "ocr_score_threshold", "ocr_retry_interval_s", "ocr_label_views",
            "ocr_display_interval_s", "ocr_batch_size", "display_change_threshold"),
    "identify": ("catalog_path", "gallery_index_path", "catalog_match_margin", "reference_device"),
    "semantic": ("model_name", "model_revision", "max_image_pixels", "max_new_tokens",
                 "quantization", "bnb_skip_modules", "attention_backend", "structured_output",
                 "seed", "do_sample", "temperature", "top_p", "top_k", "presence_penalty",
                 "repetition_penalty", "semantic_batch_size", "scene_interval_s",
                 "active_scene_interval_s", "semantic_cooldown_s", "joint_review",
                 "joint_review_min_gap_s", "max_clip_images", "semantic_budget", "semantic_max_attempts"),
    "aggregate": ("measurement_resolutions", "plateau_min_samples", "state_gap_s",
                  "max_clock_anchor_gap_s", "clock_score_threshold"),
    "derive": ("event_gap_s",), "export": ("video_crf", "video_preset", "annotated_video"),
}

def dependencies(stage, context, summary):
    c = context["config"]
    package = Path(__file__).parent
    code = {}
    for name in (*FILES[stage], "context.py", "runtime.py"):
        path = package / name
        if path.is_file():
            code[name] = file_hash(path)
    values = {k: c.get(k) for k in KEYS[stage]}
    for key in ("custom_detector_weights", "custom_class_map", "catalog_path", "gallery_index_path"):
        if key in values and values[key]:
            path = Path(values[key])
            values[key] = {"path": str(path), "sha256": file_hash(path) if path.is_file() else None}
    values["class_contract"] = [(k, r.get("id_policy"), r.get("aliases"))
                                for k, r in context["classes"].items()]
    values["action_contract"] = context["visual_context"]["actions"]
    if stage == "prepare":
        values["sources"] = {k: file_hash(p) for k, p in context["source_paths"].items()}
        values["manifest"] = context["manifest"]["capture"]
        worker = c["repo_root"] / "runs/v42/ocr_worker.py"
        code["ocr_worker"] = file_hash(worker)
    if stage in ("scan", "semantic"):
        for name in ("torch", "transformers", "bitsandbytes", "opencv-python"):
            try:
                values[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                values[name] = None
    if stage == "semantic":
        values["prompt"] = file_hash(c["prompt_template_path"])
    values["upstream"] = {name: summary["stages"].get(name, {}).get("artifacts", {})
                          for name in UPSTREAM[stage]}
    return stable_id("V42", stage, code, values)

def descendants(stage):
    result = {stage}
    for name in STAGES[STAGES.index(stage)+1:]:
        if result.intersection(UPSTREAM[name]):
            result.add(name)
    return result
