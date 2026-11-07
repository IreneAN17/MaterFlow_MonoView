from collections import Counter,defaultdict
from copy import deepcopy
import re
from ..schema import stable_id,write_json,write_jsonl,read_json
from ..materials.ocr import extract_lab_observations
from ..materials.events import propose_lab_events
from .runtime import Ids
from .context import core_evidence


def bind_and_aggregate(frames,tracking,backend,context,progress):
    c=context["config"];output=c["output_dir"];ids=Ids(output/"id_map.json",c["run_id"])
    context["evidence_index"]={}
    detector_graphs=[{**g,"captions":[]} for g in tracking["frame_graphs"]]
    raw=extract_lab_observations(frames,{**tracking,"frame_graphs":detector_graphs},None,context)
    semantic_graphs=[g for g in tracking["frame_graphs"] if g["captions"]]
    raw+=extract_lab_observations(frames,{**tracking,"frame_graphs":semantic_graphs},backend,context)
    # Sparse detector mode still has real SAM2 frame evidence, kept distinct from detector/OCR facts.
    from ..materials.ocr import _evidence,_observation
    for graph in tracking["frame_graphs"]:
        frame=graph["frame"];key=f"{frame['source_id']}:{frame['frame_id']}"
        for item in tracking.get("display",{}).get(key,[]):
            if item["evidence_kind"]!="SAM2_propagated":continue
            tid=item["track_id"];request=stable_id("SAM2",tid,frame["source_id"],frame["frame_id"])
            ref=_evidence(frame,request,"segmentation_presence",context,item["box_xyxy"],evidence_kind="video_segmentation",model=c.get("sam2_model"),model_revision=c.get("sam2_revision"))
            payload={"kind":"object","object_class":item["object_class"],"description":item["object_class"],"track_id":tid,"classification_status":"supported","model_source":"SAM2_video"}
            raw.append(_observation(frame,"visual_state",item["object_class"],payload,[ref],tid))

    tracks={t["track_id"]:t for t in tracking["tracks"]};involved=set();labelled=set()
    for o in raw:
        p=o["payload"]
        if o["signal_type"]=="material_flow":involved.update(p.get(k) for k in ("source_track_id","target_track_id"))
        if p.get("kind")=="relation":involved.update(p.get(k) for k in ("subject_track_id","object_track_id"))
        if o["signal_type"]=="ocr_text":
            if p.get("kind")=="display":involved.add(o.get("track_id"))
            if p.get("kind")=="label":labelled.add(o.get("track_id"))
        if p.get("kind")=="state" and p.get("state") not in ("stationary","visible"):involved.add(o.get("track_id"))
    identities={};rejected=[]
    for tid,t in sorted(tracks.items(),key=lambda item:(item[1]["history"][0]["source_id"],item[1]["history"][0]["source_time_s"],item[1]["history"][0]["box_xyxy"][0],item[0])):
        category=t["object_class"];policy=context["classes"].get(category,{}).get("id_policy")
        history=t["history"];box=history[0]["box_xyxy"];foreground=.08<(box[0]+box[2])/2<.92 and .08<(box[1]+box[3])/2<.96
        relevant=tid in involved or (tid in labelled and len(history)>=2) or (c.get("relevance_policy","interaction_readout_foreground")=="interaction_readout_foreground" and foreground and len(history)>=3)
        oid=ids.instance(category,tid) if policy=="auto_instance" and relevant else None
        identities[tid]={"track_id":tid,"object_id":oid,"object_class":category,"instrument_class":category if policy=="class_only" else None,"relevant":relevant,"shot_id":t["shot_id"],"field_status":"supported" if oid else "unresolved"}
        if not relevant:rejected.append({"track_id":tid,"category":category,"reason":"background_or_insufficient_persistence","first_evidence":history[0]})
    context["identities"]=identities
    context["objects"]={v["object_id"]:{"object_id":v["object_id"],"object_class":v["object_class"]} for v in identities.values() if v["object_id"]}
    shots=read_json(output/"shots.json")
    def public(track):return "camera_wearer" if track=="camera_wearer" else identities.get(track,{}).get("object_id") or "unknown"
    processed=[];filtered=[]
    for original in raw:
        o=deepcopy(original);p=o["payload"];tid=o.get("track_id")
        if p.get("kind")=="relation" and tid=="camera_wearer":tid=p.get("object_track_id");o["track_id"]=tid
        identity=identities.get(tid,{})
        category=identity.get("object_class") or p.get("object_class")
        policy=context["classes"].get(category,{}).get("id_policy")
        o.update(uid=o["observation_id"],object_id=identity.get("object_id"),object_class=category if policy=="auto_instance" else None,instrument_class=category if policy=="class_only" else None)
        evidence=[context["evidence_index"][r] for r in o["evidence_refs"]]
        frame_id=evidence[0]["frame_id"];o["shot_id"]=shots.get(f"{o['source_id']}:{frame_id}");o["visual_evidence_refs"]=list(o["evidence_refs"])
        o["evidence_refs"]=[];o.pop("instrument_id",None)
        for side in ("source","target","subject","object"):
            if side+"_track_id" in p:p[side+"_object_id"]=identities.get(p[side+"_track_id"],{}).get("object_id")
        if o["signal_type"]=="material_flow":o["observed_value"]=public(p.get("source_track_id"))+" -> "+public(p.get("target_track_id"))
        if p.get("kind")=="relation":o["observed_value"]=public(p.get("subject_track_id"))+" "+str(p.get("relation") or p.get("relation_raw"))+" "+public(p.get("object_track_id"))
        if p.get("kind")=="object" and tid not in identities:
            filtered.append({"uid":o["uid"],"reason":"unlocalized_visibility_candidate"});continue
        if p.get("kind")=="object" and tid in identities and not identity["relevant"]:filtered.append({"uid":o["uid"],"reason":"irrelevant_object"});continue
        if p.get("kind")=="object" and category not in context["classes"]:o["notes"]="Unknown category; raw description preserved."
        supported=["observed_value"] if p.get("classification_status")!="unresolved" else []
        if o["signal_type"]=="material_flow" and not (p.get("arrival_visible") is True and p.get("source_object_id") and p.get("target_object_id") and p["source_object_id"]!=p["target_object_id"]):
            supported=[];o["notes"]="Candidate flow; arrival or carrier correspondence unresolved."
        if policy=="class_only" and p.get("kind") in ("label","unknown"):
            o["instrument_class"]=None
        if o["object_class"]:supported.append("object_class")
        if o["object_id"]:supported.append("object_id")
        if o["instrument_class"]:supported.append("instrument_class")
        na=[k for k in ("object_class","object_id") if policy=="class_only"]
        if policy=="auto_instance" or (policy=="class_only" and p.get("kind") in ("label","unknown")):na.append("instrument_class")
        core_evidence(o,"Observations",supported=supported,not_applicable=na)
        processed.append(o)
        # Complete balance readouts remain useful even when sample attribution is unknown.
        if o["signal_type"]=="ocr_text" and p.get("kind")=="display" and p.get("value") is not None and p.get("reading_kind") not in ("setpoint","unknown"):
            signal=None;unit=p.get("unit")
            if category=="balance" and unit=="g":signal="mass_g"
            if category=="temperature probe" and unit in ("°C","C","℃") and p.get("reading_kind")=="measured":
                if any(x["payload"].get("state")=="probe_measuring_sample" and x.get("track_id")==tid and x["start_s"]==o["start_s"] for x in raw):signal="temperature_c"
            if category=="stirrer" and str(unit).lower()=="rpm" and p.get("reading_kind")=="measured":
                if any(x["payload"].get("state")=="actual_speed_feedback" and x.get("track_id")==tid and x["start_s"]==o["start_s"] for x in raw):signal="stirring_speed_rpm"
            if signal:
                n=deepcopy(o);n.update(uid=stable_id("O",o["uid"],signal),observation_id=stable_id("O",o["uid"],signal),signal_type=signal,observed_value=p["value"],unit=unit,object_class=None,object_id=None,notes="Displayed value; sample attribution/net quantity unresolved.")
                n["payload"]["transcription_uid"]=o["uid"]
                core_evidence(n,"Observations",supported=["observed_value","instrument_class"]);processed.append(n)
    # The fine-grained records include all detections and rejected objects, never silently delete original evidence.
    write_jsonl(output/"raw_observations.jsonl",raw);write_jsonl(output/"normalized_observations.jsonl",processed)
    groups=[];last_by_key={};mapping={}
    for o in sorted(processed,key=lambda r:(r["source_id"],r["start_s"],r["uid"])):
        p=o["payload"];kind=p.get("kind")
        value=o["observed_value"] if kind!="object" else (o.get("object_class") or o.get("instrument_class") or o["observed_value"])
        # Unknown frame-local identities must not collapse distinct objects or displays.
        key=(o["source_id"],o["shot_id"],o.get("track_id"),o["signal_type"],kind,str(value),o.get("unit"))
        old=last_by_key.get(key);gap=o["start_s"]-old["end_s"] if old else None
        if kind in ("object","relation"):maximum=1/c["sample_fps"]+.05
        elif o["signal_type"]=="ocr_text" and kind=="label":
            maximum=0
            if old and o.get("track_id") in tracks:
                t=tracks[o["track_id"]];points=sorted({h["source_time_s"] for h in t["history"]+t.get("predicted_history",[]) if old["end_s"]-1e-6<=h["source_time_s"]<=o["start_s"]+1e-6})
                continuous=bool(points and points[0]<=old["end_s"]+1e-6 and points[-1]>=o["start_s"]-1e-6 and all(b-a<=1/c["sample_fps"]+.05 for a,b in zip(points,points[1:])))
                if continuous:maximum=float("inf")
        else:maximum=1/c["sample_fps"]+.05
        merge=old is not None and 0<=gap<=maximum and o.get("track_id") in identities
        if merge:
            old["end_s"]=o["end_s"];old["member_uids"].append(o["uid"]);old["visual_evidence_refs"]=sorted(set(old["visual_evidence_refs"]+o["visual_evidence_refs"]))
        else:
            old=deepcopy(o);old["member_uids"]=[o["uid"]];old["observation_id"]=ids.get("O",o["uid"]);groups.append(old);last_by_key[key]=old
        mapping[o["uid"]]=old["observation_id"]
    for o in groups:
        transcription=o["payload"].get("transcription_uid")
        if transcription and transcription in mapping:o["evidence_refs"]=[mapping[transcription]]
        for e in o["field_evidence"].values():e["evidence_refs"]=[o["observation_id"]]
    previous_mapping=read_json(output/"aggregation_map.json") if (output/"aggregation_map.json").is_file() else {}
    redirects={previous_mapping[uid]:public for uid,public in mapping.items() if uid in previous_mapping and previous_mapping[uid]!=public}
    ids.reconcile("O",[o["uid"] for o in groups],redirects)
    for category in context["classes"]:
        kind=category.replace(" ","_")
        if context["classes"][category]["id_policy"]=="auto_instance":ids.reconcile(kind,[tid for tid,i in identities.items() if i.get("object_id", "") and i["object_id"].startswith(kind+"_")])
    write_json(output/"aggregation_map.json",mapping);write_json(output/"identities.json",identities)
    write_json(output/"visual_evidence_index.json",context["evidence_index"])
    write_json(output/"filter_report.json",{"raw_count":len(raw),"normalized_count":len(processed),"business_count":len(groups),"rejected_tracks":rejected,"filtered_records":filtered})
    write_jsonl(output/"observations.jsonl",groups)
    progress.update(f"observations: {len(raw)} raw -> {len(groups)} business records")
    return groups


