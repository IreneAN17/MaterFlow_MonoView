from pathlib import Path
import argparse
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.v42.context import load_context
from materflow.v42.catalog import load_catalog
from materflow.v42.gallery import build_index

def main():
    p=argparse.ArgumentParser();p.add_argument("--config",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();context=load_context(load_config(a.config))
    catalog=load_catalog(context["config"].get("catalog_path"))
    build_index(context,catalog,Path(a.output))
    print(a.output)

if __name__=="__main__":
    main()
