"""Events consume admitted observations only. Materials require physical carrier evidence."""
from pathlib import Path
from copy import deepcopy
import json
import sqlite3
from ..schema import read_json,write_json,stable_id
from ..materials.events import propose_lab_events
from ..v41.context import core_evidence
from ..v41.domain import material_lineage
from .ids import Ids
from .io import internal,records
from .intervals import unknown_segments,covered_segments
from .timeline import Timeline

def derive(context,progress):
    c=context["config"];folder=internal(context)
    observations=list(records(folder/"observations.jsonl"))
    raw=list(records(folder/"normalized_observations.jsonl"))
    mapping=read_json(folder/"aggregation_map.json")
    identities=read_json(folder/"identities.json")
    context["identities"]=identities
    # Motion processing events cannot be confirmed from an isolated photograph.
    event_raw=[o for o in raw if not (o["payload"].get("state") in
        ("stirring_motion","stirring_started","sample_in_oven","sample_placed_in_oven") and
        o["payload"].get("temporal_input")!="ordered_images")]
    candidates=propose_lab_events(event_raw,context)
    byid={o["uid"]:o for o in raw}
    merged=[]
    for event in sorted(candidates,key=lambda e:(e["source_id"],e["observed_start_s"],e["action"])):
        if event["action"] not in context["actions"]:
            continue
        shot=byid[event["evidence_refs"][0]]["shot_id"]
        event["shot_id"]=shot
        previous=next((p for p in reversed(merged) if p["source_id"]==event["source_id"] and
                       p["shot_id"]==shot and all(p.get(k)==event.get(k) for k in
                       ("action","source_track_id","target_track_id"))),None)
        overlap=bool(previous and set(previous["evidence_refs"])&set(event["evidence_refs"]))
        touching=bool(previous and 0<=event["observed_start_s"]-previous["observed_end_s"]<=1/c["sample_fps"]+.05)
        if overlap or touching:
            previous["evidence_refs"]=sorted(set(previous["evidence_refs"]+event["evidence_refs"]))
            previous["observed_end_s"]=max(previous["observed_end_s"],event["observed_end_s"])
            for key in ("boundary_start_refs","boundary_end_refs"):
                previous[key]=sorted(set(previous[key]+event[key]))
            for part in event["output_parts"]:
                if part not in previous["output_parts"]:
                    previous["output_parts"].append(part)
        else:
            merged.append(event)
    ids=Ids(folder/"id_map.json",c["run_id"]);timeline=Timeline(context)
    intervals=read_json(folder/"interval_evidence.json")
    interval_byid={r["record_id"]:r for r in intervals}
    event_intervals=[]
    for event in merged:
        uid=stable_id("E",c["run_id"],event["action"],sorted(event["evidence_refs"]))
        event.update(uid=uid,event_id=ids.get("E",uid))
        start=event.get("start_s");end=event.get("end_s")
        event["start_s"]=event["observed_start_s"] if start is None else start
        event["end_s"]=event["observed_end_s"] if end is None else end
        source_refs=list(event["evidence_refs"])
        public_refs=sorted({mapping[r] for r in source_refs})
        supports=covered_segments([segment for oid in public_refs for segment in interval_byid[oid]["supported_segments"]])
        gaps=unknown_segments(event["start_s"],event["end_s"],supports)
        bounded=bool(event["boundary_start_refs"] and event["boundary_end_refs"] and not gaps)
        clock_ok=timeline.iso(event["source_id"],event["start_s"]) and timeline.iso(event["source_id"],event["end_s"])
        event["interval_status"]="supported" if bounded and clock_ok else "unresolved"
        event["evidence_refs"]=public_refs
        event.pop("sop_step_candidate",None)
        if event["action"]=="filter" and event["output_parts"]:
            primary=next((p for p in event["output_parts"] if p["part"]=="retentate"),event["output_parts"][0])
            event["target_track_id"]=primary["track_id"]
            event["target_container_id"]=identities.get(primary["track_id"],{}).get("object_id")
        event["notes"]="Visible evidence span; full processing duration unresolved." if event["interval_status"]!="supported" else "Both boundaries and intervening evidence supported."
        fields=["action"]+[k for k in ("source_container_id","target_container_id") if event.get(k)]
        core_evidence(event,"Events",public_refs,supported=fields,
                      not_applicable=["source_container_id"] if event["action"] in ("stir","hold") else [])
        event_intervals.append({"record_id":event["event_id"],"source_id":event["source_id"],
            "start_s":event["start_s"],"end_s":event["end_s"],"temporal_kind":"event",
            "time_semantics":"confirmed_boundaries" if event["interval_status"]=="supported" else "visible_evidence_span",
            "supported_segments":supports,"unknown_segments":gaps,
            "boundary_start_refs":event["boundary_start_refs"],"boundary_end_refs":event["boundary_end_refs"],
            "confirmed_processing_duration":timeline.elapsed(event["source_id"],event["start_s"],event["end_s"])
                if event["interval_status"]=="supported" else None})
    connection=sqlite3.connect(folder/"aggregation.sqlite")
    tracks=[]
    tids={e.get(key) for e in merged for key in ("source_track_id","target_track_id")}-{None}
    for tid in tids:
        history=[{"source_time_s":t} for (t,) in connection.execute("SELECT t FROM presence WHERE tid=? ORDER BY t",(tid,))]
        tracks.append({"track_id":tid,"history":history})
    connection.close()
    materials,lineage=material_lineage(merged,context,ids,{"tracks":tracks})
    ids.reconcile("E",[e["uid"] for e in merged]);ids.save()
    write_json(folder/"derived.json",{"events":merged,"materials":materials,"lineage":lineage})
    write_json(folder/"event_interval_evidence.json",event_intervals)
    write_json(folder/"derive_metrics.json",{"events":len(merged),"materials":len(materials),"lineage":len(lineage),
        "supported_events":sum(e["interval_status"]=="supported" for e in merged),
        "empty_lineage_reason":"No admitted, bound physical flow/process evidence" if not lineage else None,
        "events_read_pixels":False,"packaging_defines_contents":False})
    progress.update(f"derive: {len(merged)} events, {len(materials)} material states, {len(lineage)} lineage edges")
    return [folder/"derived.json",folder/"event_interval_evidence.json",folder/"derive_metrics.json"]