def derive_events(observations,raw,context,ids,mapping):
    # Rules consume only normalized observations; no model/pixels/SOP.
    candidates=propose_lab_events(raw,context);by_id={o["uid"]:o for o in raw};merged=[]
    for e in sorted(candidates,key=lambda r:(r["source_id"],r["observed_start_s"] or 0,r["action"])):
        if e["action"] not in context["actions"]:continue
        shot=by_id[e["evidence_refs"][0]]["shot_id"]
        previous=next((p for p in reversed(merged) if p["source_id"]==e["source_id"] and p["shot_id"]==shot and all(p.get(k)==e.get(k) for k in ("action","source_track_id","target_track_id"))),None)
        # Merge only overlapping or directly adjacent evidence; sparse distant positives stay separate candidates.
        touching=previous and e["observed_start_s"] is not None and previous["observed_end_s"] is not None and 0<=e["observed_start_s"]-previous["observed_end_s"]<=1/context["config"]["sample_fps"]+.05
        if touching:
            previous["evidence_refs"]=sorted(set(previous["evidence_refs"]+e["evidence_refs"]));previous["observed_end_s"]=max(previous["observed_end_s"],e["observed_end_s"])
            for part in e["output_parts"]:
                if part not in previous["output_parts"]:previous["output_parts"].append(part)
            continue
        e["shot_id"]=shot;merged.append(e)
    for e in merged:
        uid=stable_id("E",context["config"]["run_id"],e["action"],sorted(e["evidence_refs"]))
        e.update(uid=uid,event_id=ids.get("E",uid),start_s=e["observed_start_s"],end_s=e["observed_end_s"],interval_status="unresolved",notes="Visible evidence span; actual operation boundaries/continuous duration unresolved.")
        e["evidence_refs"]=sorted({mapping[r] for r in e["evidence_refs"]});e.pop("sop_step_candidate",None)
        if e["action"]=="filter" and e["output_parts"]:
            primary=next((p for p in e["output_parts"] if p["part"]=="retentate"),e["output_parts"][0])
            e["target_track_id"]=primary["track_id"];e["target_container_id"]=context["identities"].get(primary["track_id"],{}).get("object_id")
        support=["action"]+[k for k in ("source_container_id","target_container_id") if e.get(k)]
        core_evidence(e,"Events",e["evidence_refs"],support,not_applicable=["source_container_id"] if e["action"] in ("stir","hold") else [])
    ids.reconcile("E",[e["uid"] for e in merged])
    return merged


