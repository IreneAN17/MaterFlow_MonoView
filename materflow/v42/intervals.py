"""Typed temporal grouping with explicit evidence gaps and no duration fabrication."""
from copy import deepcopy
from statistics import median
import json
import math

def covered_segments(segments, gap=1e-7):
    result=[]
    for a,b in sorted(segments):
        if not result or a-result[-1][1]>gap:
            result.append([a,b])
        else:
            result[-1][1]=max(result[-1][1],b)
    return result

def unknown_segments(start,end,supported):
    cursor=start;gaps=[]
    for a,b in covered_segments(supported):
        if a>cursor+1e-7:
            gaps.append([cursor,min(a,end)])
        cursor=max(cursor,b)
    if cursor<end-1e-7:
        gaps.append([cursor,end])
    return [x for x in gaps if x[1]-x[0]>1e-7]

def presence_segments(connection,tid,sid,start,end,shot,max_gap):
    rows=connection.execute("SELECT t,shot FROM presence WHERE tid=? AND sid=? AND t BETWEEN ? AND ? ORDER BY t",
                            (tid,sid,start-1e-6,end+1e-6)).fetchall()
    segments=[]
    for t,s in rows:
        if s!=shot:
            continue
        if segments and t-segments[-1][1]<=max_gap+1e-7:
            segments[-1][1]=t
        else:
            segments.append([t,t])
    return segments

def continuous_presence(connection,tid,sid,start,end,shot,max_gap):
    if not tid:
        return False
    spans=presence_segments(connection,tid,sid,start,end,shot,max_gap)
    return len(spans)==1 and spans[0][0]<=start+1e-6 and spans[0][1]>=end-1e-6

def resolution_of(point,config):
    explicit=config.get("measurement_resolutions",{}).get(point.get("instrument_class"))
    explicit=explicit or config.get("measurement_resolutions",{}).get(point.get("unit"))
    if explicit:
        return float(explicit),"configured_display_resolution"
    raw=point["payload"].get("raw_text","")
    import re
    match=re.fullmatch(r"\s*[+-]?\d+(?:\.(\d+))?\s*(?:mg|kg|g|ml|l|rpm|°c|℃|c)\s*",raw,re.I)
    if match:
        decimals=len(match.group(1) or "")
        return 10.**(-decimals),"complete_display_least_digit_not_instrument_accuracy"
    return None,"unconfigured"

