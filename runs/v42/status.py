from pathlib import Path
import argparse
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json
from materflow.v42.runtime import process_info

def main():
    p=argparse.ArgumentParser();p.add_argument("--config",required=True);a=p.parse_args()
    c=load_config(a.config);path=c["output_dir"]/"run_summary.json"
    if not path.exists():
        print("Not started:",c["output_dir"]);return
    s=read_json(path)
    live=c["output_dir"]/"status.json"
    if live.exists():
        latest=read_json(live)
        if latest.get("stage") in s["stages"] and latest.get("pid")==s["stages"][latest["stage"]].get("pid"):
            s["stages"][latest["stage"]].update(latest)
    for name,item in s["stages"].items():
        status=item["status"]
        if status=="running":
            process=process_info(item["pid"])
            if not process["alive"] or process["created"]!=item.get("pid_created"):
                status="stopped; resume required"
        print(name+": "+status+"; "+str(round(item.get("elapsed_s",0),1))+"s; "+str(item.get("progress","")))
        if item.get("error"):
            print("  "+item["error"])

if __name__=="__main__":
    main()
