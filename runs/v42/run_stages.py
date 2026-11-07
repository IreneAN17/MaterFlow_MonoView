"""One independent process per V4.2 pass; safely merged UTF-8 logs."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.v42.runtime import STAGES,utc
from materflow.schema import read_json,write_json

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True)
    p.add_argument("--from-stage",choices=STAGES,default="prepare")
    p.add_argument("--through-stage",choices=STAGES,default="export")
    a=p.parse_args();c=load_config(a.config)
    start,end=STAGES.index(a.from_stage),STAGES.index(a.through_stage)
    if end<start:
        p.error("through-stage precedes from-stage")
    logs=c["output_dir"]/"logs";logs.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.update(PYTHONUTF8="1",HF_DEACTIVATE_ASYNC_LOAD="1",TRITON_CACHE_DIR=str(ROOT/"models/triton-cache"))
    for stage in STAGES[start:end+1]:
        with (logs/(stage+".log")).open("a",encoding="utf-8") as log:
            child=subprocess.Popen([sys.executable,"-u",str(ROOT/"run.py"),"--config",str(c["config_path"]),"--stage",stage],
                cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
            try:
                for line in child.stdout:
                    log.write(line);log.flush();print(line,end="",flush=True)
                code=child.wait()
            except KeyboardInterrupt:
                code=130
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate();child.wait()
            finally:
                child.stdout.close()
        if code:
            path=c["output_dir"]/"run_summary.json"
            if path.exists():
                summary=read_json(path);item=summary["stages"].get(stage,{})
                if item.get("status")=="running":
                    item.update(status="interrupted" if code==130 else "failed",exit_code=code,
                                finished_utc=utc(),error="Worker stopped before committing this pass")
                    write_json(path,summary)
            raise SystemExit(code)

if __name__=="__main__":
    main()
