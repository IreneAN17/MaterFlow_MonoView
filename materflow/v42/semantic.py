"""Task-based Qwen: independent scenes and explicitly ordered local reviews."""
from pathlib import Path
from collections import deque, defaultdict, Counter
from dataclasses import asdict
import json
import time
import importlib.metadata
from ..schema import read_json,write_json,stable_id,file_hash
from ..perception.relations import scene_response_schema,parse_relation_output,decode_model_json
from .io import internal,records,FrameStore
from .scan import scan_rows

class PendingWork(RuntimeError):
    pending_work=True

def joint_schema(frame_ids):
    return {"type":"object","properties":{
        "response":scene_response_schema(),
        "state_spans":{"type":"array","items":{"type":"object","properties":{
            "object":{"type":"string"},"state":{"type":"string"},
            "start_frame_id":{"enum":frame_ids},"end_frame_id":{"enum":frame_ids},
            "continuous":{"type":"boolean"}},"required":["object","state","start_frame_id","end_frame_id","continuous"],"additionalProperties":False}},
        "action":{"anyOf":[{"type":"string","enum":["stir","transfer","hold","filter"]},{"type":"null"}]},
        "start_visible":{"type":"boolean"},"end_visible":{"type":"boolean"},
        "start_frame_id":{"anyOf":[{"type":"integer","enum":frame_ids},{"type":"null"}]},
        "end_frame_id":{"anyOf":[{"type":"integer","enum":frame_ids},{"type":"null"}]},
        "detail":{"type":"string"}},
        "required":["response","state_spans","action","start_visible","end_visible","start_frame_id","end_frame_id","detail"],
        "additionalProperties":False}

def plan_tasks(context):
    c=context["config"];folder=internal(context)
    display_changes=set();last_readings={}
    for item in records(folder/"ocr_results.jsonl"):
        if item["kind"]!="display_candidate" or not item["parsed_readouts"]:
            continue
        key=item["track_id"]
        value=tuple((x["value"],x["unit"]) for x in item["parsed_readouts"])
        if value!=last_readings.get(key):
            display_changes.add((item["frame"]["source_id"],item["frame"]["frame_id"]))
        last_readings[key]=value
    tasks=[];last={};seen=set();oldshot=None;history=deque(maxlen=3)
    run_active=False;streak=0;last_joint=-1e9;pending=[];joint_index=0
    for graph in scan_rows(context):
        f=graph["frame"];sid,t=f["source_id"],f["source_time_s"];shot=graph["shot_id"]
        if shot!=oldshot:
            history.clear();run_active=False;streak=0;pending=[];seen=set()
        for job in pending:
            if job["shot_id"]==shot and f["frame_id"]>job["anchor"]["frame_id"]:
                job["after"].append(f)
        done=[job for job in pending if len(job["after"])>=2]
        for job in done:
            frames=[job["before"],job["anchor"],job["after"][-1]][-c["max_clip_images"]:]
            tasks.append({"kind":"joint","frames":frames,"frame":job["anchor"],
                          "shot_id":shot,"selection_reasons":["local_motion_boundary"],
                          "candidates":job["candidates"]})
            pending.remove(job)
        active=graph["local_motion"]>=c["local_motion_threshold"]
        next_streak=(streak+1 if active==run_active else 1)
        boundary=(active!=run_active and streak>=3)
        if c["joint_review"] and boundary and len(history)>=2 and t-last_joint>=c["joint_review_min_gap_s"]:
            pending.append({"anchor":f,"before":history[0],"after":[],"shot_id":shot,
                            "candidates":graph["display"]})
            last_joint=t
        run_active,streak=active,next_streak
        fresh=[d for d in graph["display"] if d["object_class"] not in ("hand","bottle")
               and d["support_points"]>=2 and d["track_id"] not in seen]
        reasons=[]
        if shot!=oldshot:
            reasons.append("shot_start")
        if t-last.get(sid,-1e9)>=c["scene_interval_s"]:
            reasons.append("periodic_sanity")
        if active and t-last.get(sid,-1e9)>=c["active_scene_interval_s"]:
            reasons.append("local_activity")
        if fresh:
            reasons.append("new_stable_non_bottle_track")
        if (sid,f["frame_id"]) in display_changes:
            reasons.append("instrument_reading_change")
        if reasons and t-last.get(sid,-1e9)>=c["semantic_cooldown_s"]:
            tasks.append({"kind":"scene","frame":f,"frames":[f],"shot_id":shot,
                          "selection_reasons":reasons,"candidates":graph["display"]})
            last[sid]=t
            seen.update(d["track_id"] for d in graph["display"])
        history.append(f);oldshot=shot
    # Near an excerpt boundary, a complete 3-frame window may not exist.
    # A 2-frame review is allowed; absence of boundary evidence remains unknown.
    for job in pending:
        frames=[job["before"],job["anchor"],*job["after"]]
        if len(frames)>=2:
            tasks.append({"kind":"joint","frames":frames[:c["max_clip_images"]],
                          "frame":job["anchor"],"shot_id":job["shot_id"],
                          "selection_reasons":["local_motion_boundary_at_excerpt_tail"],
                          "candidates":job["candidates"]})
    return sorted(tasks,key=lambda r:(r["frame"]["source_id"],r["frame"]["source_time_s"],r["kind"]))

