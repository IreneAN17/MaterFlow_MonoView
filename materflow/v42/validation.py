"""Configuration contracts shared by CLI and tests."""
import math

def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be positive finite")

def validate_config(c):
    from ..schema import validate_box
    for key in ("sample_fps", "detection_idle_fps", "detection_active_fps", "track_gap_s",
                "scene_interval_s", "active_scene_interval_s", "semantic_cooldown_s",
                "clock_interval_s", "max_clock_anchor_gap_s", "ocr_retry_interval_s",
                "ocr_display_interval_s", "chunk_seconds", "state_gap_s"):
        positive(c[key], key)
    if max(c["detection_idle_fps"], c["detection_active_fps"]) > c["sample_fps"]:
        raise ValueError("Detector refresh cannot exceed decoded sample rate")
    for key in ("flow_inlier_fraction", "ocr_score_threshold", "clock_score_threshold",
                "local_motion_threshold", "display_change_threshold"):
        if isinstance(c[key], bool) or not 0 <= c[key] <= 1:
            raise ValueError("Invalid " + key)
    for key in ("ocr_label_views", "ocr_batch_size", "semantic_batch_size",
                "semantic_max_attempts", "plateau_min_samples", "optical_flow_width",
                "max_clip_images", "flow_min_points"):
        if isinstance(c[key], bool) or not isinstance(c[key], int) or c[key] < 1:
            raise ValueError("Invalid integer " + key)
    if c["max_clip_images"] < 2:
        raise ValueError("Joint reviews require >=2 images")
    for key in ("clock_roi", "work_roi"):
        validate_box(c[key])
    if c["tracker_backend"] not in ("klt", "bytetrack", "botsort"):
        raise ValueError("Unknown tracker backend")
    if c["detector_backend"] not in ("grounding_dino", "custom_yolo"):
        raise ValueError("Unknown detector backend")
    if c["detector_fallback"] not in (None, "grounding_dino"):
        raise ValueError("Invalid explicit detector fallback")
    if c["segmentation_backend"] not in ("off", "sam2"):
        raise ValueError("Invalid segmentation backend")
    if c.get("semantic_budget") is not None:
        positive(c["semantic_budget"], "semantic_budget")
    for unit, resolution in c.get("measurement_resolutions", {}).items():
        positive(resolution, "resolution " + unit)
    ranges = c.get("ranges_s", [])
    for i, pair in enumerate(ranges):
        if len(pair) != 2 or any(isinstance(x, bool) or not math.isfinite(x) for x in pair):
            raise ValueError("Invalid source range")
        if not 0 <= pair[0] < pair[1] or (i and pair[0] < ranges[i-1][1]):
            raise ValueError("Ranges must be sorted and disjoint")
