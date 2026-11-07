"""One Python process per stage, UTF-8 logs and reliable native exit handling."""
from pathlib import Path
import argparse,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json
from materflow.v41.runtime import STAGES,utc

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--from-stage',choices=STAGES,default='prepare');args=parser.parse_args()
    c=load_config(args.config);logs=c['output_dir']/'logs';logs.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.update(PYTHONUTF8='1',HF_DEACTIVATE_ASYNC_LOAD='1',TRITON_CACHE_DIR=str(ROOT/'models/triton-cache'))
    for stage in STAGES[STAGES.index(args.from_stage):]:
        command=[sys.executable,'-u',str(ROOT/'run.py'),'--config',str(c['config_path']),'--stage',stage]
        with (logs/(stage+'.log')).open('a',encoding='utf-8') as log:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
            try:
                for line in child.stdout:log.write(line);log.flush();print(line,end='',flush=True)
                code=child.wait()
            except KeyboardInterrupt:
                code=130
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.terminate();child.wait()
            finally:child.stdout.close()
        if code:
            path=c['output_dir']/'run_summary.json'
            if path.is_file():
                summary=read_json(path);item=summary.get('stages',{}).get(stage,{})
                if item.get('status')=='running':
                    item.update(status='interrupted' if code==130 else 'failed',exit_code=code,error='Worker exited before completion; preserved checkpoint may be resumed',finished_utc=utc());write_json(path,summary)
            raise SystemExit(code)
if __name__=='__main__':main()
