from dataclasses import asdict
from pathlib import Path
import gc,os,sys,time,traceback
from ..config import load_config
from ..schema import FrameRecord,read_json,read_jsonl,write_json,write_jsonl,file_hash
from .context import load_context,VERSION
from .runtime import STAGES,Progress,signature,verified,utc,Ids,process_info,gpu_sample

def measured_backend(config,metrics,progress):
    from ..backends.factory import create_backend
    start=time.perf_counter();backend=create_backend(config);metrics["load_s"]=time.perf_counter()-start;metrics["model"]=backend.version;metrics["batch_forwards"]=0;metrics["batch_elapsed_s"]=[]
    original=backend._infer_batch
    def wrapped(*args,**kwargs):
        start=time.perf_counter();metrics["batch_forwards"]+=1
        try:
            result=original(*args,**kwargs)
            metrics.setdefault("batches",[]).append({**getattr(backend,"last_inference",{}),"decoded_response_tokens":[len(backend.processor.tokenizer.encode(text,add_special_tokens=False)) for text in result]})
            return result
        finally:
            metrics["batch_elapsed_s"].append(time.perf_counter()-start);progress.update(f"Qwen batch {metrics['batch_forwards']} finished; input batch={len(args[0]) if args else '?'}")
    backend._infer_batch=wrapped
    return backend

def release():
    gc.collect()
    if "torch" in sys.modules and sys.modules["torch"].cuda.is_available():sys.modules["torch"].cuda.empty_cache()

def ensure_frozen_dependencies(stage,context,summary,expected):
    """Reject source edits during inference and preserve suspect cache separately."""
    if signature(stage,context,summary)==expected:return
    output=context["config"]["output_dir"].resolve()
    name={"ground":"ground","consolidate":"features"}.get(stage)
    if name:
        cache=(output/"cache"/name).resolve()
        target=(output/"diagnostics"/("changed_dependencies_"+stage+"_"+str(time.time_ns()))).resolve()
        if not cache.is_relative_to(output) or not target.is_relative_to(output):raise ValueError("Cache quarantine must stay inside the run output")
        if cache.exists():target.parent.mkdir(parents=True,exist_ok=True);cache.rename(target)
    raise RuntimeError("Stage code/config dependencies changed while running; suspect visual cache preserved in diagnostics; resume this stage")


