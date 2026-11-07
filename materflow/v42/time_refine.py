"""Read missing business-boundary clocks exactly; never overwrite prepare evidence."""
from pathlib import Path
from PIL import Image
from ..schema import read_json,write_json,file_hash,stable_id
from .io import internal,records,FrameStore
from .clock import run_worker,normalize_anchors
from .timeline import Timeline

def refine_missing_times(context):
    c=context["config"];folder=internal(context)
    base=Timeline(context,include_refined=False)
    wanted=set()
    for row in records(folder/"observations.jsonl"):
        for key in ("start_s","end_s"):
            if not base.iso(row["source_id"],row[key]):
                wanted.add((row["source_id"],row[key]))
    derived=read_json(folder/"derived.json")
    for row in derived["events"]:
        for key in ("start_s","end_s"):
            if not base.iso(row["source_id"],row[key]):
                wanted.add((row["source_id"],row[key]))
    inputs={sid:file_hash(path) for sid,path in context["source_paths"].items()}
    key=stable_id("CLOCK42",sorted(wanted),inputs,c["timezone"],c["clock_roi"],
                  file_hash(c["repo_root"]/"runs/v42/ocr_worker.py"),c["clock_score_threshold"])
    manifest=folder/"extra_clock_manifest.json";anchors_path=folder/"extra_clock_anchors.json"
    if manifest.exists() and anchors_path.exists():
        old=read_json(manifest)
        if old.get("key")==key and old.get("anchors_sha256")==file_hash(anchors_path):
            return [manifest,anchors_path,*[Path(p) for p in old.get("evidence_files",[])]]
    frames=[r for r in records(folder/"frame_index.jsonl") if (r["source_id"],r["source_time_s"]) in wanted]
    requests=[];artifacts=[]
    for frame in FrameStore(context).fetch(frames):
        with Image.open(frame["image_path"]) as image:
            w,h=image.size
            roi=c["clock_roi"]
            pixels=[round(v*(w if i%2==0 else h)) for i,v in enumerate(roi)]
            crop=image.crop(pixels)
            path=folder/"evidence/extra_clocks"/(frame["source_id"]+"_"+str(frame["frame_id"])+".png")
            path.parent.mkdir(parents=True,exist_ok=True)
            crop.resize((crop.width*2,crop.height*2)).save(path)
        requests.append({**frame,"crop_path":str(path),"roi_xyxy":roi})
        artifacts+=[path,Path(frame["image_path"])]
    if requests:
        raw_path=folder/"extra_clock_raw.json"
        raw=run_worker(requests,raw_path,context,"clock")
        extra=normalize_anchors(raw["results"],c["timezone"],c["clock_score_threshold"])
        artifacts+=[raw_path,raw_path.with_name("extra_clock_raw_requests.json")]
    else:
        extra=[]
    # Validate extra readings with the neighboring original clock evidence.
    combined=sorted([*base.anchors,*extra],key=lambda r:(r["source_id"],r["source_time_s"]))
    normalized=normalize_anchors(combined,c["timezone"],c["clock_score_threshold"])
    keys={(r["source_id"],r["frame_id"]) for r in extra}
    extra=[r for r in normalized if (r["source_id"],r["frame_id"]) in keys]
    write_json(anchors_path,extra)
    write_json(manifest,{"key":key,"inputs":inputs,"timezone":c["timezone"],
        "requested_boundaries":len(wanted),"exact_ocr_queries":len(requests),
        "supported_boundaries":sum(bool(r["datetime"]) for r in extra),
        "anchors_sha256":file_hash(anchors_path),"evidence_files":[str(p) for p in artifacts],
        "policy":"Exact-point date OCR only; no added continuity or processing-duration claim"})
    return [manifest,anchors_path,*artifacts]