def material_lineage(events,context,ids,tracking):
    materials=[];edges=[];current={};last={};bytrack={t["track_id"]:t for t in tracking["tracks"]}
    def previous(carrier,track,event):
        prior=last.get(carrier)
        if prior is None or prior["shot_id"]!=event["shot_id"]:return None
        history=sorted(h["source_time_s"] for h in bytrack.get(track,{}).get("history",[]) if prior["end_s"]<=h["source_time_s"]<=event["start_s"])
        if not history or history[0]>prior["end_s"]+.25 or history[-1]<event["start_s"]-.25:return None
        if any(b-a>1/context["config"]["sample_fps"]+.05 for a,b in zip(history,history[1:])):return None
        return current.get(carrier)
    def node(prefix,carrier,event,seed):
        uid=stable_id(prefix,context["config"]["run_id"],seed,carrier);item={"uid":uid,"material_id":ids.get(prefix,uid),"material_type":"source_material" if prefix=="M" else "sample_state","container_id":carrier,"created_by_event_id":None if prefix=="M" else event["event_id"],"provenance_status":"unresolved","notes":"Physical provenance only; initial contents/composition unresolved."}
        core_evidence(item,"Materials",event["evidence_refs"] if prefix=="M" else [event["event_id"]],supported=["container_id"] if carrier else []);materials.append(item);return item
    def edge(before,after,event,relation,part=None):
        uid=stable_id("L",before["material_id"],after["material_id"],event["event_id"],relation,part)
        edges.append({"uid":uid,"edge_id":ids.get("L",uid),"source_material_id":before["material_id"],"target_material_id":after["material_id"],"relation":relation,"event_id":event["event_id"],"output_part":part,"edge_status":"supported" if before["container_id"] and after["container_id"] else "unresolved","notes":"Direct observed relation; complete upstream provenance remains separately unresolved."})
    for e in events:
        source=e.get("source_container_id");target=e.get("target_container_id");action=e["action"]
        if action=="transfer":
            if not source or not target:continue
            before=previous(source,e.get("source_track_id"),e) or node("M",source,e,[e["event_id"],"source"])
            old_target=previous(target,e.get("target_track_id"),e)
            after=node("S",target,e,e["event_id"]);edge(before,after,e,"transferred_to")
            if old_target:edge(old_target,after,e,"contributed_to")
            current[source]=before;last[source]=e;current[target]=after;last[target]=e
        elif action in ("stir","hold","filter"):
            carrier=source if action=="filter" else target;track=e.get("source_track_id" if action=="filter" else "target_track_id")
            if not carrier:continue
            # Unbounded stir/hold supports an action candidate but not a confirmed completed processing state.
            if action in ("stir","hold") and e["interval_status"]!="supported":continue
            before=previous(carrier,track,e) or node("M",carrier,e,[e["event_id"],"source"])
            for part in e.get("output_parts",[]) if action=="filter" else [{"part":None,"track_id":track}]:
                out=context["identities"].get(part["track_id"],{}).get("object_id")
                if out:
                    after=node("S",out,e,[e["event_id"],part["part"],out]);edge(before,after,e,"processed_into",part["part"]);current[out]=after;last[out]=e
    ids.reconcile("M",[m["uid"] for m in materials if m["material_type"]=="source_material"])
    ids.reconcile("S",[m["uid"] for m in materials if m["material_type"]=="sample_state"])
    ids.reconcile("L",[e["uid"] for e in edges])
    return materials,edges
