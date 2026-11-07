from pathlib import Path
import argparse
import json
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.v42.dataset import validate_dataset
from materflow.schema import write_json

def main():
    p=argparse.ArgumentParser();p.add_argument("--dataset",required=True);p.add_argument("--split-manifest",required=True)
    p.add_argument("--output",default="output/dataset_checks/v42_dataset_report.json")
    a=p.parse_args();report=validate_dataset(a.dataset,a.split_manifest);write_json(a.output,report)
    print(json.dumps({k:v for k,v in report.items() if k!="image_manifest"},ensure_ascii=True))
    raise SystemExit(0 if report["status"]=="format_checks_passed" else 2)

if __name__=="__main__":
    main()
