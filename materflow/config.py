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
    return config