def aggregate_points(points,context,connection,ids):
    c=context["config"];groups=[];last_by_key={};last_values={};mapping={}
    max_visibility=1/c["sample_fps"]+.05
    for point in points:
        p=point["payload"];kind=p.get("kind");signal=point["signal_type"]
        numeric=signal in ("mass_g","temperature_c","stirring_speed_rpm")
        tid=point.get("track_id")
        value=point["observed_value"]
        if kind=="object":
            value=point.get("object_class") or point.get("instrument_class") or value
        key=(point["source_id"],point["shot_id"],tid,signal,kind,
             point.get("unit"),p.get("reading_kind"), None if numeric else str(value))
        old=last_by_key.get(key)
        gap=point["start_s"]-old["end_s"] if old else math.inf
        can_merge=False;uncertain=False
        if old and tid and 0<=gap:
            presence=continuous_presence(connection,tid,point["source_id"],old["end_s"],point["start_s"],
                                         point["shot_id"],max_visibility)
            if kind in ("object","relation"):
                can_merge=gap<=max_visibility and presence
            elif kind in ("label","display"):
                # A different reading between A and A prevents reconnecting old A records.
                seq=(tid,signal,kind,point.get("unit"))
                can_merge=(last_values.get(seq)==str(value) and presence and
                           gap<=max(c["ocr_retry_interval_s"],c["ocr_display_interval_s"])+.25)
                uncertain=can_merge and gap>max_visibility
            elif kind=="state":
                seq=(tid,signal,kind)
                can_merge=(last_values.get(seq)==str(value) and presence and
                           gap<=max(c["state_gap_s"],c["active_scene_interval_s"]+.25))
                uncertain=can_merge and gap>max_visibility
            elif numeric:
                step,method=resolution_of(point,c);oldstep=old.get("display_resolution")
                if step and oldstep and step==oldstep and presence and gap<=c["ocr_display_interval_s"]+.25:
                    can_merge=abs(float(value)-median(old["numeric_values"]))<=step*.5+1e-12
                    if p.get("display_change_trigger") is True:
                        can_merge=False
                    uncertain=can_merge and gap>max_visibility
        if can_merge:
            old["end_s"]=max(old["end_s"],point["end_s"])
            old["member_uids"].append(point["uid"])
            old["visual_evidence_refs"]+=point["visual_evidence_refs"]
            old["support_segments"]+=point.get("support_segments",[[point["start_s"],point["end_s"]]])
            if kind in ("object","relation") and gap<=max_visibility:
                old["support_segments"].append([old["last_point_end_s"],point["start_s"]])
            old["last_point_end_s"]=point["end_s"]
            old["transcription_uids"]+=p.get("transcription_uids",[])
            old["uncertain_bridge"]|=uncertain
            if numeric:
                old["numeric_values"].append(float(value))
                old["observed_value"]=median(old["numeric_values"])
                old["value_basis"]="derived"
        else:
            old=deepcopy(point)
            old["member_uids"]=[point["uid"]]
            old["observation_id"]=ids.get("O",point["uid"])
            old["support_segments"]=point.get("support_segments",[[point["start_s"],point["end_s"]]])
            old["last_point_end_s"]=point["end_s"]
            old["transcription_uids"]=p.get("transcription_uids",[])
            old["uncertain_bridge"]=False
            old["numeric_values"]=[float(value)] if numeric else []
            if numeric:
                step,method=resolution_of(point,c)
                old["display_resolution"]=step
                old["resolution_method"]=method
            groups.append(old);last_by_key[key]=old
        mapping[point["uid"]]=old["observation_id"]
        if kind in ("label","display"):
            last_values[(tid,signal,kind,point.get("unit"))]=str(value)
        if kind=="state":
            last_values[(tid,signal,kind)]=str(value)
    intervals=[]
    for row in groups:
        kind=row["payload"].get("kind")
        temporal_kind="point" if row["start_s"]==row["end_s"] else (
            "visibility" if kind=="object" else "relation" if kind=="relation" else
            "sampled_measurement_plateau" if row["numeric_values"] else
            "label_observation_span" if kind in ("label","display") else "state_evidence_span")
        spans=covered_segments(row["support_segments"])
        gaps=unknown_segments(row["start_s"],row["end_s"],spans)
        row["interval_status"]="not_applicable" if temporal_kind=="point" else "unresolved" if gaps else "supported"
        row["evidence_refs"]=sorted({mapping[uid] for uid in row["transcription_uids"] if uid in mapping}
                                    -{row["observation_id"]})
        for e in row["field_evidence"].values():
            e["evidence_refs"]=[row["observation_id"]]
        if row["numeric_values"] and len(row["numeric_values"])<c["plateau_min_samples"]:
            temporal_kind="sparse_reading_span" if row["end_s"]>row["start_s"] else "point"
        intervals.append({"record_id":row["observation_id"],"source_id":row["source_id"],"track_id":row.get("track_id"),
            "start_s":row["start_s"],"end_s":row["end_s"],"temporal_kind":temporal_kind,
            "time_semantics":"visible_evidence_span","supported_segments":spans,"unknown_segments":gaps,
            "supporting_point_uids":row["member_uids"],"visual_evidence_refs":row["visual_evidence_refs"],
            "observed_supported_duration":sum(b-a for a,b in spans),"confirmed_processing_duration":None,
            "statistics":{"count":len(row["numeric_values"]),"minimum":min(row["numeric_values"]),
                          "maximum":max(row["numeric_values"]),"median":median(row["numeric_values"])}
                         if row["numeric_values"] else None,
            "display_resolution":row.get("display_resolution"),"resolution_method":row.get("resolution_method")})
        if gaps:
            row["notes"]=(row.get("notes") or "")+" Evidence envelope contains unobserved gaps; not a confirmed continuous process."
    ids.reconcile("O",[g["uid"] for g in groups])
    ids.save()
    return groups,mapping,intervals