def prompt_for(job,context):
    base=context["config"]["prompt_template_path"].read_text(encoding="utf-8-sig")
    base=base.replace("{context_json}",json.dumps(context["visual_context"],ensure_ascii=False,separators=(",",":")))
    candidates=[{"class":d["object_class"],"box":list(map(lambda x:round(x,3),d["box_xyxy"]))}
                for d in job["candidates"] if d["object_class"]!="hand"]
    base+="\n定位候选（可能误检，不是事实）："+json.dumps(candidates,ensure_ascii=False,separators=(",",":"))
    if job["kind"]=="joint":
        ids=[f["frame_id"] for f in job["frames"]]
        base+=("\n这是按时间顺序的同一局部片段，原帧号："+json.dumps(ids)+
               "。response只描述中间锚点帧"+str(job["frame"]["frame_id"])+
               "的五数组事实，可用相邻图判断实际运动。顶层action为可见动作或null；"+
               "start_visible/end_visible只在局部图确实显示动作开始/结束时为true，"+
               "对应start_frame_id/end_frame_id填上述原帧号，否则null。不能把首末图片自动当启停。"+
               "state_spans只写有多帧可见证据的状态范围，object引用response中的对象，start_frame_id/end_frame_id取原帧号，continuous仅在展示的连续片段内状态持续可见才为true，无证据用[]。detail简短说明。只输出约定顶层JSON，不输出事件编号或谱系。")
    return base

def validate_response(raw,job,context):
    if job["kind"]=="scene":
        return {"response":parse_relation_output(raw,context)}
    body=decode_model_json(raw)
    required={"response","state_spans","action","start_visible","end_visible","start_frame_id","end_frame_id","detail"}
    if not isinstance(body,dict) or set(body)!=required:
        raise ValueError("Ordered review top-level schema invalid")
    ids=[f["frame_id"] for f in job["frames"]]
    if body["action"] not in {*context["actions"],None}:
        raise ValueError("Unknown reviewed action")
    boundary_notes=[]
    for side in ("start","end"):
        flag=body[side+"_visible"];fid=body[side+"_frame_id"]
        if not isinstance(flag,bool) or (flag and fid not in ids):
            raise ValueError("Boundary flag/frame mismatch")
        if not flag and fid is not None:
            # A false flag asserts no boundary. Ignore its unused coordinate; keep original raw JSON.
            boundary_notes.append(side+" coordinate ignored because boundary was explicitly not visible")
            body[side+"_frame_id"]=None
    if body["start_visible"] and body["end_visible"] and body["start_frame_id"]>body["end_frame_id"]:
        raise ValueError("Reversed boundary evidence")
    body["response"]=parse_relation_output(json.dumps(body["response"],ensure_ascii=False),context)
    locals_={o["local_id"] for o in body["response"]["objects"]}
    if not isinstance(body["state_spans"],list):
        raise ValueError("Invalid state spans")
    for span in body["state_spans"]:
        if span.get("object") not in locals_ or span.get("start_frame_id") not in ids or span.get("end_frame_id") not in ids or span["end_frame_id"]<span["start_frame_id"] or not isinstance(span.get("continuous"),bool):
            raise ValueError("Invalid ordered state span")
    body["boundary_validation_notes"]=boundary_notes
    return body

