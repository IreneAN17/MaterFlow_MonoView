"""Independent workbook cells, references, timestamps, provenance and silent source-PTS video QA."""
from pathlib import Path
from fractions import Fraction
import argparse
import hashlib
import json
import sys
import av
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json,file_hash
from materflow.v42.context import load_context
from materflow.v42.runtime import STAGES,dependencies,verified
from materflow.v42.export import FIELDS,public_rows,validate,excel_value
from materflow.v41.sampling import in_ranges

def video_info(path,ranges=(),previews=None,positions=()):
    pts=[];first=None
    with av.open(str(path)) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=4
        result={"width":stream.width,"height":stream.height,"rate":str(stream.average_rate),
                "audio_streams":len(container.streams.audio)}
        for frame in container.decode(video=0):
            t=Fraction(frame.pts-(stream.start_time or 0))*frame.time_base
            if ranges and float(t)>=max(b for a,b in ranges):
                break
            if not in_ranges(float(t),ranges):
                continue
            if first is None:
                first=t
            if previews and len(pts) in positions:
                frame.to_image().save(previews/("preview_"+str(len(pts)).zfill(4)+".jpg"),quality=92)
            pts.append(str(t-first))
    result.update(frames=len(pts),relative_pts_sha256=hashlib.sha256("\n".join(pts).encode()).hexdigest())
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--config",required=True);a=p.parse_args()
    context=load_context(load_config(a.config));c=context["config"];out=c["output_dir"]
    summary=read_json(out/"run_summary.json")
    for stage in STAGES:
        item=summary["stages"].get(stage,{})
        assert item.get("signature")==dependencies(stage,context,summary),stage+" dependency signature stale"
        assert verified(item,out),stage+" artifact verification failed"
    result,raw,timeline=public_rows(context);validate(result,raw,context)
    diagnostic=out/"diagnostics";diagnostic.mkdir(exist_ok=True)
    report={"schema_version":"ChemFlow-v4.2","record_counts":{k:len(v) for k,v in result.items()},
            "workbooks":{},"videos":{},"all_stage_dependencies_and_hashes_valid":True,
            "human_accuracy_verified":False,"custom_detector_training_executed":False}
    for file,parts in (("observations_v4.2.xlsx",[("Observations","observations")]),
                        ("events_v4.2.xlsx",[("Events","events")]),
                        ("lineage_v4.2.xlsx",[("Materials","materials"),("Lineage","lineage")])):
        book=load_workbook(out/file,read_only=True,data_only=False);checks={};fields=[]
        groups=list(parts)
        if file.startswith("observations"):
            groups.append(("Object Identities","objects"))
        for name,key in groups:
            rows=book[name].iter_rows()
            assert [cell.value for cell in next(rows)]==FIELDS[name]
            for cells,record in zip(rows,result[key],strict=True):
                expected=[excel_value(k,record.get(k)) for k in FIELDS[name]]
                assert [cell.value for cell in cells]==[None if v=="" else v for v in expected],(file,name)
                for cell in cells:
                    if isinstance(cell.value,str):
                        assert cell.data_type=="s"
            checks[name]=len(result[key])
        for name,key in parts:
            for record in raw[key]:
                for field,detail in record.get("field_evidence",{}).items():
                    fields.append({"record_id":record[FIELDS[name][0]],"field":field,**detail})
        rows=book["Field Evidence"].iter_rows()
        assert [cell.value for cell in next(rows)]==FIELDS["Field Evidence"]
        for cells,record in zip(rows,fields,strict=True):
            expected=[excel_value(k,record.get(k)) for k in FIELDS["Field Evidence"]]
            assert [cell.value for cell in cells]==[None if v=="" else v for v in expected]
        checks["Field Evidence"]=len(fields);book.close();report["workbooks"][file]=checks
    for key,rows in result.items():
        public=[json.loads(line) for line in (out/(key+".jsonl")).read_text(encoding="utf-8").splitlines()]
        assert rows==public,key
        assert all("start_s" not in row and "end_s" not in row for row in public)
    for sid,path in context["source_paths"].items():
        source=video_info(path,c.get("ranges_s",[]))
        positions=(0,source["frames"]//2,source["frames"]-1)
        annotated=video_info(out/(sid+"_annotated.mp4"),previews=diagnostic,positions=positions)
        for key in ("width","height","rate","frames","relative_pts_sha256"):
            assert source[key]==annotated[key],(key,source[key],annotated[key])
        assert annotated["audio_streams"]==0
        report["videos"][sid]={"source":source,"annotated":annotated,
                              "all_selected_source_frames_and_pts_preserved":True,"silent":True}
    report["all_public_cells_match_automatic_json"]=True
    report["all_public_references_valid"]=True
    report["public_main_tables_use_wall_clock_only"]=True
    report["missing_clock_fields"]=sum(not r.get(k) for name in ("observations","events")
       for r in result[name] for k in ("start_time","end_time"))
    write_json(diagnostic/"validation.json",report)
    print(json.dumps(report,ensure_ascii=True))

if __name__=="__main__":
    main()
