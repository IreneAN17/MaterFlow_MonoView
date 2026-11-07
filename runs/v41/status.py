from pathlib import Path
import argparse,json,sys,ctypes
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);args=parser.parse_args();c=load_config(args.config);path=c["output_dir"]/"run_summary.json"
    if not path.is_file():print("Not started:",c["output_dir"]);return
    s=read_json(path)
    for name,item in s["stages"].items():
        status=item["status"]
        if status=="running":
            from materflow.v41.runtime import process_info
            process=process_info(item["pid"])
            if not process["alive"] or (item.get("pid_created") and item["pid_created"]!=process["created"]):status="stopped (stale running entry; resume required)"
            elif not item.get("pid_created"):status="running (creation identity unavailable in this older stage record)"
        elapsed=f"{item['elapsed_s']:.1f}s" if "elapsed_s" in item else "in progress"
        print(f"{name}: {status}; elapsed={elapsed}; PID={item.get('pid')}; {item.get('progress','')}")
        if item.get("error"):print("  "+item["error"])
if __name__=="__main__":main()
