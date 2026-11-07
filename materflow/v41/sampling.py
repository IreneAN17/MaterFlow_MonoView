from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image
from ..schema import FrameRecord,write_json,write_jsonl
import av

def selected_frames(path,config):
    ranges=config.get("ranges_s",[]);next_time=-1
    with av.open(str(path)) as container:
        video=container.streams.video[0];video.codec_context.thread_count=4
        for index,frame in enumerate(container.decode(video)):
            if frame.pts is None:raise ValueError("Video PTS required")
            t=float((frame.pts-(video.start_time or 0))*frame.time_base)
            if ranges and t>=max(b for a,b in ranges):break
            if not in_ranges(t,ranges) or t+1e-8<next_time:continue
            next_time=t+1/config["sample_fps"]-1e-7
            yield {"source_frame_index":index,"source_time_s":t,"pts":frame.pts,"time_base":str(frame.time_base),"image":frame.to_image()}
from .clock import read_clocks

def in_ranges(time_s,ranges):return not ranges or any(a-1e-8<=time_s<b-1e-8 for a,b in ranges)

def prepare(context,progress):
    config=context["config"];output=config["output_dir"];ranges=config.get("ranges_s",[]);frames=[];motions={};shots={};selection=[]
    for sid,path in context["source_paths"].items():
        folder=output/"frames"/sid;folder.mkdir(parents=True,exist_ok=True)
        previous=None;last_time=None;shot=0;next_sample=-1;source_frames=[]
        for decoded in selected_frames(path,config):
            t=decoded["source_time_s"]
            if t is None:raise ValueError("Video PTS required")
            if ranges and t>=max(b for a,b in ranges):break
            if not in_ranges(t,ranges) or t+1e-8<next_sample:continue
            next_sample=t+1/config["sample_fps"]-1e-7
            image=decoded.pop("image");thumb=np.asarray(image.resize((96,54)),dtype=np.float32)/255
            motion=float(np.abs(thumb-previous).mean()) if previous is not None else 1.
            hist=float(np.abs(thumb.mean(axis=(0,1))-previous.mean(axis=(0,1))).mean()) if previous is not None else 1.
            if previous is None or t-last_time>1.5/config["sample_fps"] or (motion>config.get("cut_threshold",.38) and hist>.1):shot+=1
            fid=decoded["source_frame_index"];target=folder/f"{fid:08d}.png";image.save(target,compress_level=1)
            frame=FrameRecord(sid,fid,image_path=str(target.resolve()),width=image.width,height=image.height,**decoded)
            source_frames.append(frame)
            if len(source_frames)%25==0:progress.update(f"prepare {sid}: sampled {len(source_frames)} frames")
            motions[f"{sid}:{fid}"]=motion;shots[f"{sid}:{fid}"]=f"{sid}:{shot}";previous=thumb;last_time=t
        frames.extend(source_frames)
        progress.update(f"prepare {sid}: {len(source_frames)} sampled frames")
    if not frames:raise ValueError("Empty selected source range")
    anchors=read_clocks(frames,config)
    # A clock jump is a hard boundary even when the scene looks similar.
    for sid in context["sources"]:
        valid=[a for a in anchors if a["source_id"]==sid and a.get("datetime")]
        for a,b in zip(valid,valid[1:]):
            delta=(datetime.fromisoformat(b["datetime"])-datetime.fromisoformat(a["datetime"])).total_seconds()
            if abs(delta-(b["source_time_s"]-a["source_time_s"]))>1.1:
                for f in frames:
                    if f.source_id==sid and f.source_time_s>=b["source_time_s"]:shots[f"{f.source_id}:{f.frame_id}"]+=f":clock{b['frame_id']}"
    for sid in context["sources"]:
        last=-1e9;old_shot=None
        for f in [f for f in frames if f.source_id==sid]:
            reason="shot_start" if shots[f"{f.source_id}:{f.frame_id}"]!=old_shot else "periodic" if f.source_time_s-last>=config.get("caption_interval_s",2.5)-1e-6 else "motion" if motions[f"{f.source_id}:{f.frame_id}"]>=config.get("motion_threshold",.075) and f.source_time_s-last>=1/config.get("caption_active_fps",1)-1e-6 else None
            old_shot=shots[f"{f.source_id}:{f.frame_id}"]
            if reason:selection.append({**asdict(f),"selection_reason":reason});last=f.source_time_s
    write_jsonl(output/"frame_index.jsonl",frames);write_json(output/"caption_schedule.json",selection)
    write_json(output/"shots.json",shots);write_json(output/"motion_scores.json",motions)
    write_json(output/"context.json",{"schema_version":"ChemFlow-v4.1","qwen_context":context["qwen_context"],"model_context_excludes":["Reagents","SOP","reference_rows"]})
    write_json(output/"analysis_policy.json",{"sample_fps":config["sample_fps"],"caption_interval_s":config.get("caption_interval_s",2.5),"caption_active_fps":config.get("caption_active_fps",1),"sampled_frames":len(frames),"caption_frames":len(selection),"ranges_s":ranges,"human_reference_used":False,"clock_engine":"RapidOCR-ONNXRuntime"})
    return [p for p in output.rglob("*") if p.is_file() and ("frames" in p.relative_to(output).parts or p.parent.name=="clock_roi" or p.name in ("frame_index.jsonl","caption_schedule.json","shots.json","motion_scores.json","context.json","analysis_policy.json","clock_inputs.json","clock_raw.json","clock_anchors.json"))]