def process_run(config_path,stage="all",force=False):
    config=load_config(config_path);context=load_context(config);output=config["output_dir"];output.mkdir(parents=True,exist_ok=True);summary_path=output/"run_summary.json"
    summary=read_json(summary_path) if summary_path.is_file() else {"schema_version":VERSION,"run_id":config["run_id"],"stages":{},"human_review_complete":False,"inputs":{}}
    if summary["run_id"]!=config["run_id"] or summary.get("schema_version")!=VERSION:raise ValueError("Output folder belongs to a different run/schema")
    previous_ids=summary.get("id_allocations",{})
    if previous_ids:
        ledger=Ids(output/"id_map.json",config["run_id"])
        for kind,mapping in previous_ids.items():
            if any(ledger.data["maps"].get(kind,{}).get(uid)!=public for uid,public in mapping.items()):raise ValueError("Stable ID ledger changed/missing; restore its original allocations before resuming")
    summary["config"]=config
    summary["inputs"]={name:{"path":str(config[name]),"sha256":file_hash(config[name])} for name in ("registry_path","manifest_path")}
    selected=STAGES if stage=="all" else (stage,)
    if stage not in (*STAGES,"all"):raise ValueError(stage)
    if stage!="all":
        for upstream in STAGES[:STAGES.index(stage)]:
            item=summary["stages"].get(upstream,{})
            if item.get("signature")!=signature(upstream,context,summary) or not verified(item,output):raise ValueError(f"Missing/stale {upstream}; resume from that stage")
    result=None
    for current in selected:
        key=signature(current,context,summary);old=summary["stages"].get(current,{})
        if not force and old.get("signature")==key and verified(old,output):
            summary.setdefault("cache_checks",[]).append({"stage":current,"verified_utc":utc(),"signature":key})
            write_json(summary_path,summary);print(f"Cached {current}: verified artifacts",flush=True);continue
        for name in STAGES[STAGES.index(current):]:summary["stages"].pop(name,None)
        item={"status":"running","signature":key,"pid":os.getpid(),"pid_created":process_info(os.getpid())["created"],"gpu_start":gpu_sample(),"started_utc":utc(),"last_progress_utc":utc(),"progress":"starting","exit_code":None}
        summary["stages"][current]=item;write_json(summary_path,summary);start=time.perf_counter();context["run_fingerprint"]=key
        print(f"Starting {current}: {output}",flush=True)
        with Progress(summary_path,summary,current) as progress:
            try:
                if current=="prepare":
                    from .sampling import prepare
                    artifacts=prepare(context,progress)
                else:
                    frames=[FrameRecord(**r) for r in read_jsonl(output/"frame_index.jsonl")];context["frames"]=frames;artifacts=[]
                    if current=="caption":
                        from ..perception.relations import extract_frame_relations
                        metrics={};backend=measured_backend(config,metrics,progress)
                        context["model_tag"]=config["model_name"].replace("/","_");context["windows"]=[]
                        schedule=read_json(output/"caption_schedule.json");chosen=[FrameRecord(**{k:v for k,v in r.items() if k!="selection_reason"}) for r in schedule]
                        (output/"prompts").mkdir(exist_ok=True);(output/"prompts/lab_scene.txt").write_text(context["scene_prompt"],encoding="utf-8")
                        for sid in context["sources"]:extract_frame_relations([f for f in chosen if f.source_id==sid],backend,context)
                        metrics["scheduled_frames"]=len(chosen);metrics["source_frames"]=len(frames)
                        if config["device"].startswith("cuda"):
                            import torch
                            metrics["peak_allocated_bytes"]=torch.cuda.max_memory_allocated();metrics["peak_reserved_bytes"]=torch.cuda.max_memory_reserved()
                        write_json(output/"caption_metrics.json",metrics);artifacts=list(context.get("caption_artifacts",[]))+[output/"caption_metrics.json",output/"prompts/lab_scene.txt"]
                        del backend;release()
                    elif current=="ground":
                        from .vision import ground
                        captions=[]
                        for sid in context["sources"]:captions.extend(read_json(output/"captions"/config["model_name"].replace("/","_")/(sid+".json")))
                        graphs=ground(frames,captions,context,progress);write_json(output/"frame_graphs.json",graphs)
                        artifacts=[output/p for p in ("frame_graphs.json","ground_metrics.json","ground_filter.json")];release()
                    elif current=="consolidate":
                        from .vision import track
                        from .domain import bind_and_aggregate
                        graphs=read_json(output/"frame_graphs.json");tracking=track(graphs,context,progress);write_json(output/"tracking.json",tracking);release()
                        roi_metrics={}
                        class Lazy:
                            backend=None
                            def infer_images(self,images,prompt,**kwargs):
                                if self.backend is None:self.backend=measured_backend(config,roi_metrics,progress)
                                return self.backend.infer_images(images,prompt,**kwargs)
                        lazy=Lazy();bind_and_aggregate(frames,tracking,lazy if config["roi_reread"] else None,context,progress)
                        if lazy.backend is not None:del lazy.backend
                        release();write_json(output/"roi_metrics.json",roi_metrics)
                        artifacts=[output/p for p in ("tracking.json","tracking_metrics.json","raw_observations.jsonl","normalized_observations.jsonl","observations.jsonl","identities.json","aggregation_map.json","visual_evidence_index.json","filter_report.json","roi_metrics.json")]+context.get("crop_artifacts",[])
                    elif current=="derive":
                        from .domain import derive_events,material_lineage
                        from .export import export,time_report
                        from .video import render
                        context["identities"]=read_json(output/"identities.json");context["evidence_index"]=read_json(output/"visual_evidence_index.json")
                        context["objects"]={i["object_id"]:{"object_id":i["object_id"],"object_class":i["object_class"]} for i in context["identities"].values() if i.get("object_id")}
                        observations=read_jsonl(output/"observations.jsonl");normalized=read_jsonl(output/"normalized_observations.jsonl");ids=Ids(output/"id_map.json",config["run_id"]);mapping=read_json(output/"aggregation_map.json");tracking=read_json(output/"tracking.json")
                        events=derive_events(observations,normalized,context,ids,mapping);materials,lineage=material_lineage(events,context,ids,tracking)
                        result={"observations":observations,"events":events,"materials":materials,"lineage":lineage}
                        artifacts=export(result,context)+time_report(result,context)
                        if config.get("annotated_video",True):artifacts+=render(result,tracking,frames,context,progress)
                        write_json(output/"record_counts.json",{k:len(v) for k,v in result.items()});artifacts.append(output/"record_counts.json")
                ensure_frozen_dependencies(current,context,summary,key)
                if (output/"id_map.json").is_file():summary["id_allocations"]=read_json(output/"id_map.json")["maps"]
                progress.update("hashing completed artifacts")
                artifacts={str(Path(p).relative_to(output)):file_hash(p) for p in artifacts}
                progress.update(status="complete",finished_utc=utc(),elapsed_s=time.perf_counter()-start,exit_code=0,artifacts=artifacts,gpu_end=gpu_sample())
            except BaseException as error:
                interrupted=isinstance(error,KeyboardInterrupt)
                progress.update(status="interrupted" if interrupted else "failed",error=f"{type(error).__name__}: {error}",finished_utc=utc(),elapsed_s=time.perf_counter()-start,exit_code=130 if interrupted else 1)
                raise
        print(f"Completed {current}",flush=True)
    return result or summary
