"""Cache-only resume is refused unless every pass is already valid."""
from pathlib import Path
import argparse
import subprocess
import sys
import json
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json
from materflow.v42.context import load_context
from materflow.v42.runtime import STAGES,dependencies,verified

def main():
    p=argparse.ArgumentParser();p.add_argument("--config",required=True);a=p.parse_args()
    context=load_context(load_config(a.config));c=context["config"];out=c["output_dir"]
    before=read_json(out/"run_summary.json")
    for stage in STAGES:
        item=before["stages"].get(stage,{})
        if item.get("signature")!=dependencies(stage,context,before) or not verified(item,out):
            raise ValueError("Not a valid complete cache: "+stage)
    start=time.perf_counter()
    result=subprocess.run([sys.executable,str(ROOT/"run.py"),"--config",str(c["config_path"]),"--stage","all"],
                          cwd=ROOT,text=True,encoding="utf-8",capture_output=True)
    report={"exit_code":result.returncode,"elapsed_s":time.perf_counter()-start,
        "all_eight_cached":all("Cached "+stage+":" in result.stdout for stage in STAGES),
        "stdout":result.stdout,"stderr":result.stderr}
    if result.returncode or not report["all_eight_cached"]:
        raise RuntimeError(str(report))
    if read_json(out/"run_summary.json")["stages"]!=before["stages"]:
        raise ValueError("Cache verification changed measured pass records")
    write_json(out/"diagnostics/hot_cache_check.json",report)
    print(json.dumps(report,ensure_ascii=True))

if __name__=="__main__":
    main()
