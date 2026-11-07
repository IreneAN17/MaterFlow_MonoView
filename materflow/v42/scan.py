"""First visual pass: real detections + camera-compensated local motion + optical tracking."""
from pathlib import Path
from collections import Counter
import time
import json
import numpy as np
from ..schema import read_json,write_json,stable_id,file_hash
from .io import internal,source_frames,jsonl_writer,records
from .tracking import create_tracker,gray_image,local_motion
from .detectors import create_detector

def scan(context,progress):
    c=context["config"];folder=internal(context)
    shots=read_json(folder/"shots.json")
    start=time.perf_counter();detector=create_detector(context);load_s=time.perf_counter()-start
    tracker=create_tracker(c);segmenter=None
    parts=[];writer=None;append=None;descriptor=None;partkey=None;last_detect={};previous=None
    reused_part=False;restore_state=None
    metrics=Counter();raw_rejections=folder/"scan_rejections.jsonl"
    begin=time.perf_counter()
    def close_part(error=None):
        nonlocal writer
        if writer is None:
            return
        writer.__exit__(type(error) if error else None,error,error.__traceback__ if error else None)
        if error is None:
            descriptor["sha256"]=file_hash(Path(descriptor["path"]))
            before=descriptor.get("logical_counts",{})
            descriptor["logical_counts"]={key:metrics[key]-before.get(key,0) for key in
                 ("sampled_frames","detections","detector_frames","optical_evidence_rows","rejected_detections")}
            if hasattr(tracker,"state"):
                descriptor["end_state"]=tracker.state()
            write_json(Path(descriptor["path"]).with_suffix(".meta.json"),descriptor)
            parts.append({k:v for k,v in descriptor.items() if k not in ("rejected","end_state")})
        writer=None
    try:
        with jsonl_writer(raw_rejections) as reject:
            for frame,image in source_frames(context):
                sid,t=frame["source_id"],frame["source_time_s"]
                thiskey=(sid,int(t//c["chunk_seconds"]))
                if thiskey != partkey:
                    close_part()
                    partkey=thiskey
                    path=folder/"scan"/(sid+"_"+str(thiskey[1]).zfill(6)+".jsonl")
                    meta=path.with_suffix(".meta.json")
                    reusable=None
                    if c["tracker_backend"]=="klt" and path.exists() and meta.exists():
                        candidate=read_json(meta)
                        if candidate.get("input_signature")==context.get("stage_signature") and candidate.get("sha256")==file_hash(path):
                            reusable=candidate
                    reused_part=reusable is not None
                    if reused_part:
                        descriptor=reusable;parts.append({k:v for k,v in reusable.items() if k not in ("rejected","end_state")})
                        restore_state=reusable["end_state"]
                        for row in reusable.get("rejected",[]):
                            reject(row)
                        for metric in ("sampled_frames","detections","detector_frames","optical_evidence_rows","rejected_detections"):
                            metrics[metric]+=reusable.get("logical_counts",{}).get(metric,0)
                        metrics["committed_partitions_reused"]+=1
                    else:
                        if restore_state is not None:
                            tracker.restore(restore_state,previous)
                            for track in tracker.active.values():
                                last_detect[track["last_frame"]["source_id"]]=max(last_detect.get(track["last_frame"]["source_id"],-1e9),track["last_detection_s"])
                            restore_state=None
                        descriptor={"path":str(path),"source_id":sid,"rows":0,"start_s":t,
                                    "input_signature":context.get("stage_signature"),"rejected":[],"logical_counts":dict(metrics)}
                        writer=jsonl_writer(path);append=writer.__enter__()
                    # Detector task caches make replay safe after interruption. Partition rows remain atomic.
                    write_json(folder/"scan_checkpoint.json",{"status":"running","partition":thiskey,
                               "completed_partition_count":len(parts),"last_committed_source_time_s":t,
                               "resume_policy":"Replay optical state from source; reuse verified neural detection tasks"})
                if reused_part:
                    previous=gray_image(image,c["optical_flow_width"])
                    metrics["replayed_decoded_samples"]+=1
                    continue
                gray=gray_image(image,c["optical_flow_width"])
                motion,camera,camera_shift=local_motion(previous,gray)
                shot=shots[f"{sid}:{frame['frame_id']}"]
                fresh_shot = tracker.shot != shot
                active=motion>=c["local_motion_threshold"]
                rate=c["detection_active_fps"] if active else c["detection_idle_fps"]
                refresh=fresh_shot or t-last_detect.get(sid,-1e9)>=1/rate-1e-6
                detections=[]
                if refresh:
                    detections,rejected=detector.query(image)
                    for d in detections:
                        d["detection_id"]=stable_id("D",sid,frame["frame_id"],d["object_class"],d["box_xyxy"])
                    for d in rejected:
                        reject({**frame,**d})
                        descriptor["rejected"].append({**frame,**d})
                    metrics["rejected_detections"]+=len(rejected)
                    metrics["detector_frames"]+=1
                    last_detect[sid]=t
                    selected=[d for d in detections if d["object_class"] in c["segmentation_classes"]]
                    if selected and c["segmentation_backend"]=="sam2":
                        if segmenter is None:
                            from .segmentation import SegmentationReview
                            segmenter=SegmentationReview(context)
                        segmenter.review(image,selected)
                display=tracker.update(frame,image,gray,detections,refresh,shot)
                row={"frame":frame,"shot_id":shot,"local_motion":motion,"camera_shift":camera_shift,
                     "camera_compensated":camera is not None,"detector_refreshed":refresh,
                     "detections":detections,"display":display}
                append(row)
                descriptor["rows"]+=1;descriptor["end_s"]=t
                previous=gray
                metrics["sampled_frames"]+=1;metrics["detections"]+=len(detections)
                metrics["optical_evidence_rows"]+=sum(d["evidence_kind"]=="optical_flow" for d in display)
                if metrics["sampled_frames"]%25==0:
                    progress.update(f"scan: {metrics['sampled_frames']} samples; {detector.calls} real forwards; {detector.hits} task-cache hits")
        close_part()
        if restore_state is not None:
            tracker.restore(restore_state,previous)
    except BaseException as e:
        close_part(e)
        raise
    write_json(folder/"scan_partitions.json",parts)
    report={**metrics,"model_load_s":load_s,"inference_and_tracking_s":time.perf_counter()-begin,
            "actual_detector":detector.version,"forward_calls":detector.calls,
            "detection_cache_hits":detector.hits,"corrupt_detection_cache":detector.corrupt,
            "tracker":c["tracker_backend"],"tracker_counters":dict(tracker.metrics),
            "peak_active_tracks":tracker.peak_active,"sam2_calls":segmenter.metrics["image_encoder_forwards"] if segmenter else 0,"dinov2_calls":0,
            "sampling_units":"source PTS seconds","human_reference_used":False}
    write_json(folder/"scan_metrics.json",report)
    write_json(folder/"scan_checkpoint.json",{"status":"complete","partitions":len(parts)})
    return [folder/p for p in ("scan_partitions.json","scan_metrics.json","scan_checkpoint.json","scan_rejections.jsonl")]+[Path(p["path"]) for p in parts]+[Path(p["path"]).with_suffix(".meta.json") for p in parts]+list(segmenter.artifacts if segmenter else [])

def scan_rows(context):
    folder=internal(context)
    for part in read_json(folder/"scan_partitions.json"):
        yield from records(part["path"])
