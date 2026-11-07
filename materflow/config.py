from pathlib import Path
import math
import re
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_config(config_path):
    path = Path(config_path).resolve()
    with path.open(encoding="utf-8-sig") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("run YAML must be a mapping")
    for name in ("run_id", "registry_path", "manifest_path", "output_dir"):
        if not config.get(name):
            raise ValueError(f"Missing config field: {name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", config["run_id"]):
        raise ValueError("run_id must be a safe path component")
    for obsolete in ("video_path", "enable_lineage", "backend", "pipeline_config"):
        if obsolete in config:
            raise ValueError(f"Unsupported legacy option {obsolete}; use the explicit v4.0 run example")
    config["repo_root"] = REPO_ROOT
    config["config_path"] = path
    config.setdefault("prompt_template_path", "configs/prompts/lab_frame.template.txt")
    for name in ("registry_path", "manifest_path", "output_dir", "prompt_template_path"):
        config[name] = (REPO_ROOT / config[name]).resolve()
        if name != "output_dir" and not config[name].is_file():
            raise FileNotFoundError(f"{name}: {config[name]}")
    defaults = {"model_name": "Qwen/Qwen3.5-9B", "caption_batch_size": 1, "sample_fps": 5, "window_frames": 10,
                "window_stride": 8, "device": "cuda", "max_new_tokens": 2048,
                "grounding_model": "IDEA-Research/grounding-dino-tiny",
                "sam2_model": "facebook/sam2.1-hiera-tiny", "dinov2_model": "facebook/dinov2-small",
                "model_revision": "main", "grounding_revision": "main", "sam2_revision": "main",
                "dinov2_revision": "main", "local_files_only": False, "roi_reread": True,
                "clip_review": True, "max_clip_reviews_per_source": 10}
    for key, value in defaults.items():
        config.setdefault(key, value)
    config.setdefault("caption_fps", None)
    if config["caption_fps"] is not None:
        fps = config["caption_fps"]
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0 < fps <= config["sample_fps"]:
            raise ValueError("caption_fps must be positive and <= sample_fps")
    if config.get("caption_reuse_path"):
        config["caption_reuse_path"] = (REPO_ROOT / config["caption_reuse_path"]).resolve()
        if not config["caption_reuse_path"].is_file():
            raise FileNotFoundError(config["caption_reuse_path"])
    config.setdefault("quantization", None)
    if config["quantization"] not in (None, "nf4"):
        raise ValueError("quantization must be null or nf4")
    if config.get("model_cache_dir"):
        config["model_cache_dir"] = (REPO_ROOT / config["model_cache_dir"]).resolve()
    if config.get("max_image_pixels") is not None and (not isinstance(config["max_image_pixels"], int) or config["max_image_pixels"] < 4096):
        raise ValueError("max_image_pixels must be an integer >= 4096")
    if isinstance(config["sample_fps"], bool) or not isinstance(config["sample_fps"], (int, float)) or not math.isfinite(config["sample_fps"]) or config["sample_fps"] <= 0:
        raise ValueError("sample_fps must be positive")
    for name in ("window_frames", "window_stride", "caption_batch_size"):
        if isinstance(config[name], bool) or not isinstance(config[name], int) or config[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if config["window_stride"] > config["window_frames"]:
        raise ValueError("window_stride cannot leave uncovered frames")
    if "caption_interval_s" in config:
        from .schema import validate_box
        for key in ("caption_interval_s","caption_active_fps","detection_fps","max_track_gap_s"):
            value=config.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:raise ValueError(f"{key} must be positive and finite")
        if config["detection_fps"]>config["sample_fps"] or config["caption_active_fps"]>config["sample_fps"]:raise ValueError("Analysis rates cannot exceed sampled frames")
        for key in ("motion_threshold","cut_threshold","appearance_threshold","conflict_iou","conflict_margin","box_threshold","text_threshold"):
            value=config.get(key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=1):raise ValueError(f"Invalid {key}")
        for value in config.get("class_thresholds",{}).values():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=1:raise ValueError("Invalid per-class detection threshold")
        for key in ("clock_roi","work_roi"):
            if key in config:validate_box(config[key])
        ranges=config.get("ranges_s",[])
        for i,pair in enumerate(ranges):
            if len(pair)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in pair) or not 0<=pair[0]<pair[1] or (i and pair[0]<ranges[i-1][1]):raise ValueError("ranges_s must be ordered non-overlapping positive intervals")
        chunk=config.get("track_chunk_frames",40)
        if isinstance(chunk,bool) or not isinstance(chunk,int) or chunk<1:raise ValueError("Invalid track_chunk_frames")
        if config.get("relevance_policy","interaction_readout_foreground") not in ("interaction_readout_foreground","interaction_readout"):raise ValueError("Unknown relevance policy")
    return config
