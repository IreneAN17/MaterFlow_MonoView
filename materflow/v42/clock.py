"""Source clock evidence with explicit quantization and no global extrapolation."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image
import numpy as np
import subprocess
import sys
import time
from ..schema import read_json, write_json, stable_id
from ..v41.time_formats import complete_clock
from .io import source_frames, jsonl_writer, internal
from ..v41.sampling import in_ranges

def normalize_anchors(rows, timezone, threshold=.85):
    result = []
    for row in rows:
        parsed, method = complete_clock(row.get("raw_text", ""))
        value = None
        if parsed and row.get("model_score", 0) >= threshold:
            dt = datetime.fromisoformat(parsed).replace(tzinfo=ZoneInfo(timezone))
            value = dt.isoformat()
        result.append({**row, "datetime": value, "parse_method": method,
                       "precision_s": 1, "timezone": timezone})
    # Do not bridge an isolated inconsistent OCR clock.
    for i in range(1, len(result)-1):
        a, b, d = result[i-1:i+2]
        if len({r["source_id"] for r in (a, b, d)}) != 1:
            continue
        if all(r["datetime"] for r in (a, b, d)):
            dt = lambda r: datetime.fromisoformat(r["datetime"])
            expected = d["source_time_s"] - a["source_time_s"]
            left = b["source_time_s"] - a["source_time_s"]
            if abs((dt(d)-dt(a)).total_seconds()-expected) <= 1.1 and abs((dt(b)-dt(a)).total_seconds()-left) > 1.1:
                b["datetime"] = None
                b["parse_method"] = "isolated_inconsistency"
    return result

def time_at(t, sid, anchors, shots, max_gap=1.3):
    if t is None:
        return {"datetime": None, "status": "unresolved", "reason": "missing_source_time"}
    rows = sorted((a for a in anchors if a["source_id"] == sid and a.get("datetime")),
                  key=lambda a: a["source_time_s"])
    for a in rows:
        if abs(t-a["source_time_s"]) < 1e-6:
            return {"datetime": a["datetime"], "status": "supported", "precision_s": 1,
                    "method": "clock_ocr", "anchor_frames": [a["frame_id"]]}
    for a, b in zip(rows, rows[1:]):
        ta, tb = a["source_time_s"], b["source_time_s"]
        if not ta < t < tb or tb-ta > max_gap:
            continue
        if shots.get(f"{sid}:{a['frame_id']}") != shots.get(f"{sid}:{b['frame_id']}"):
            continue
        da, db = datetime.fromisoformat(a["datetime"]), datetime.fromisoformat(b["datetime"])
        if abs((db-da).total_seconds()-(tb-ta)) > .1:
            continue
        # Keep the uncertainty of the seconds-only watermark, never invent ms.
        value = da + timedelta(seconds=int(t-ta))
        return {"datetime": value.isoformat(), "status": "supported", "precision_s": 1,
                "method": "locally_bracketed_clock", "anchor_frames": [a["frame_id"], b["frame_id"]]}
    return {"datetime": None, "status": "unresolved", "reason": "no_consistent_bracket"}

def display_time(value):
    return datetime.fromisoformat(value).strftime("%Y/%m/%d-%H:%M:%S") if value else None

def run_worker(requests, path, context, mode):
    c = context["config"]
    script = c["repo_root"] / "runs/v42/ocr_worker.py"
    request_file = path.with_name(path.stem + "_requests.json")
    write_json(request_file, requests)
    if not c["ocr_python"].is_file():
        raise FileNotFoundError("Isolated OCR Python missing; use runs/v41/setup_ocr.ps1")
    from .ocr_service import client
    worker=client(context);started=time.perf_counter()
    results=list(worker.iter_results(requests,mode))
    report={**worker.manifest,'results':results,'elapsed_s':time.perf_counter()-started,
            'worker_starts':worker.starts,'initialization_count':worker.starts,'startup_s':worker.startup_s}
    write_json(path,report)
    return report


def prepare(context, progress):
    c = context["config"]
    folder = internal(context)
    cropdir = folder / "evidence/clocks"
    cropdir.mkdir(parents=True, exist_ok=True)
    requests, motions, shot_map = [], {}, {}
    previous, last_clock, last_t, shot = {}, {}, {}, {}
    start = time.perf_counter()
    count = 0
    with jsonl_writer(folder / "frame_index.jsonl") as append:
        for frame, image in source_frames(context):
            sid, t = frame["source_id"], frame["source_time_s"]
            thumb = np.asarray(image.resize((96, 54)), dtype=np.float32) / 255
            diff = float(np.abs(thumb-previous[sid]).mean()) if sid in previous else 1.
            if sid not in previous or t-last_t[sid] > 1.5/c["sample_fps"] or diff > c.get("cut_threshold", .38):
                shot[sid] = shot.get(sid, 0)+1
            key = f"{sid}:{frame['frame_id']}"
            shot_map[key] = f"{sid}:{shot[sid]}"
            motions[key] = diff
            append(frame)
            previous[sid], last_t[sid] = thumb, t
            if t-last_clock.get(sid, -1e9) >= c["clock_interval_s"]-1e-6:
                w, h = image.size
                pixels = [int(v*(w if i%2==0 else h)) for i,v in enumerate(c["clock_roi"])]
                crop = image.crop(pixels)
                path = cropdir / (sid+"_"+str(frame["frame_id"])+".png")
                crop.resize((crop.width*2, crop.height*2)).save(path)
                requests.append({**frame, "crop_path": str(path), "roi_xyxy": c["clock_roi"]})
                last_clock[sid] = t
            count += 1
            if count % 50 == 0:
                progress.update(f"prepare: indexed {count} samples; full frames are not persisted")
    if not count:
        raise ValueError("Empty selected range")
    raw = run_worker(requests, folder/"clock_raw.json", context, "clock")
    anchors = normalize_anchors(raw["results"], c["timezone"], c["clock_score_threshold"])
    segments=[]
    for sid in context["sources"]:
        valid = [r for r in anchors if r["source_id"] == sid and r["datetime"]]
        jumps = []
        for a, b in zip(valid, valid[1:]):
            delta = (datetime.fromisoformat(b["datetime"])-datetime.fromisoformat(a["datetime"])).total_seconds()
            if abs(delta-(b["source_time_s"]-a["source_time_s"])) > 1.1:
                jumps.append(b["frame_id"])
                segments.append({"source_id":sid,"boundary_frame_id":b["frame_id"],"previous_anchor":a,"next_anchor":b})
        for key in shot_map:
            s, fid = key.rsplit(":", 1)
            if s == sid:
                from bisect import bisect_right
                shot_map[key] += ":epoch"+str(bisect_right(jumps,int(fid)))
    write_json(folder/"clock_segments.json", {"format_version":2,"boundaries":segments})
    write_json(folder/"clock_anchors.json", anchors)
    write_json(folder/"shots.json", shot_map)
    write_json(folder/"motion_scores.json", motions)
    write_json(folder/"prepare_metrics.json", {"sampled_frames": count, "clock_requests": len(requests),
               "parsed_anchors": sum(bool(a["datetime"]) for a in anchors), "elapsed_s": time.perf_counter()-start,
               "full_source_frames_saved": 0, "human_reference_used": False})
    progress.update(f"prepare: {count} samples, {len(anchors)} source-clock anchors")
    return [folder/p for p in ("clock_segments.json", "frame_index.jsonl", "clock_raw.json", "clock_raw_requests.json",
            "clock_anchors.json", "shots.json", "motion_scores.json", "prepare_metrics.json")] + list(cropdir.glob("*.png"))
