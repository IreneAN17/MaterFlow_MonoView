"""Independent final cell, reference, frame/PTS and silent-video checks."""
import argparse,json,sys,hashlib
from pathlib import Path
from fractions import Fraction
import av
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,read_jsonl,write_json,file_hash
from materflow.v41.context import FIELDS,load_context
from materflow.v41.export import validate
from materflow.v41.sampling import in_ranges

def inspect_video(path,ranges=(),preview_dir=None):
    times=[];first=None
    with av.open(str(path)) as container:
        s=container.streams.video[0];s.codec_context.thread_count=4;info={'width':s.width,'height':s.height,'rate':str(s.average_rate),'audio_streams':len(container.streams.audio)}
        for frame in container.decode(video=0):
            t=Fraction(frame.pts-(s.start_time or 0))*frame.time_base
            if ranges and float(t)>=max(b for a,b in ranges):break
            if not in_ranges(float(t),ranges):continue
            if first is None:first=t
            if preview_dir and len(times) in (0,375,749):frame.to_image().save(preview_dir/f'preview_{len(times):04d}.jpg',quality=92)
            times.append(str(t-first))
    info.update(decoded_frames=len(times),relative_pts_sha256=hashlib.sha256(('\n'.join(times)).encode()).hexdigest(),first_time_s=float(first) if first is not None else None)
    return info

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);args=p.parse_args();c=load_config(args.config);out=c['output_dir'];context=load_context(c)
    s=read_json(out/'run_summary.json');assert all(s['stages'][k]['status']=='complete' for k in ('prepare','caption','ground','consolidate','derive'))
    result={'observations':read_jsonl(out/'observations.jsonl'),**read_json(out/'derived.json')};context['identities']=read_json(out/'identities.json');context['evidence_index']=read_json(out/'evidence_index.json');validate(result,context)
    diagnostic=out/'diagnostics';diagnostic.mkdir(exist_ok=True);report={'record_counts':{k:len(v) for k,v in result.items()},'workbooks':{},'videos':{},'human_accuracy_verified':False}
    for file,mapping in {'observations_v4.1.xlsx':[('Observations','observations')],'events_v4.1.xlsx':[('Events','events')],'lineage_v4.1.xlsx':[('Materials','materials'),('Lineage','lineage')]}.items():
        book=load_workbook(out/file,read_only=True,data_only=False);fields=[];counts={}
        for name,key in mapping:
            records=result[key];rows=book[name].iter_rows();assert [cell.value for cell in next(rows)]==FIELDS[name]
            for row,record in zip(rows,records,strict=True):
                expected=[]
                for column in FIELDS[name]:
                    value=record.get(column);value=';'.join(map(str,value)) if isinstance(value,list) else value;expected.append(None if value=='' else value)
                assert [cell.value for cell in row]==expected,(file,name)
                assert all(cell.data_type=='s' for cell in row if isinstance(cell.value,str))
                for column,item in record.get('field_evidence',{}).items():fields.append({'record_id':record[FIELDS[name][0]],'field':column,**item})
            counts[name]=len(records)
        rows=book['Field Evidence'].iter_rows();assert [cell.value for cell in next(rows)]==FIELDS['Field Evidence']
        for row,record in zip(rows,fields,strict=True):
            values=[record.get(k) for k in FIELDS['Field Evidence']];values=[';'.join(map(str,v)) if isinstance(v,list) else v for v in values];assert [cell.value for cell in row]==[None if v=='' else v for v in values]
        counts['Field Evidence']=len(fields);report['workbooks'][file]=counts;book.close()
    for sid,path in context['source_paths'].items():
        a=inspect_video(path,c.get('ranges_s',[]));b=inspect_video(out/(sid+'_annotated.mp4'),preview_dir=diagnostic)
        assert b['audio_streams']==0
        for key in ('width','height','rate','decoded_frames','relative_pts_sha256'):assert a[key]==b[key],(key,a[key],b[key])
        report['videos'][sid]={'source':a,'annotated':b,'all_selected_frames_and_relative_pts_preserved':True,'silent':True}
    for stage,item in s['stages'].items():
        for relative,digest in item['artifacts'].items():assert file_hash(out/relative)==digest,(stage,relative)
    report['all_artifact_hashes_valid']=True;report['all_business_cells_match_json']=True
    write_json(diagnostic/'validation.json',report);print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
