"""Selected original-resolution OCR regions, cached independently of catalogs."""
from pathlib import Path
from collections import defaultdict
import re
import time
import unicodedata
import hashlib
import numpy as np
from PIL import Image
from ..schema import read_json,write_json,stable_id,file_hash
from .io import internal,FrameStore,crop_box,records,jsonl_writer
from .scan import scan_rows
from .clock import run_worker

READING = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(mg|kg|g|mL|ml|L|rpm|°C|℃|C)$",re.I)

def parse_readout(text):
    normalized=unicodedata.normalize("NFKC",text).strip().replace("−","-")
    match=READING.fullmatch(normalized)
    if not match:
        return None
    value=float(match.group(1));unit=match.group(2)
    units={"g":"g","mg":"mg","kg":"kg","ml":"mL","l":"L","rpm":"rpm","°c":"°C","c":"°C","℃":"°C"}
    return {"value":value,"unit":units[unit.casefold()],"reading_kind":"display",
            "normalization":"Unicode NFKC, Unicode minus and unit case only"}

def select_regions(context):
    c=context["config"]
    selected=[];last={};counts=defaultdict(int);best={}
    for graph in scan_rows(context):
        frame=graph["frame"];now=frame["source_time_s"]
        for d in graph["detections"]:
            category=d["object_class"]
            if category=="hand":
                continue
            tid=d["track_id"]
            is_instrument=context["classes"][category]["id_policy"]=="class_only"
            kind="display_candidate" if is_instrument else "label_candidate"
            box=d["box_xyxy"];area=(box[2]-box[0])*(box[3]-box[1])
            gap=c["ocr_display_interval_s"] if is_instrument else c["ocr_retry_interval_s"]
            if now-last.get(tid,-1e9)<gap:
                continue
            if not is_instrument and counts[tid]>=c["ocr_label_views"]:
                continue
            if not is_instrument and area<best.get(tid,0)*.8:
                continue
            selected.append({"frame":frame,"track_id":tid,"object_class":category,
                             "kind":kind,"box_xyxy":box})
            counts[tid]+=1;last[tid]=now;best[tid]=max(best.get(tid,0),area)
    return selected

def run_ocr(context,progress):
    c=context["config"];folder=internal(context);start=time.perf_counter()
    choices=select_regions(context);store=FrameStore(context)
    frames={(r["frame"]["source_id"],r["frame"]["frame_id"]):r["frame"] for r in choices}
    materialized={(r["source_id"],r["frame_id"]):r for r in store.fetch(frames.values())}
    cache=folder/"cache/ocr";cache.mkdir(parents=True,exist_ok=True)
    cropdir=folder/"evidence/ocr";cropdir.mkdir(parents=True,exist_ok=True)
    worker_hash=file_hash(c["repo_root"]/"runs/v42/ocr_worker.py")
    ready=[];pending=[];hits=0;artifacts=[];duplicate_crops=0;seen={};last_display={}
    for choice in choices:
        frame=materialized[choice["frame"]["source_id"],choice["frame"]["frame_id"]]
        with Image.open(frame["image_path"]) as image:
            crop,pixels=crop_box(image,choice["box_xyxy"])
            thumb=np.asarray(crop.resize((32,32)).convert("L"),dtype=float)
            old=seen.get(choice["track_id"])
            if choice["kind"]=="label_candidate" and old is not None and float(np.mean(np.abs(thumb-old)))<2.:
                duplicate_crops+=1
                continue
            seen[choice["track_id"]]=thumb
            digest=hashlib.sha256(np.asarray(crop).tobytes()).hexdigest()
            key=stable_id("OCR",digest,worker_hash,"regions","RapidOCR-1.4.4")
            crop_path=cropdir/(key+".png")
            crop.save(crop_path)  # Deterministic evidence reconstruction; no existence-only image cache.
        entry={**choice,"frame":frame,"request_id":stable_id("OCRQ",frame,choice["track_id"],key),
               "cache_key":key,"crop_path":str(crop_path),"crop_xyxy_px":pixels,
               "original_size":[frame["width"],frame["height"]]}
        if choice["kind"]=="display_candidate":
            prior=last_display.get(choice["track_id"])
            if prior is None:
                score=1.
            else:
                delta=np.abs(thumb-prior)/255.
                score=max(float(delta[y:y+8,x:x+8].mean()) for y in range(0,32,8) for x in range(0,32,8))
            entry.update(display_change_score=score,display_change_trigger=score>=c["display_change_threshold"])
            last_display[choice["track_id"]]=thumb
        path=cache/(key+".json")
        cached=None
        if path.exists():
            try:
                test=read_json(path)
                if test.get("key")==key and test.get("digest")==stable_id("",test.get("result")):
                    cached=test["result"]
            except (ValueError,OSError):
                pass
        if cached is not None:
            hits+=1;ready.append({**entry,**cached})
        else:
            pending.append(entry)
        artifacts.append(crop_path)
    worker_models=None
    if ready:
        worker_models=ready[0].get("worker_model")
    for start_index in range(0,len(pending),c["ocr_batch_size"]):
        batch=pending[start_index:start_index+c["ocr_batch_size"]]
        batch_path=folder/"ocr_batches"/(str(start_index).zfill(6)+".json")
        batch_path.parent.mkdir(parents=True,exist_ok=True)
        raw=run_worker(batch,batch_path,context,"regions")
        worker_models={k:raw[k] for k in ("engine","versions","model_sha256","scores_are_calibrated_probabilities")}
        for item in raw["results"]:
            result={k:item[k] for k in ("raw_text","model_score","blocks","elapsed")}
            result["worker_model"]=worker_models
            write_json(cache/(item["cache_key"]+".json"),{"key":item["cache_key"],
                       "result":result,"digest":stable_id("",result)})
            ready.append({**item,**result})
        progress.update(f"ocr: {min(start_index+c['ocr_batch_size'],len(pending))}/{len(pending)} selected regions")
        artifacts += [batch_path,batch_path.with_name(batch_path.stem+"_requests.json")]
    ready.sort(key=lambda r:(r["frame"]["source_id"],r["frame"]["source_time_s"],r["request_id"]))
    for entry in ready:
        readings=[]
        for i,block in enumerate(entry["blocks"]):
            parsed=parse_readout(block["raw_text"])
            if parsed and block["model_score"]>=c["ocr_score_threshold"]:
                readings.append({**parsed,"raw_text":block["raw_text"],"block_index":i,
                                 "model_score":block["model_score"]})
        entry["parsed_readouts"]=readings
        entry["sample_attribution"]="unresolved"
    with jsonl_writer(folder/"ocr_results.jsonl") as append:
        for item in ready:
            append(item)
    write_json(folder/"ocr_metrics.json",{
        "selected_regions":len(choices),"unique_crop_queries":len(ready),"worker_queries":len(pending),
        "cache_hits":hits,"duplicate_label_crops_avoided":duplicate_crops,
        "elapsed_s":time.perf_counter()-start,"worker":worker_models,
        "display_change_triggers":sum(bool(r.get("display_change_trigger")) for r in ready),
        "catalog_used_as_ocr_input":False,"numeric_readouts":sum(len(x["parsed_readouts"]) for x in ready)})
    return [folder/"ocr_results.jsonl",folder/"ocr_metrics.json"]+artifacts
