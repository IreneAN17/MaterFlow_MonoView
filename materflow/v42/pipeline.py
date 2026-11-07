"""Independent, disk-backed V4.2 passes. Raw recognition precedes business decisions."""
from pathlib import Path
import importlib
import os
import sys
import time
from ..config import load_config
from ..schema import read_json, write_json, file_hash
from .context import load_context
from .runtime import STAGES, UPSTREAM, Progress, dependencies, descendants, verified, utc, process_info, gpu_sample
from . import VERSION

FUNCTIONS = {
    "prepare": ("clock", "prepare"), "scan": ("scan", "scan"),
    "ocr": ("ocr", "run_ocr"), "identify": ("catalog", "identify"),
    "semantic": ("semantic", "run_semantic"), "aggregate": ("aggregate", "aggregate"),
    "derive": ("derive", "derive"), "export": ("export", "export_run"),
}

def ancestors(stage):
    result = set()
    for name in UPSTREAM[stage]:
        result.add(name)
        result.update(ancestors(name))
    return result

def process_run(config_path, stage="all", force=False):
    from .power import keep_awake
    with keep_awake() as power:
        print("Execution-state request:",power,flush=True)
        return _process_run(config_path,stage,force)

def _process_run(config_path, stage="all", force=False):
    if stage not in (*STAGES, "all"):
        raise ValueError("V4.2 stages: " + ", ".join(STAGES))
    context = load_context(load_config(config_path))
    c = context["config"];out=c["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    path=out/"run_summary.json"
    summary=read_json(path) if path.is_file() else {
        "schema_version":VERSION,"run_id":c["run_id"],"stages":{},
        "human_review_complete":False,"full_video_run":not bool(c.get("ranges_s"))}
    if summary["run_id"]!=c["run_id"] or summary["schema_version"]!=VERSION:
        raise ValueError("Output directory belongs to another run")
    summary["config"]=c
    ledger_path=out/"internal/id_map.json"
    if summary.get("id_allocations"):
        if not ledger_path.is_file():
            raise ValueError("Persistent ID ledger missing; restore it before resuming")
        current_maps=read_json(ledger_path)["maps"]
        for kind,mapping in summary["id_allocations"].items():
            if any(current_maps.get(kind,{}).get(uid)!=public for uid,public in mapping.items()):
                raise ValueError("Existing public ID allocations changed; restore original mappings")
    summary["inputs"]={k:{"path":str(c[k]),"sha256":file_hash(c[k])}
                       for k in ("registry_path","manifest_path")}
    if stage!="all":
        for name in STAGES:
            if name not in ancestors(stage):
                continue
            old=summary["stages"].get(name,{})
            if old.get("signature")!=dependencies(name,context,summary) or not verified(old,out):
                raise ValueError("Missing/stale upstream "+name+"; resume from that pass")
    for name in STAGES if stage=="all" else (stage,):
        key=dependencies(name,context,summary)
        old=summary["stages"].get(name,{})
        if not force and old.get("signature")==key and verified(old,out):
            summary.setdefault("cache_checks",[]).append({"stage":name,"utc":utc(),"signature":key})
            write_json(path,summary)
            print("Cached "+name+": verified artifacts",flush=True)
            continue
        for downstream in descendants(name):
            if downstream in summary["stages"]:
                summary.setdefault("history",[]).append({"stage":downstream,"invalidated_by":name,
                                                       "record":summary["stages"][downstream]})
            summary["stages"].pop(downstream,None)
        context["stage_signature"]=key
        item={"status":"running","signature":key,"pid":os.getpid(),
              "pid_created":process_info(os.getpid())["created"],"started_utc":utc(),
              "progress":"starting","last_progress_utc":utc(),"gpu_start":gpu_sample(),"exit_code":None}
        summary["stages"][name]=item
        write_json(path,summary)
        print("Starting "+name+": "+str(out),flush=True)
        start=time.perf_counter();cpu_start=time.process_time()
        with Progress(path,summary,name) as progress:
            try:
                module,fn=FUNCTIONS[name]
                try:
                    artifacts=getattr(importlib.import_module("materflow.v42."+module),fn)(context,progress)
                finally:
                    if context.get("_ocr_worker"):
                        context["_ocr_worker"].close()
                        context.pop("_ocr_worker",None)
                if ledger_path.is_file():
                    summary["id_allocations"]=read_json(ledger_path)["maps"]
                if dependencies(name,context,summary)!=key:
                    # Every task key freezes its own implementation at initialization.
                    # The pass is never accepted if source dependencies changed in flight.
                    raise RuntimeError("Pass dependencies changed during execution; resume with the stable code")
                digests={str(Path(p).relative_to(out)):file_hash(p) for p in artifacts}
                progress.update(status="complete",progress="all artifacts verified",
                                elapsed_s=time.perf_counter()-start,process_cpu_s=time.process_time()-cpu_start,finished_utc=utc(),
                                gpu_end=gpu_sample(),exit_code=0,artifacts=digests)
                if "torch" in sys.modules and sys.modules["torch"].cuda.is_available():
                    progress.update(peak_allocated_bytes=sys.modules["torch"].cuda.max_memory_allocated(),
                                    peak_reserved_bytes=sys.modules["torch"].cuda.max_memory_reserved())
            except BaseException as e:
                progress.update(status="interrupted" if isinstance(e,KeyboardInterrupt) else "partial" if getattr(e,"pending_work",False) else "failed",
                                elapsed_s=time.perf_counter()-start,process_cpu_s=time.process_time()-cpu_start,finished_utc=utc(),
                                error=type(e).__name__+": "+str(e),exit_code=130 if isinstance(e,KeyboardInterrupt) else 1)
                raise
        print("Completed "+name,flush=True)
    return summary
