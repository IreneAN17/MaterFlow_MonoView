"""Silent original-PTS video, source timestamps only in public overlay."""
from collections import deque
from fractions import Fraction
from datetime import datetime
import json
import sqlite3
import textwrap
import av
from PIL import ImageDraw
from ..outputs.video import _font,_color
from ..schema import read_json,write_json,file_hash
from ..v41.sampling import in_ranges
from .io import internal

def pixel_lines(text,draw,font,width,maximum=2):
    words=" ".join(str(text).split())
    lines=[];current=""
    for char in words:
        if current and draw.textlength(current+char,font=font)>width:
            lines.append(current);current=""
            if len(lines)==maximum:
                break
        current+=char
    if len(lines)<maximum and current:
        lines.append(current)
    if "".join(lines).replace(" ","") != words.replace(" ","") and lines:
        line=lines[-1]
        while line and draw.textlength(line+"...",font=font)>width:
            line=line[:-1]
        lines[-1]=line+"..."
    return lines

def render(context,result,timeline,progress):
    c=context["config"];out=c["output_dir"];folder=internal(context)
    identities=read_json(folder/"identities.json")
    connection=sqlite3.connect(folder/"aggregation.sqlite")
    products=[];font=_font(22);small=_font(18)
    for sid,path in context["source_paths"].items():
        samples=iter(connection.execute("SELECT t,data FROM samples WHERE sid=? ORDER BY t",(sid,)))
        following=next(samples,None);current=None;current_clock=None
        target=out/(sid+"_annotated.mp4");count=0;origin=None;last=None
        with av.open(str(path)) as incoming,av.open(str(target),"w") as outgoing:
            source=incoming.streams.video[0];source.codec_context.thread_count=4
            stream=outgoing.add_stream("libx264",rate=source.average_rate or Fraction(30,1))
            stream.width=source.width;stream.height=source.height;stream.pix_fmt="yuv420p"
            stream.time_base=source.time_base;stream.codec_context.time_base=source.time_base
            stream.codec_context.thread_count=4
            stream.options={"crf":str(c["video_crf"]),"preset":c["video_preset"]}
            for decoded in incoming.decode(video=0):
                t=float((decoded.pts-(source.start_time or 0))*decoded.time_base)
                if c.get("ranges_s") and t>=max(b for a,b in c["ranges_s"]):
                    break
                if not in_ranges(t,c.get("ranges_s",[])):
                    continue
                while following and following[0]<=t+1e-7:
                    current=json.loads(following[1])
                    current_clock=timeline.lookup(sid,current["frame"]["source_time_s"])
                    following=next(samples,None)
                if origin is None:
                    origin=decoded.pts;source_start=t
                im=decoded.to_image().convert("RGB");draw=ImageDraw.Draw(im)
                clock=current_clock.get("datetime") if current_clock else None
                stamp=datetime.fromisoformat(clock).strftime("%Y/%m/%d-%H:%M:%S") if clock else "unknown recording time"
                age=t-current["frame"]["source_time_s"] if current else None
                valid=current and 0<=age<=1/c["sample_fps"]+.04
                label_time=(datetime.fromisoformat(clock).strftime("%Y/%m/%d-%H:%M:%S") if clock else "unknown")
                lines=["AUTO V4.2 | "+sid+" | recording "+stamp,
                       "Evidence @"+label_time+" | "+(f"evidence delay {age:.2f}s" if valid else "no current visual evidence")+" | candidate boundaries remain unresolved"]
                texts=[o["observed_value"] for o in result["observations"] if o["signal_type"]=="ocr_text" and
                       o["source_id"]==sid and o["start_time"] and o["end_time"] and clock and
                       o["start_time"]<=clock<=o["end_time"]]
                if texts:
                    lines+=pixel_lines("OCR: "+" | ".join(dict.fromkeys(map(str,texts))),draw,font,im.width-20,2)
                event_labels=[e["event_id"]+" "+e["action"]+" ["+e["interval_status"]+"]" for e in result["events"]
                    if e["source_id"]==sid and clock and e["start_time"] and e["end_time"] and e["start_time"]<=clock<=e["end_time"]]
                if event_labels:
                    lines+=pixel_lines("Event evidence: "+", ".join(event_labels),draw,font,im.width-20,1)
                height=8+28*len(lines);occupied=[]
                if valid:
                    for d in current["display"]:
                        identity=identities.get(d["track_id"],{})
                        if not identity.get("business_admitted"):
                            continue
                        x1,y1,x2,y2=[int(v*(im.width if i%2==0 else im.height)) for i,v in enumerate(d["box_xyxy"])]
                        color=_color(d["track_id"]);draw.rectangle((x1,y1,x2,y2),outline=color,width=3)
                        name=identity.get("object_id") or identity.get("instrument_class") or "unresolved"
                        label=name+" | "+d["evidence_kind"]
                        label=pixel_lines(label,draw,small,max(50,im.width-20),1)[0]
                        width=round(draw.textlength(label,font=small))
                        x=max(0,min(x1,im.width-width-4));y=max(height+3,min(y1-24,im.height-25))
                        while any(abs(y-oy)<23 and not(x+width<ox or ox+ow<x) for ox,oy,ow in occupied) and y<im.height-49:
                            y+=24
                        occupied.append((x,y,width))
                        draw.rectangle((x,y,x+width+3,y+23),fill=(12,22,31))
                        draw.text((x+1,y),label,font=small,fill=color)
                draw.rectangle((0,0,im.width,height),fill=(12,22,31))
                for n,line in enumerate(lines):
                    draw.text((10,3+28*n),line,font=font,fill=(240,246,250))
                frame=av.VideoFrame.from_image(im);frame.pts=decoded.pts-origin;frame.time_base=decoded.time_base
                for packet in stream.encode(frame):
                    outgoing.mux(packet)
                count+=1;last=t
                if count%150==0:
                    progress.update(f"annotated video: {count} original frames rendered")
            for packet in stream.encode():
                outgoing.mux(packet)
        metadata={"source_id":sid,"input_sha256":file_hash(path),"frames":count,
            "source_start_time":timeline.iso(sid,source_start),"source_end_time":timeline.iso(sid,last),
            "audio_preserved":False,"output_pts_normalized_to_excerpt_start":True,
            "source_positions_internal":True,"human_reference_used":False}
        write_json(target.with_suffix(".json"),metadata)
        write_json(folder/(sid+"_video_positions.json"),{"source_start_s":source_start,"source_last_s":last,"first_pts":origin})
        products+=[target,target.with_suffix(".json"),folder/(sid+"_video_positions.json")]
    connection.close()
    return products
