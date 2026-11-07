"""Evidence binding, three-layer admission and typed interval aggregation."""
from pathlib import Path
from collections import defaultdict, Counter
from copy import deepcopy
import json
import sqlite3
import os
from ..schema import read_json,write_json,stable_id
from ..v41.runtime import Ids
from ..v41.context import core_evidence
from ..perception.grounding import box_iou
from .io import internal,records,jsonl_writer
from .scan import scan_rows
from .intervals import aggregate_points,continuous_presence

def physical_flow_classes(payload,classes):
    return all(classes.get(payload.get(side+"_class"),{}).get("id_policy")=="auto_instance"
               for side in ("source","target"))

def bind_objects(response,graph):
    shown=graph["display"];mapping={};used=set()
    for obj in response["objects"]:
        options=[d for d in shown if d["object_class"]==obj["object_class"] and d["track_id"] not in used]
        hint=obj.get("region_hint_xyxy");chosen=None
        if hint and options:
            ranked=sorted(((box_iou(hint,d["box_xyxy"]),d) for d in options),key=lambda x:-x[0])
            if ranked[0][0]>.2 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>.12):
                chosen=ranked[0][1]
        elif len(options)==1 and sum(x["object_class"]==obj["object_class"] for x in response["objects"])==1:
            chosen=options[0]
        mapping[obj["local_id"]]=chosen["track_id"] if chosen else stable_id("UT",graph["frame"]["source_id"],graph["frame"]["frame_id"],obj["local_id"])
        if chosen:
            used.add(chosen["track_id"])
    return mapping

