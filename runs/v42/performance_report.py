"""Model-free audit; never load the historical giant partition index."""
from pathlib import Path
import argparse,json,sys,statistics,subprocess,importlib.metadata,time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json
def array_items(path):
    decoder=json.JSONDecoder()
    with Path(path).open(encoding='utf-8-sig') as stream:
        buffer='';offset=0;ended=False;started=False
        while True:
            if offset>=len(buffer):
                block=stream.read(1024*1024)
                if not block:break
                buffer=block;offset=0
            while offset<len(buffer) and (buffer[offset].isspace() or buffer[offset]==','):offset+=1
            if offset>=len(buffer):continue
            if not started:
                if buffer[offset]!='[':raise ValueError('Expected legacy JSON array')
                started=True;offset+=1;continue
            if buffer[offset]==']':ended=True;break
            try:value,end=decoder.raw_decode(buffer,offset)
            except json.JSONDecodeError:
                more=stream.read(1024*1024)
                if not more:raise
                buffer=buffer[offset:]+more;offset=0;continue
            offset=end;yield value
        if not ended:raise ValueError('Truncated JSON array')

def observations_audit(out):
    from collections import Counter
    path=out/'internal/ocr_results.jsonl';counts=Counter();tracks={};rows=0;empty=0
    if path.exists():
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if not line.strip():continue
                row=json.loads(line);rows+=1;empty+=not bool(row.get('raw_text'));category=row['object_class']
                counts[category]+=1;tracks.setdefault(category,set()).add(row['track_id'])
    return {'query_records':rows,'empty_records':empty,'empty_fraction':empty/rows if rows else None,'class_queries':dict(counts),'unique_track_fragments':{k:len(v) for k,v in tracks.items()}}

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args();c=load_config(a.config);out=c['output_dir']
    summary=read_json(out/'run_summary.json') if (out/'run_summary.json').exists() else {'stages':{}}
    result={'format_version':2,'run_id':c['run_id'],'stages':{},'metrics':{},'independent_quality_verified':False,'sleep_intervals':'unknown; consult system event audit','model_inference_started_by_report':False}
    for name,item in summary['stages'].items():result['stages'][name]={k:item.get(k) for k in ('status','elapsed_s','process_cpu_s','error')}
    for name in ('prepare','scan','ocr','semantic'):
        path=out/'internal'/(name+'_metrics.json')
        if path.exists():result['metrics'][name]={k:v for k,v in read_json(path).items() if not isinstance(v,(dict,list))}
    result['metadata_bytes']={name:(out/'internal'/name).stat().st_size for name in ('scan_partitions.json','scan_checkpoint.json','semantic_plan.json','shots.json') if (out/'internal'/name).exists()}
    result['status_bytes']=(out/'status.json').stat().st_size if (out/'status.json').exists() else None
    result['ocr_audit']=observations_audit(out)
    plan=out/'internal/semantic_plan.json'
    if plan.exists():
        lengths=[len(j.get('candidates',[])) for j in array_items(plan)]
        result['semantic_candidate_distribution']={'count':len(lengths),'p50':statistics.median(lengths) if lengths else None,'p95':sorted(lengths)[min(len(lengths)-1,int(len(lengths)*.95))] if lengths else None,'max':max(lengths,default=None)}
    result['environment']={'python':sys.version,'packages':{n:importlib.metadata.version(n) for n in ('torch','transformers','bitsandbytes','numpy')}}
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True)
    result['git_head']=head.stdout.strip() if head.returncode==0 else None
    result['workspace_status']=subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True).stdout.splitlines()
    target=out/'diagnostics/performance.json';write_json(target,result);print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
