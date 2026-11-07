"""Verify complete cache inputs before running a no-inference resume check."""
from pathlib import Path
import argparse,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json
from materflow.v41.context import load_context
from materflow.v41.runtime import STAGES,signature,verified

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);args=p.parse_args();c=load_config(args.config);out=c['output_dir'];path=out/'run_summary.json';before=read_json(path);context=load_context(c)
    for stage in STAGES:
        item=before['stages'].get(stage,{})
        if item.get('signature')!=signature(stage,context,before) or not verified(item,out):raise ValueError('Not a complete valid cache: '+stage+'; resume normal stages first')
    start=time.perf_counter();r=subprocess.run([sys.executable,str(ROOT/'run.py'),'--config',str(c['config_path']),'--stage','all'],cwd=ROOT,text=True,encoding='utf-8',capture_output=True)
    report={'exit_code':r.returncode,'elapsed_s':time.perf_counter()-start,'stdout':r.stdout,'stderr':r.stderr,'all_five_cached':all('Cached '+stage+':' in r.stdout for stage in STAGES)}
    if r.returncode or not report['all_five_cached']:raise RuntimeError(str(report))
    after=read_json(path)
    if before['stages']!=after['stages']:raise ValueError('Cache resume changed measured stage records')
    write_json(out/'diagnostics/hot_cache_check.json',report);print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
