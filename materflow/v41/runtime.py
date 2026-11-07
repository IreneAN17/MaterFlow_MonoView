import os,time,threading,contextlib
from datetime import datetime,timezone
from pathlib import Path
from ..schema import read_json,write_json,file_hash,stable_id,json_default

STAGES=("prepare","caption","ground","consolidate","derive")
# Dependency lists are explicit: presentation edits never invalidate model inference.
CODE={"prepare":["context.py","sampling.py","clock.py"],"caption":[],"ground":["vision.py"],"consolidate":["vision.py","domain.py","runtime.py"],"derive":["domain.py","export.py","video.py","clock.py","time_formats.py","runtime.py"]}
KEYS={"prepare":["sample_fps","ranges_s","caption_interval_s","caption_active_fps","motion_threshold","cut_threshold","clock_roi","timezone"],
"caption":["model_name","model_revision","max_image_pixels","max_new_tokens","quantization","caption_batch_size","do_sample","allow_batch_fallback","device","offload_embeddings","temperature","top_p","top_k","presence_penalty","repetition_penalty","seed","structured_output","attention_backend","bnb_skip_modules","torch_num_threads"],
"ground":["grounding_model","grounding_revision","box_threshold","text_threshold","detection_fps","work_roi","class_thresholds","conflict_iou","conflict_margin"],
"consolidate":["sam2_model","sam2_revision","dinov2_model","dinov2_revision","track_chunk_frames","max_track_gap_s","appearance_threshold","roi_reread","relevance_policy"],
"derive":["event_gap_s","annotated_video","video_crf","video_preset"]}

def utc():return datetime.now(timezone.utc).isoformat()

def signature(stage,context,summary):
    c=context["config"]; package=Path(__file__).parent
    values={k:c.get(k) for k in KEYS[stage]}
    code={name:file_hash(package/name) for name in CODE[stage]}
    code["shared_records"]=file_hash(package.parent/"schema.py")
    if stage in ("ground","consolidate"):code["box_geometry"]=file_hash(package.parent/"perception/grounding.py")
    if stage=="consolidate":code["tracking_model_wrapper"]=file_hash(package.parent/"tracking/objects.py");code["raw_observation_extractor"]=file_hash(package.parent/"materials/ocr.py")
    if stage=="derive":code["event_rules"]=file_hash(package.parent/"materials/events.py")
    if stage in ("caption","ground","consolidate"):
        import importlib.metadata
        for dependency in ("torch","transformers","bitsandbytes"):
            try: values[dependency]=importlib.metadata.version(dependency)
            except importlib.metadata.PackageNotFoundError: values[dependency]=None
    if stage=="prepare":
        code["clock_engine"]=file_hash(c["repo_root"]/"runs/v41/rapid_clock.py")
        values.update(registry=file_hash(c["registry_path"]),manifest=file_hash(c["manifest_path"]),sources={sid:file_hash(p) for sid,p in context["source_paths"].items()})
    else:
        previous=summary["stages"].get(STAGES[STAGES.index(stage)-1],{})
        values["upstream"]={k:v for k,v in previous.get("artifacts",{}).items()}
        if stage=="caption":
            values["upstream"]={p:h for p,h in previous.get("artifacts",{}).items() if p in ("frame_index.jsonl","caption_schedule.json","context.json") or p.startswith("frames/") or p.startswith("frames\\")}
            values["prompt"]=file_hash(c["prompt_template_path"])
            code.update({str(p):file_hash(p) for p in (package.parent/"backends").glob("*.py")})
            code["parser"]=file_hash(package.parent/"perception/relations.py")
    return stable_id("stage",stage,values,code)

def verified(item,output):
    return item.get("status")=="complete" and all((output/p).is_file() and file_hash(output/p)==digest for p,digest in item.get("artifacts",{}).items())

