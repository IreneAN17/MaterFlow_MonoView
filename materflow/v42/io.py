"""Streaming indexes and reproducible source-frame materialization."""
from contextlib import contextmanager
from pathlib import Path
from dataclasses import asdict
from hashlib import sha256
import json
import os
import av
import numpy as np
from ..schema import FrameRecord, file_hash, json_default, write_json
from ..v41.sampling import in_ranges

def internal(context):
    p = context["config"]["output_dir"] / "internal"
    p.mkdir(parents=True, exist_ok=True)
    return p

def records(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)

@contextmanager
def jsonl_writer(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".pending")
    with tmp.open("w", encoding="utf-8") as f:
        def append(record):
            f.write(json.dumps(record, ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
        yield append
    tmp.replace(path)

def source_frames(context):
    c = context["config"]
    for sid, path in context["source_paths"].items():
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.codec_context.thread_count = c.get("torch_num_threads", 4)
            due = -1.
            for index, decoded in enumerate(container.decode(video=0)):
                if decoded.pts is None:
                    raise ValueError("V4.2 needs source PTS")
                t = float((decoded.pts - (stream.start_time or 0)) * decoded.time_base)
                if c.get("ranges_s") and t >= max(b for a, b in c["ranges_s"]):
                    break
                if not in_ranges(t, c.get("ranges_s", [])) or t + 1e-7 < due:
                    continue
                due = t + 1 / c["sample_fps"] - 1e-7
                row = asdict(FrameRecord(sid, index, index, t, "", decoded.pts,
                                        str(decoded.time_base), stream.width, stream.height))
                yield row, decoded.to_image().convert("RGB")

class FrameStore:
    def __init__(self, context):
        self.context = context
        self.folder = internal(context) / "evidence/frames"
        if "_source_hashes" not in context:
            context["_source_hashes"]={sid:file_hash(path) for sid,path in context["source_paths"].items()}
        self.source_hashes=context["_source_hashes"]

    def path(self, row):
        return self.folder / row["source_id"] / (str(row["frame_id"]).zfill(8) + ".png")

    def input_key(self,row):
        from ..schema import stable_id
        return stable_id("FRAME",self.source_hashes[row["source_id"]],row["frame_id"],
                         row.get("pts"),row.get("time_base"),"PyAV RGB24 full-resolution")

    def valid(self,row):
        from ..schema import read_json
        path=self.path(row);meta=path.with_suffix(".json")
        if not path.is_file() or not meta.is_file():
            return False
        try:
            info=read_json(meta)
            return info.get("input_key")==self.input_key(row) and info.get("sha256")==file_hash(path)
        except (ValueError,OSError):
            return False

    def save(self, row, image):
        target = self.path(row)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.valid(row):
            image.save(target, compress_level=1)
            write_json(target.with_suffix(".json"),{"input_key":self.input_key(row),"sha256":file_hash(target)})
        return {**row, "image_path": str(target.resolve())}

    def fetch(self, rows):
        pending = {}
        for row in rows:
            path = self.path(row)
            if self.valid(row):
                yield {**row, "image_path": str(path.resolve())}
            else:
                pending[row["source_id"], row["frame_id"]] = row
        if not pending:
            return
        # Sequential decode guarantees original frame index even for VFR/B-frames.
        for sid, path in self.context["source_paths"].items():
            wanted = {fid: r for (s, fid), r in pending.items() if s == sid}
            if not wanted:
                continue
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                stream.codec_context.thread_count = 4
                for fid, decoded in enumerate(container.decode(video=0)):
                    if fid in wanted:
                        row = wanted.pop(fid)
                        if decoded.pts != row["pts"]:
                            raise ValueError("Source frame PTS changed")
                        yield self.save(row, decoded.to_image().convert("RGB"))
                    if not wanted:
                        break
            if wanted:
                raise ValueError("Requested frame missing from source")

def pixel_hash(image):
    return sha256(np.asarray(image.convert("RGB")).tobytes()).hexdigest()

def crop_box(image, box):
    w, h = image.size
    pixels = [max(0, min(w if i % 2 == 0 else h,
                        int(v * (w if i % 2 == 0 else h)))) for i, v in enumerate(box)]
    if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
        raise ValueError("Empty crop")
    return image.crop(pixels), pixels
