"""One decoder (PyAV); source frame indexes and PTS are never sample indexes."""
from pathlib import Path
import av


def inspect_video(video_path):
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        return {"path": str(Path(video_path).resolve()), "width": stream.width, "height": stream.height,
                "average_rate": float(stream.average_rate) if stream.average_rate else None,
                "duration_s": float(stream.duration * stream.time_base) if stream.duration is not None else None,
                "frames": stream.frames or None, "time_base": str(stream.time_base), "start_pts": stream.start_time}


def read_frames(video_path, frame_indices=None):
    """Yield decoded frame metadata and PIL image, sequentially and bounded in memory."""
    requested = set(frame_indices) if frame_indices is not None else None
    seen = set()
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        origin = stream.start_time
        for index, frame in enumerate(container.decode(stream)):
            if requested is not None and index not in requested:
                continue
            time_s = float((frame.pts - (origin if origin is not None else 0)) * frame.time_base) if frame.pts is not None else None
            yield {"source_frame_index": index, "source_time_s": time_s, "pts": frame.pts,
                   "time_base": str(frame.time_base), "image": frame.to_image()}
            seen.add(index)
            if requested is not None and seen == requested:
                break
    if requested is not None and requested != seen:
        raise ValueError(f"Unavailable decoded frame indices: {sorted(requested - seen)}")