class Progress:
    def __init__(self,path,summary,stage):self.path,self.summary,self.stage=path,summary,stage;self.stop=threading.Event();self.lock=threading.Lock()
    def update(self,message=None,**values):
        with self.lock:
            item=self.summary["stages"][self.stage];item.update(values,last_heartbeat_utc=utc())
            if message is not None:item.update(progress=message,last_progress_utc=utc());print(message,flush=True)
            write_json(self.path,self.summary)
    def __enter__(self):
        def pulse():
            while not self.stop.wait(5):self.update()
        self.worker=threading.Thread(target=pulse,daemon=True);self.worker.start();return self
    def __exit__(self,*args):self.stop.set();self.worker.join()

class Ids:
    def __init__(self,path,run_id):
        self.path=Path(path);self.data=read_json(path) if self.path.is_file() else {"run_id":run_id,"maps":{},"retired":{}}
        if self.data["run_id"]!=run_id:raise ValueError("ID ledger run mismatch")
        for kind,mapping in self.data["maps"].items():
            if len(set(mapping.values()))!=len(mapping):raise ValueError("Duplicate public IDs in ledger")
            import re
            pattern=re.escape(kind)+(r"[0-9]{3,}" if kind in ("O","E","M","S","L") else r"_[1-9][0-9]*")
            if any(re.fullmatch(pattern,value) is None for value in mapping.values()):raise ValueError("Malformed public ID ledger")
    def get(self,kind,uid):
        mapping=self.data["maps"].setdefault(kind,{})
        if uid not in mapping:
            maximum=max([int(v.rsplit("_",1)[-1]) if "_" in v else int(v[len(kind):]) for v in mapping.values()] or [0])
            mapping[uid]=f"{kind}_{maximum+1}" if kind not in ("O","E","M","S","L") else f"{kind}{maximum+1:03d}"
            self.save()
        public=mapping[uid]
        self.data.setdefault("retired",{}).setdefault(kind,{}).pop(uid,None)
        self.data.setdefault("redirects",{}).pop(public,None)
        return public
    def instance(self,category,uid):
        instances=self.data.setdefault("instances",{})
        if uid not in instances:
            existing=[public for kind,mapping in self.data["maps"].items() if kind not in ("O","E","M","S","L") for key,public in mapping.items() if key==uid]
            if len(set(existing))>1:raise ValueError("Conflicting physical aliases in ID ledger")
            public=existing[0] if existing else self.get(category.replace(" ","_"),uid)
            instances[uid]={"public_id":public,"initial_class":category,"current_class":category,"classification_history":[category]}
        item=instances[uid]
        if category!=item["current_class"]:
            item["current_class"]=category;item["classification_history"].append(category)
        self.save()
        return item["public_id"]
    def reconcile(self,kind,active_uids,redirects=None):
        active=set(active_uids);retired=self.data["retired"].setdefault(kind,{})
        for uid,public in self.data["maps"].get(kind,{}).items():
            if uid not in active:retired[uid]={"public_id":public,"reason":"not_in_current_projection"}
            else:retired.pop(uid,None)
        if redirects:self.data.setdefault("redirects",{}).update(redirects)
        self.save()
    def save(self):write_json(self.path,self.data)


def process_info(pid):
    """Read-only liveness and creation identity; never use os.kill(pid, 0) on Windows."""
    import ctypes
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return {"alive":False,"created":None}
    handle=ctypes.c_void_p(handle)
    try:
        code=ctypes.c_ulong();values=[ctypes.c_ulonglong() for _ in range(4)]
        alive=bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
        ok=kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in values])
        return {"alive":alive,"created":str(values[0].value) if ok else None}
    finally:kernel.CloseHandle(handle)


def gpu_sample():
    import subprocess
    try:
        result=subprocess.run(["nvidia-smi","--query-gpu=name,pstate,clocks.sm,power.draw,memory.used,utilization.gpu","--format=csv,noheader"],capture_output=True,text=True,timeout=5)
        return {"utc":utc(),"sample":result.stdout.strip(),"exit_code":result.returncode}
    except (OSError,subprocess.TimeoutExpired) as error:return {"utc":utc(),"unavailable":str(error)}
