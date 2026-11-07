from bisect import bisect_right
from fractions import Fraction
import textwrap
import av
from PIL import ImageDraw
from ..outputs.video import _font,_color
from ..schema import read_json,write_json,file_hash
from .sampling import in_ranges
from .clock import map_time

def display_lines(text,width=125,maximum=2):
    lines=textwrap.wrap(" ".join(str(text).split()),width=width) or [""]
    return lines[:maximum-1]+[lines[maximum-1][:width-3]+"..."] if len(lines)>maximum else lines

def render(result,tracking,frames,context,progress):
    c=context["config"];output=c["output_dir"];products=[];identities=context["identities"];anchors=read_json(output/"time_mapping.json")["anchors"];shots=read_json(output/"shots.json")
    font=_font(22);small=_font(18)
    for sid,path in context["source_paths"].items():
        selected=sorted((f for f in frames if f.source_id==sid),key=lambda f:f.source_time_s);times=[f.source_time_s for f in selected];target=output/(sid+"_annotated.mp4");count=0;first_pts=None;last_time=None
        with av.open(str(path)) as incoming,av.open(str(target),"w") as outgoing:
            vin=incoming.streams.video[0];vin.codec_context.thread_count=4
            stream=outgoing.add_stream("libx264",rate=vin.average_rate or Fraction(30,1));stream.width=vin.width;stream.height=vin.height;stream.pix_fmt="yuv420p";stream.time_base=vin.time_base;stream.codec_context.time_base=vin.time_base;stream.options={"crf":str(c.get("video_crf",21)),"preset":c.get("video_preset","fast")};stream.codec_context.thread_count=4
            for decoded in incoming.decode(video=0):
                t=float((decoded.pts-(vin.start_time or 0))*decoded.time_base)
                if c.get("ranges_s") and t>=max(b for a,b in c["ranges_s"]):break
                if not in_ranges(t,c.get("ranges_s",[])):continue
                if first_pts is None:first_pts=decoded.pts;first_source_time=t
                image=decoded.to_image().convert("RGB");draw=ImageDraw.Draw(image);i=bisect_right(times,t+1e-8)-1;frame=selected[i] if i>=0 else None
                age=t-frame.source_time_s if frame else None;valid=frame and 0<=age<=1/c["sample_fps"]+.04
                clock=map_time(frame.source_time_s,sid,anchors,shots) if valid else {"datetime":None}
                lines=[f"AUTO V4.1 | {sid} | source {t:.3f}s | clock {clock.get('datetime') or 'unresolved'}",f"Evidence boxes @{frame.source_time_s:.3f}s; age {age:.3f}s | visible spans, boundaries unresolved" if valid else "No attached sample evidence"]
                texts=[o["observed_value"] for o in result["observations"] if o["signal_type"]=="ocr_text" and o["source_id"]==sid and o["start_s"]-.01<=t<=o["end_s"]+.2]
                if texts:lines.extend(display_lines("OCR: "+" | ".join(dict.fromkeys(map(str,texts))),maximum=2))
                events=[e for e in result["events"] if e["source_id"]==sid and e["start_s"]-.01<=t<=e["end_s"]+.2]
                if events:lines.append("Event evidence: "+", ".join(e["event_id"]+" "+e["action"] for e in events)[:130])
                height=min(170,8+28*len(lines));occupied=[]
                if valid:
                    for box in tracking["display"].get(f"{sid}:{frame.frame_id}",[]):
                        identity=identities.get(box["track_id"],{})
                        if not identity.get("relevant"):continue
                        color=_color(box["track_id"]);x1,y1,x2,y2=[int(v*(image.width if k%2==0 else image.height)) for k,v in enumerate(box["box_xyxy"])]
                        draw.rectangle((x1,y1,x2,y2),outline=color,width=3)
                        label=(identity.get("object_id") or identity.get("instrument_class") or "unknown")+" | "+box["evidence_kind"]
                        label=label[:64];width=int(draw.textlength(label,font=small));x=max(0,min(x1,image.width-width-4));y=max(height+3,min(y1-23,image.height-24))
                        while any(abs(y-old_y)<22 and abs(x-old_x)<max(width,old_w) for old_x,old_y,old_w in occupied) and y<image.height-48:y+=23
                        occupied.append((x,y,width));draw.rectangle((x,y,x+width+3,y+23),fill=(12,22,31));draw.text((x+1,y),label,font=small,fill=color)
                draw.rectangle((0,0,image.width,height),fill=(12,22,31))
                for n,line in enumerate(lines):
                    if 8+28*n<height:draw.text((10,3+28*n),line,font=font,fill=(240,246,250))
                encoded=av.VideoFrame.from_image(image);encoded.pts=decoded.pts-first_pts;encoded.time_base=decoded.time_base
                for packet in stream.encode(encoded):outgoing.mux(packet)
                count+=1;last_time=t
                if count%150==0:progress.update(f"annotated video {sid}: {count} selected source frames")
            for packet in stream.encode():outgoing.mux(packet)
        metadata={"input_path":str(path),"input_sha256":file_hash(path),"output_video":str(target),"source_frames_encoded":count,"source_start_s":first_source_time,"source_last_frame_s":last_time,"output_pts_origin_source_s":first_source_time,"audio_preserved":False,"human_reference_used":False,"overlay_policy":"Detector and SAM2 propagated boxes identified separately; nearest sampled evidence with age; no confirmed duration implied"}
        write_json(target.with_suffix(".json"),metadata);products.extend((target,target.with_suffix(".json")))
    return products
