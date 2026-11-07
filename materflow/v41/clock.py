import re,subprocess,unicodedata
from datetime import datetime,timedelta
from pathlib import Path
from PIL import Image,ImageOps,ImageEnhance
from ..schema import write_json,read_json

def parse_clock(text):
    value=unicodedata.normalize("NFKC",text)
    match=re.search(r"(20\d{2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*[:：]\s*(\d{2})",value)
    if not match:return None
    try:return datetime(*map(int,match.groups())).isoformat(sep=" ")
    except ValueError:return None

def read_clocks(frames,config):
    output=config["output_dir"];folder=output/"clock_roi";folder.mkdir(exist_ok=True)
    rows=[];last={}
    roi=config.get("clock_roi",[.78,.93,.997,.985])
    for frame in frames:
        if frame.source_time_s-last.get(frame.source_id,-1e9)<.99:continue
        last[frame.source_id]=frame.source_time_s
        with Image.open(frame.image_path) as original:
            w,h=original.size;box=[int(v*(w if i%2==0 else h)) for i,v in enumerate(roi)]
            crop=original.crop(box).convert("RGB");crop=crop.resize((crop.width*2,crop.height*2))
            path=folder/f"{frame.source_id}_{frame.frame_id}.png";crop.save(path)
        rows.append({"source_id":frame.source_id,"frame_id":frame.frame_id,"source_time_s":frame.source_time_s,"crop_path":str(path.resolve()),"roi_xyxy":roi})
    write_json(output/"clock_inputs.json",rows)
    python=config["repo_root"]/".venv-ocr/Scripts/python.exe"
    script=config["repo_root"]/"runs/v41/rapid_clock.py"
    if not python.is_file():raise FileNotFoundError("Install isolated OCR environment with runs/v41/setup_ocr.ps1")
    process=subprocess.run([str(python),str(script),"--input",str(output/"clock_inputs.json"),"--output",str(output/"clock_raw.json")],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if process.returncode:raise RuntimeError("Clock OCR failed: "+process.stderr[-1500:])
    raw=read_json(output/"clock_raw.json");raw=raw if isinstance(raw,list) else [raw]
    for item in raw:item.update(datetime=parse_clock(item["raw_text"]) if item.get("model_score",0)>=.85 else None,precision_s=1,timezone=config.get("timezone","Asia/Shanghai"))
    write_json(output/"clock_anchors.json",raw)
    return raw

def map_time(time_s,source_id,anchors,shots):
    if time_s is None:return {"datetime":None,"status":"unresolved"}
    valid=[a for a in anchors if a["source_id"]==source_id and a.get("datetime")]
    for a in valid:
        if abs(a["source_time_s"]-time_s)<1e-6:return {"datetime":a["datetime"],"status":"supported","anchor_frames":[a["frame_id"]],"precision_s":1,"timezone":a["timezone"]}
    for a,b in zip(valid,valid[1:]):
        if not a["source_time_s"]<time_s<b["source_time_s"] or b["source_time_s"]-a["source_time_s"]>1.3:continue
        if shots.get(f"{source_id}:{a['frame_id']}")!=shots.get(f"{source_id}:{b['frame_id']}"):continue
        delta=(datetime.fromisoformat(b["datetime"])-datetime.fromisoformat(a["datetime"])).total_seconds()
        if abs(delta-(b["source_time_s"]-a["source_time_s"]))>.1:continue
        dt=datetime.fromisoformat(a["datetime"])+timedelta(seconds=int(time_s-a["source_time_s"]))
        return {"datetime":dt.isoformat(sep=" "),"status":"supported","method":"local_clock_interpolation_seconds","precision_s":1,"anchor_frames":[a["frame_id"],b["frame_id"]],"timezone":a["timezone"]}
    return {"datetime":None,"status":"unresolved"}