def aggregate(context,progress):
    c=context["config"];folder=internal(context)
    dbpath=folder/("aggregation_"+str(os.getpid())+".sqlite")
    connection=sqlite3.connect(dbpath)
    connection.executescript("""
        CREATE TABLE samples(sid TEXT,fid INTEGER,t REAL,shot TEXT,data TEXT,PRIMARY KEY(sid,fid));
        CREATE TABLE presence(tid TEXT,sid TEXT,t REAL,fid INTEGER,shot TEXT,category TEXT,kind TEXT,box TEXT);
        CREATE INDEX presence_track_time ON presence(tid,sid,t);
        CREATE TABLE points(uid TEXT PRIMARY KEY,sid TEXT,t REAL,tid TEXT,kind TEXT,signal TEXT,data TEXT);
        CREATE INDEX points_track_time ON points(tid,sid,t);
        CREATE TABLE evidence(uid TEXT PRIMARY KEY,data TEXT);
    """)
    summaries={};raw_count=0
    def point(frame,shot,tid,category,signal,value,payload,request,box=None,extra=None):
        nonlocal raw_count
        uid=stable_id("P",frame["source_id"],frame["frame_id"],request,signal,tid,payload)
        eid=stable_id("V",uid,box)
        evidence={"visual_evidence_id":eid,"source_id":frame["source_id"],"frame_id":frame["frame_id"],
            "source_frame_index":frame["source_frame_index"],"source_time_s":frame["source_time_s"],
            "pts":frame.get("pts"),"time_base":frame.get("time_base"),"image_path":frame.get("image_path") or None,
            "request_id":request,"box_xyxy":box,**(extra or {})}
        connection.execute("INSERT OR IGNORE INTO evidence VALUES(?,?)",(eid,json.dumps(evidence,ensure_ascii=False)))
        row={"uid":uid,"observation_id":uid,"source_id":frame["source_id"],"start_s":frame["source_time_s"],
            "end_s":frame["source_time_s"],"track_id":tid,"object_class":category,
            "object_id":None,"instrument_class":None,"signal_type":signal,"observed_value":value,
            "unit":payload.get("unit"),"value_basis":"direct","payload":payload,"shot_id":shot,
            "evidence_refs":[],"visual_evidence_refs":[eid],"notes":None}
        connection.execute("INSERT OR IGNORE INTO points VALUES(?,?,?,?,?,?,?)",
                           (uid,row["source_id"],row["start_s"],tid,payload.get("kind"),signal,json.dumps(row,ensure_ascii=False)))
        raw_count+=1
        return row
    for graph in scan_rows(context):
        frame=graph["frame"];shot=graph["shot_id"]
        connection.execute("INSERT INTO samples VALUES(?,?,?,?,?)",
                           (frame["source_id"],frame["frame_id"],frame["source_time_s"],shot,json.dumps(graph,ensure_ascii=False)))
        for d in graph["display"]:
            tid,category=d["track_id"],d["object_class"]
            if category=="hand":
                continue
            connection.execute("INSERT INTO presence VALUES(?,?,?,?,?,?,?,?)",
                (tid,frame["source_id"],frame["source_time_s"],frame["frame_id"],shot,category,
                 d["evidence_kind"],json.dumps(d["box_xyxy"])))
            summary=summaries.setdefault(tid,{"track_id":tid,"object_class":category,"first_frame":frame,
                       "last_frame":frame,"first_s":frame["source_time_s"],"last_s":frame["source_time_s"],
                       "shot_id":shot,"samples":0})
            summary.update(last_frame=frame,last_s=frame["source_time_s"],object_class=category,samples=summary["samples"]+1)
            payload={"kind":"object","object_class":category,"classification_status":"supported",
                     "track_id":tid,"visual_source":d["evidence_kind"]}
            point(frame,shot,tid,category,"visual_state",category,payload,
                  stable_id("VIS",tid,frame["frame_id"],d["evidence_kind"]),d["box_xyxy"],
                  {"evidence_kind":d["evidence_kind"],"last_detection_s":d["last_detection_s"]})
    raw_by_ocr={}
    for entry in records(folder/"ocr_results.jsonl"):
        frame=entry["frame"]
        sample=connection.execute("SELECT shot FROM samples WHERE sid=? AND fid=?",
                                  (frame["source_id"],frame["frame_id"])).fetchone()
        if not sample:
            raise ValueError("OCR frame not in sampled source index")
        kind="display" if entry["kind"]=="display_candidate" else "label"
        payload={"kind":kind,"raw_text":entry["raw_text"],"object_class":entry["object_class"],
                 "reading_kind":"display" if kind=="display" else "unknown",
                 "unit":None,"value":None,"ocr_request_id":entry["request_id"]}
        o=point(frame,sample[0],entry["track_id"],entry["object_class"],"ocr_text",
                entry["raw_text"],payload,entry["request_id"],entry["box_xyxy"],
                {"crop_path":entry["crop_path"],"crop_xyxy_px":entry["crop_xyxy_px"],
                 "blocks":entry["blocks"],"evidence_kind":"RapidOCR"})
        raw_by_ocr[entry["request_id"]]=o["uid"]
        for reading in entry["parsed_readouts"]:
            # Balance display is valid without identifying a sample.
            # Temperature/speed stay text until actual measurement/feedback semantics are supported.
            if entry["object_class"]!="balance" or reading["unit"]!="g":
                continue
            p={**reading,"kind":"reading","object_class":"balance",
               "display_change_trigger":entry.get("display_change_trigger",False),
               "transcription_uids":[o["uid"]],"ocr_request_id":entry["request_id"]}
            n=point(frame,sample[0],entry["track_id"],"balance","mass_g",reading["value"],p,
                    stable_id("READ",entry["request_id"],reading["block_index"]),entry["box_xyxy"])
            n["notes"]="Displayed mass; net addition and sample attribution unresolved."
            connection.execute("UPDATE points SET data=? WHERE uid=?",(json.dumps(n,ensure_ascii=False),n["uid"]))
    semantic=read_json(folder/"semantic_results.json")
    for entry in semantic:
        frame=entry["frame"]
        row=connection.execute("SELECT data FROM samples WHERE sid=? AND fid=?",
                               (frame["source_id"],frame["frame_id"])).fetchone()
        if row is None:
            raise ValueError("Semantic anchor frame missing")
        graph=json.loads(row[0]);response=entry["response"]
        local=bind_objects(response,graph)
        classes={o["local_id"]:o["object_class"] for o in response["objects"]}
        boxes={o["local_id"]:o.get("region_hint_xyxy") for o in response["objects"]}
        ref=lambda value:"camera_wearer" if value=="camera_wearer" else local.get(value)
        temporal="ordered_images" if entry["kind"]=="joint" else "single_image"
        for obj in response["objects"]:
            point(frame,graph["shot_id"],ref(obj["local_id"]),obj["object_class"],"visual_state",
                  obj["description"],{"kind":"object","description":obj["description"],"object_class":obj["object_class"],
                  "classification_status":obj["classification_status"],"object_class_raw":obj.get("object_class_raw")},
                  entry["request_id"]+":object:"+obj["local_id"],boxes[obj["local_id"]])
        for i,r in enumerate(response["relations"]):
            p={"kind":"relation","relation":r["relation"],"subject_track_id":ref(r["subject"]),
               "object_track_id":ref(r["object"]),"object_class":classes.get(r["object"]),
               "temporal_input":temporal}
            tid=ref(r["object"]) if r["subject"]=="camera_wearer" else ref(r["subject"])
            category=classes.get(r["object"] if r["subject"]=="camera_wearer" else r["subject"])
            point(frame,graph["shot_id"],tid,category,"visual_state",r["relation"],p,
                  entry["request_id"]+":relation:"+str(i),boxes.get(r["object"]))
        for i,flow in enumerate(response["flows"]):
            p={"kind":"flow","source_track_id":ref(flow["source"]),"target_track_id":ref(flow["target"]),
               "source_class":classes.get(flow["source"]),"target_class":classes.get(flow["target"]),
               "arrival_visible":flow["arrival_visible"],"material_form":flow["material_form"],
               "temporal_input":temporal}
            point(frame,graph["shot_id"],p["target_track_id"],classes.get(flow["target"]),
                  "material_flow","visible material flow" if flow["arrival_visible"] else "candidate flow",p,
                  entry["request_id"]+":flow:"+str(i),boxes.get(flow["target"]))
        for i,state in enumerate(response["states"]):
            p={"kind":"state","state":state["state"],"detail":state["detail"],"temporal_input":temporal,
               "object_class":classes.get(state["object"])}
            point(frame,graph["shot_id"],ref(state["object"]),classes.get(state["object"]),
                  "visual_state",state["state"],p,entry["request_id"]+":state:"+str(i),boxes.get(state["object"]))
        for i,span in enumerate(entry.get("state_spans",[])):
            frames={f["frame_id"]:f for f in entry["frames"]}
            first,last=frames[span["start_frame_id"]],frames[span["end_frame_id"]]
            p={"kind":"state","state":span["state"],"detail":"Explicit ordered-image state span",
               "temporal_input":"ordered_images","continuous_claim":span["continuous"],
               "object_class":classes.get(span["object"])}
            o=point(first,graph["shot_id"],ref(span["object"]),classes.get(span["object"]),
                    "visual_state",span["state"],p,entry["request_id"]+":span:"+str(i),boxes.get(span["object"]))
            o["end_s"]=last["source_time_s"]
            selected=sorted(f["source_time_s"] for f in entry["frames"]
                            if first["frame_id"]<=f["frame_id"]<=last["frame_id"])
            dense=all(b-a<=1/c["sample_fps"]+.05 for a,b in zip(selected,selected[1:]))
            o["support_segments"]=[[o["start_s"],o["end_s"]]] if span["continuous"] and dense else [[t,t] for t in selected]
            connection.execute("UPDATE points SET data=? WHERE uid=?",(json.dumps(o,ensure_ascii=False),o["uid"]))
        # Boundary facts remain observations. The event rules, not the model, create events.
        if entry["kind"]=="joint" and entry.get("action") in ("stir","hold"):
            state_names={"stir":("stirring_started","stirring_stopped"),"hold":("sample_placed_in_oven","sample_removed_from_oven")}
            targets={ref(s["object"]) for s in response["states"] if s["state"] in
                     ("stirring_motion","sample_in_oven","sample_placed_in_oven","sample_removed_from_oven")}
            if len(targets)==1:
                tid=next(iter(targets));category=summaries.get(tid,{}).get("object_class")
                for side,name in zip(("start","end"),state_names[entry["action"]]):
                    if entry[side+"_visible"]:
                        f=next(f for f in entry["frames"] if f["frame_id"]==entry[side+"_frame_id"])
                        point(f,graph["shot_id"],tid,category,"visual_state",name,
                              {"kind":"state","state":name,"detail":entry["detail"],"temporal_input":"ordered_images",
                               "joint_request_id":entry["request_id"],"object_class":category},
                              entry["request_id"]+":"+side)
    connection.commit()
    relevant=defaultdict(list);instrument_reads=defaultdict(list)
    for uid,sid,t,tid,kind,signal,data in connection.execute("SELECT * FROM points ORDER BY sid,t,uid"):
        o=json.loads(data);p=o["payload"]
        if signal in ("mass_g","temperature_c","stirring_speed_rpm"):
            relevant[tid].append({"kind":"actual_display_readout","point_uid":uid})
            instrument_reads[tid].append(t)
        elif kind=="relation" and p.get("relation")=="hold" and p.get("subject_track_id")=="camera_wearer":
            relevant[p["object_track_id"]].append({"kind":"explicit_hand_hold","point_uid":uid})
        elif kind=="flow" and p.get("arrival_visible") is True and physical_flow_classes(p,context["classes"]):
            for side in ("source","target"):
                if p.get(side+"_track_id"):
                    relevant[p[side+"_track_id"]].append({"kind":"actual_arrival_flow","point_uid":uid})
        elif kind=="state" and p.get("temporal_input")=="ordered_images" and p.get("state") in (
            "stirring_motion","liquid_draining","sample_in_oven","stirring_started","stirring_stopped",
            "sample_placed_in_oven","sample_removed_from_oven"):
            relevant[tid].append({"kind":"ordered_processing_state","point_uid":uid})
    # Explicit semantic carrier-on-instrument relation + a real contemporaneous readout.
    weigh_candidates=defaultdict(list)
    for (data,) in connection.execute("SELECT data FROM points WHERE kind='relation'"):
        o=json.loads(data);p=o["payload"];instrument=p.get("object_track_id");carrier=p.get("subject_track_id")
        if p.get("relation")=="on" and instrument in instrument_reads and any(abs(t-o["start_s"])<=1. for t in instrument_reads[instrument]):
            weigh_candidates[(instrument,o["source_id"],o["start_s"])].append((carrier,o["uid"]))
    for group in weigh_candidates.values():
        if len({tid for tid,_ in group})==1:
            for tid,uid in group:
                relevant[tid].append({"kind":"explicit_scale_carrier_relation_plus_readout","point_uid":uid})
    matches=read_json(folder/"packaging_matches.json")
    ids=Ids(folder/"id_map.json",c["run_id"])
    identities={}
    ordered=sorted(summaries.values(),key=lambda r:(r["first_frame"]["source_id"],r["first_s"],r["track_id"]))
    for row in ordered:
        tid=row["track_id"];category=row["object_class"]
        allowed=category in context["classes"]
        package=matches.get(tid,{"status":"not_configured" if not c.get("catalog_path") else "insufficient",
                                  "packaging_catalog_id":None,"reason":"No readable matched label"})
        label_pass=category!="bottle" or package["status"]=="matched"
        active=bool(relevant.get(tid))
        admitted=allowed and label_pass and active
        policy=context["classes"].get(category,{}).get("id_policy")
        identities[tid]={**row,"object_id":ids.instance(category,tid) if admitted and policy=="auto_instance" else None,
            "instrument_class":category if policy=="class_only" else None,
            "category_status":"allowed" if allowed else "rejected",
            "packaging_match":package,"relevance_status":"supported" if active else "unresolved",
            "relevance_evidence":relevant.get(tid,[]),"business_admitted":admitted,
            "admission_reason":"all_applicable_gates_passed" if admitted else
                "outside_categories" if not allowed else "bottle_label_not_matched" if not label_pass else "no_supported_experimental_relation"}
    admitted=[];filtered=[]
    with jsonl_writer(folder/"raw_observations.jsonl") as rawwrite:
        for (data,) in connection.execute("SELECT data FROM points ORDER BY sid,t,uid"):
            rawwrite(json.loads(data))
    for (data,) in connection.execute("SELECT data FROM points ORDER BY sid,t,uid"):
        o=json.loads(data);p=o["payload"];tid=o.get("track_id");identity=identities.get(tid,{})
        category=o.get("object_class") or identity.get("object_class")
        kind=p.get("kind")
        def eligible(track,cls=None):
            info=identities.get(track,{})
            if info:
                return info["business_admitted"]
            return bool(track and cls in context["classes"] and cls!="bottle" and relevant.get(track))
        keep=eligible(tid,category)
        if kind=="flow":
            keep=p.get("arrival_visible") is True and physical_flow_classes(p,context["classes"]) and eligible(p.get("source_track_id"),p.get("source_class")) and eligible(p.get("target_track_id"),p.get("target_class"))
        elif kind=="relation":
            subject=p.get("subject_track_id");target=p.get("object_track_id")
            keep=eligible(target,p.get("object_class")) if subject=="camera_wearer" else eligible(subject,category) and eligible(target)
        elif kind=="object" and tid not in identities:
            keep=False
        if category not in context["classes"]:
            keep=False
        rejection=identity.get("admission_reason","unbound_or_unknown_candidate")
        if kind in ("label","display") and not str(o["observed_value"]).strip():
            keep=False
            rejection="unreadable_empty_ocr"
        if kind=="flow" and not physical_flow_classes(p,context["classes"]):
            rejection="flow_endpoint_is_not_an_admitted_carrier_class"
        if not keep:
            filtered.append({"uid":o["uid"],"track_id":tid,
                             "reason":rejection,
                             "signal_type":o["signal_type"]})
            continue
        policy=context["classes"][category]["id_policy"]
        o["object_class"]=category if policy=="auto_instance" else None
        o["instrument_class"]=category if policy=="class_only" else None
        o["object_id"]=identity.get("object_id")
        if o["signal_type"] in ("mass_g","temperature_c","stirring_speed_rpm"):
            o["object_id"]=None;o["object_class"]=None
        for side in ("source","target","subject","object"):
            track=p.get(side+"_track_id")
            if track:
                p[side+"_object_id"]=identities.get(track,{}).get("object_id")
        if kind=="flow":
            o["observed_value"]=(p.get("source_object_id") or "unknown")+" -> "+(p.get("target_object_id") or "unknown")
        if kind=="relation":
            subject="camera_wearer" if p.get("subject_track_id")=="camera_wearer" else p.get("subject_object_id") or "unknown"
            o["observed_value"]=subject+" "+str(p["relation"])+" "+(p.get("object_object_id") or "unknown")
        supported=["observed_value"]
        if kind=="flow" and not (p.get("source_object_id") and p.get("target_object_id")):
            supported=[]
            o["notes"]="Arrival evidence observed; carrier correspondence unresolved."
        if kind=="relation" and "unknown" in str(o["observed_value"]):
            supported=[]
        if o["object_class"]:
            supported.append("object_class")
        if o["object_id"]:
            supported.append("object_id")
        if o["instrument_class"]:
            supported.append("instrument_class")
        na=["object_class","object_id"] if policy=="class_only" else ["instrument_class"]
        core_evidence(o,"Observations",supported=supported,not_applicable=na)
        admitted.append(o)
    business,mapping,intervals=aggregate_points(admitted,context,connection,ids)
    with jsonl_writer(folder/"normalized_observations.jsonl") as append:
        for row in admitted:
            append(row)
    with jsonl_writer(folder/"observations.jsonl") as append:
        for row in business:
            append(row)
    with jsonl_writer(folder/"visual_evidence.jsonl") as append:
        for (data,) in connection.execute("SELECT data FROM evidence"):
            append(json.loads(data))
    write_json(folder/"identities.json",identities)
    write_json(folder/"aggregation_map.json",mapping)
    write_json(folder/"interval_evidence.json",intervals)
    write_json(folder/"filter_report.json",{
        "raw_count":connection.execute("SELECT COUNT(*) FROM points").fetchone()[0],
        "normalized_count":len(admitted),"business_count":len(business),
        "candidate_tracks":len(identities),"admitted_tracks":sum(i["business_admitted"] for i in identities.values()),
        "filter_reasons":dict(Counter(r["reason"] for r in filtered)),"filtered_records":filtered})
    for category,policy in context["classes"].items():
        if policy["id_policy"]=="auto_instance":
            prefix=category.replace(" ","_")
            ids.reconcile(prefix,[tid for tid,item in identities.items()
                                 if (item.get("object_id") or "").startswith(prefix+"_")])
    connection.commit();connection.close()
    finaldb=folder/"aggregation.sqlite";dbpath.replace(finaldb)
    ids.save()
    progress.update(f"aggregate: {raw_count} raw -> {len(admitted)} admitted points -> {len(business)} timestamp-ready business intervals")
    return [folder/p for p in ("raw_observations.jsonl","normalized_observations.jsonl","observations.jsonl",
        "visual_evidence.jsonl","identities.json","aggregation_map.json","interval_evidence.json","filter_report.json","aggregation.sqlite")]