def request_key(job,prompt,context):
    c=context["config"]
    parameters={k:c.get(k) for k in ("model_name","model_revision","quantization","bnb_skip_modules",
        "max_image_pixels","max_new_tokens","attention_backend","seed","do_sample","temperature",
        "top_p","top_k","presence_penalty","repetition_penalty","structured_output")}
    parameters["transformers"]=importlib.metadata.version("transformers")
    dependencies={str(p):file_hash(p) for p in (c["repo_root"]/"materflow/backends/transformers_backend.py",
                        c["repo_root"]/"materflow/perception/relations.py")}
    schema=scene_response_schema() if job["kind"]=="scene" else joint_schema([f["frame_id"] for f in job["frames"]])
    return stable_id("Q42",job["kind"],[file_hash(f["image_path"]) for f in job["frames"]],
                     prompt,parameters,dependencies,schema)

def run_semantic(context,progress):
    c=context["config"];folder=internal(context);start=time.perf_counter()
    jobs=plan_tasks(context)
    frame_rows={(f["source_id"],f["frame_id"]):f for job in jobs for f in job["frames"]}
    materialized={(f["source_id"],f["frame_id"]):f for f in FrameStore(context).fetch(frame_rows.values())}
    cache=folder/"cache/semantic";cache.mkdir(parents=True,exist_ok=True)
    from .tasks import TaskLedger
    ledger=TaskLedger(folder/"tasks.sqlite")
    failures=[]
    results=[];pending=[];artifacts=[]
    for job in jobs:
        job["frames"]=[materialized[f["source_id"],f["frame_id"]] for f in job["frames"]]
        job["frame"]=materialized[job["frame"]["source_id"],job["frame"]["frame_id"]]
        prompt=prompt_for(job,context)
        key=request_key(job,prompt,context)
        job.update(request_id=key,prompt=prompt)
        path=cache/(key+".json")
        if path.exists():
            try:
                item=read_json(path)
                if item["request_id"]==key and item["digest"]==stable_id("",item["raw"]):
                    parsed=validate_response(item["raw"],job,context)
                    results.append({**job,**parsed,"raw":item["raw"],"cached":True,
                                    "model":item.get("model"),"attempts":item.get("attempts",[])})
                    ledger.set(key,"succeeded",output_ref=str(path))
                    continue
            except (ValueError,KeyError,OSError):
                pass
        ledger.set(key,"pending",input_ref=str(job["frame"]["image_path"]))
        pending.append(job)
    write_json(folder/"semantic_plan.json",jobs)
    metrics={"planned_scene_tasks":sum(j["kind"]=="scene" for j in jobs),
             "planned_ordered_reviews":sum(j["kind"]=="joint" for j in jobs),
             "cache_hits":len(results),"image_requests":0,"ordered_requests":0,
             "batch_calls":0,"retry_calls":0,"model_load_s":0.,"batches":[],
             "pending_tasks":0,"task_reasons":dict(Counter(r for j in jobs for r in j["selection_reasons"]))}
    backend=None
    budget=c.get("semantic_budget")
    allowed=pending if budget is None else pending[:int(budget)]
    deferred=pending[len(allowed):]
    if allowed:
        # Walk nested nullable branches before loading GPU weights. LMFE rejects mixed-type enums.
        from lmformatenforcer import JsonSchemaParser
        for task in allowed:
            if task["kind"]=="joint":
                probe={"response":{k:[] for k in ("objects","relations","texts","flows","states")},"state_spans":[],"action":None,"start_visible":False,"end_visible":False,"start_frame_id":None,"end_frame_id":None,"detail":""}
                parser=JsonSchemaParser(joint_schema([f["frame_id"] for f in task["frames"]]))
                for character in json.dumps(probe,separators=(",",":")):
                    parser=parser.add_character(character)
                if not parser.can_end():
                    raise ValueError("Ordered schema failed preflight")
        from ..backends.factory import create_backend
        t=time.perf_counter();backend=create_backend(c)
        metrics["model_load_s"]=time.perf_counter()-t;metrics["model"]=backend.version
    def commit(job,raw,attempts):
        parsed=validate_response(raw,job,context)
        item={"request_id":job["request_id"],"raw":raw,"digest":stable_id("",raw),
              "model":backend.version,"attempts":attempts}
        path=cache/(job["request_id"]+".json");write_json(path,item)
        results.append({**job,**parsed,"raw":raw,"cached":False,"model":backend.version,"attempts":attempts})
        artifacts.append(path)
        ledger.set(job["request_id"],"succeeded",output_ref=str(path))
    # Independent scene requests can batch; ordered reviews are one multimodal request each.
    index=0
    while index<len(allowed):
        job=allowed[index]
        batch=[job]
        if job["kind"]=="scene":
            while index+len(batch)<len(allowed) and len(batch)<c["semantic_batch_size"] and allowed[index+len(batch)]["kind"]=="scene":
                batch.append(allowed[index+len(batch)])
        t=time.perf_counter()
        if job["kind"]=="scene":
            raws=backend.infer_images([j["frame"]["image_path"] for j in batch],
                 [j["prompt"] for j in batch],response_schema=scene_response_schema())
            metrics["image_requests"]+=len(batch)
        else:
            raws=[backend.infer_clip([f["image_path"] for f in job["frames"]],
                   [f["source_time_s"] for f in job["frames"]],job["prompt"],
                   response_schema=joint_schema([f["frame_id"] for f in job["frames"]]))]
            metrics["ordered_requests"]+=1
        metrics["batch_calls"]+=1
        metrics["batches"].append({"kind":job["kind"],"requests":len(batch),
            "elapsed_s":time.perf_counter()-t,"backend_metrics":getattr(backend,"last_inference",None)})
        for j,raw in zip(batch,raws,strict=True):
            attempts=[]
            ledger.set(j["request_id"],"running",input_ref=j["frame"]["image_path"])
            for attempt in range(c["semantic_max_attempts"]):
                rawpath=folder/"evidence/semantic"/(j["request_id"]+"_"+str(attempt)+"_"+str(time.time_ns())+".json")
                rawpath.parent.mkdir(parents=True,exist_ok=True)
                try:
                    validate_response(raw,j,context)
                    write_json(rawpath,{"raw":raw,"valid":True,"request_id":j["request_id"]})
                    attempts.append(str(rawpath));artifacts.append(rawpath)
                    commit(j,raw,attempts)
                    break
                except (ValueError,TypeError,KeyError) as e:
                    write_json(rawpath,{"raw":raw,"valid":False,"error":str(e),"request_id":j["request_id"]})
                    attempts.append(str(rawpath));artifacts.append(rawpath)
                    if attempt+1==c["semantic_max_attempts"]:
                        failures.append({"request_id":j["request_id"],"error":str(e),"raw_ref":str(rawpath)})
                        ledger.set(j["request_id"],"failed_permanent",attempt=attempt+1,error=str(e),output_ref=str(rawpath))
                        break
                    retry=j["prompt"]+"\n上次输出引用/结构不合法："+str(e)+"。重新检查可见事实，不补充或重复对象。"
                    if j["kind"]=="scene":
                        raw=backend.infer_images([j["frame"]["image_path"]],retry,response_schema=scene_response_schema())[0]
                    else:
                        raw=backend.infer_clip([f["image_path"] for f in j["frames"]],
                            [f["source_time_s"] for f in j["frames"]],retry,
                            response_schema=joint_schema([f["frame_id"] for f in j["frames"]]))
                    metrics["retry_calls"]+=1
        index+=len(batch)
        write_json(folder/"semantic_metrics.json",{**metrics,"validated_tasks":len(results),"elapsed_s":time.perf_counter()-start})
        progress.update(f"semantic: {len(results)}/{len(jobs)} validated tasks; real batches {metrics['batch_calls']}")
    results.sort(key=lambda r:(r["frame"]["source_id"],r["frame"]["source_time_s"],r["kind"]))
    write_json(folder/"semantic_results.json",results)
    metrics["pending_tasks"]=len(deferred)
    metrics["validated_tasks"]=len(results)
    metrics["failed_tasks"]=len(failures)
    metrics["elapsed_s"]=time.perf_counter()-start
    write_json(folder/"semantic_metrics.json",metrics)
    write_json(folder/"semantic_deferred.json",deferred)
    write_json(folder/"semantic_failures.json",failures)
    ledger.close()
    if failures:
        raise PendingWork(str(len(failures))+" semantic tasks failed; independent successes committed; retry same stage")
    if deferred:
        raise PendingWork("Semantic budget left "+str(len(deferred))+" tasks; results preserved; raise budget and resume")
    artifacts += [folder/p for p in ("semantic_failures.json","semantic_plan.json","semantic_results.json","semantic_metrics.json","semantic_deferred.json")]
    artifacts += [Path(f["image_path"]) for j in jobs for f in j["frames"]]
    return list(dict.fromkeys(artifacts))
