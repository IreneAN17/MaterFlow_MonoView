from pathlib import Path
from PIL import Image
from .reader import read_frames
from ..schema import FrameRecord, validate_box

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def sample_frames(source_path, sampling_config):
    """Persist full resolution sampled images, return reusable FrameRecords."""
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Capture source missing: {path}")
    source_id = sampling_config["source_id"]
    output = Path(sampling_config["output_dir"]) / "frames" / source_id
    output.mkdir(parents=True, exist_ok=True)
    records = []
    if path.is_dir() or path.suffix.lower() in IMAGE_SUFFIXES:
        paths = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) if path.is_dir() else [path]
        if not paths:
            raise ValueError(f"No images in {path}")
        timestamps = sampling_config.get("timestamps_s")
        if timestamps is not None and len(timestamps) != len(paths):
            raise ValueError("timestamps_s must correspond one-to-one to sorted image files")
        for index, item in enumerate(paths):
            with Image.open(item) as image:
                width, height = image.size
            time_s = timestamps[index] if timestamps is not None else None
            records.append(FrameRecord(source_id, index, index, time_s, str(item.resolve()), width=width, height=height))
    else:
        fps = sampling_config["sample_fps"]
        next_time = None
        last_time = None
        for decoded in read_frames(path):
            time_s = decoded["source_time_s"]
            if time_s is not None:
                if last_time is not None and time_s < last_time:
                    raise ValueError(f"Non-monotonic PTS in {source_id}")
                last_time = time_s
                if next_time is not None and time_s + 1e-9 < next_time:
                    continue
                next_time = time_s + 1 / fps if next_time is None else next_time + (int((time_s - next_time) * fps + 1e-9) + 1) / fps
            index = len(records)
            image = decoded.pop("image")
            image_path = output / f"{index:08d}.png"
            image.save(image_path)
            records.append(FrameRecord(source_id, index, image_path=str(image_path.resolve()),
                                       width=image.width, height=image.height, **decoded))
    if not records:
        raise ValueError(f"No readable frames in {path}")
    known = [r.source_time_s for r in records if r.source_time_s is not None]
    if known != sorted(known):
        raise ValueError(f"Non-monotonic image timestamps: {source_id}")
    return records


def build_time_windows(frames, windowing_config):
    """Indexes refer to the given source list. End is exclusive, including tail windows."""
    size, stride = windowing_config["window_frames"], windowing_config["window_stride"]
    windows = []
    for start in range(0, len(frames), stride):
        end = min(len(frames), start + size)
        selection = frames[start:end]
        if len({f.source_id for f in selection}) != 1:
            raise ValueError("Build windows separately for each source")
        windows.append({"source_id": selection[0].source_id, "start": start, "end": end,
                        "frame_ids": [f.frame_id for f in selection],
                        "source_frame_indices": [f.source_frame_index for f in selection],
                        "times_s": [f.source_time_s for f in selection]})
        if end == len(frames):
            break
    return windows


def closed_job_bounds(window):
    """Explicit adapter for SG-Ego's closed start-end text jobs."""
    return f"{window['start']}-{window['end'] - 1}"


def crop_regions(image, regions):
    """Input normalized xyxy; output crop + one explicit map to original pixels."""
    opened = not isinstance(image, Image.Image)
    original = Image.open(image) if opened else image
    try:
        result = []
        for region in regions:
            box = validate_box(region["box_xyxy"])
            width, height = original.size
            pixels = [int(box[0] * width), int(box[1] * height), min(width, max(int(box[2] * width), int(box[0] * width) + 1)),
                      min(height, max(int(box[3] * height), int(box[1] * height) + 1))]
            result.append({"image": original.crop(pixels).convert("RGB"), "crop_xyxy_original_px": pixels,
                           "original_size": [width, height], "region": region})
        return result
    finally:
        if opened:
            original.close()


def select_caption_frames(frames, config):
    """Select a timestamp grid without changing detection frames or their IDs."""
    fps = config.get("caption_fps")
    if fps is None:
        return list(frames)
    import math
    if not 0 < fps <= config["sample_fps"]:
        raise ValueError("caption_fps must be positive and <= sample_fps")
    origins, thresholds, previous, selected = {}, {}, {}, []
    for frame in frames:
        sid, time_s = frame.source_id, frame.source_time_s
        if time_s is None:
            raise ValueError("caption_fps requires known source timestamps")
        if sid in previous and time_s < previous[sid]:
            raise ValueError("Caption sampling requires ordered source timestamps")
        previous[sid] = time_s
        origins.setdefault(sid, time_s)
        thresholds.setdefault(sid, time_s)
        if time_s + 1e-8 >= thresholds[sid]:
            selected.append(frame)
            slot = math.floor((time_s-origins[sid])*fps + 1e-8)
            thresholds[sid] = origins[sid] + (slot+1)/fps
    return selected
