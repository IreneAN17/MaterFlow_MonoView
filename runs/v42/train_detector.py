"""Explicit opt-in training/evaluation. Never called by the recognition runner."""
from pathlib import Path
import argparse
import sys
import importlib.metadata
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.schema import write_json,file_hash
from materflow.v42.dataset import validate_dataset

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--dataset",required=True);p.add_argument("--split-manifest",required=True)
    p.add_argument("--base-weights",required=True);p.add_argument("--base-sha256",required=True)
    p.add_argument("--annotations-reviewed",action="store_true")
    p.add_argument("--evaluate-only",action="store_true")
    p.add_argument("--output",default="output/detector_training/v42")
    p.add_argument("--epochs",type=int,default=50);p.add_argument("--patience",type=int,default=10)
    p.add_argument("--imgsz",type=int,default=640);p.add_argument("--batch",type=int,default=2)
    p.add_argument("--device",default="0");p.add_argument("--seed",type=int,default=42)
    a=p.parse_args();report=validate_dataset(a.dataset,a.split_manifest)
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True);write_json(out/"dataset_check.json",report)
    if report["status"]!="format_checks_passed":
        raise SystemExit("Dataset not ready; see dataset_check.json. No training started.")
    if not a.annotations_reviewed:
        raise SystemExit("Format checks cannot confirm label completeness. Supply --annotations-reviewed only after actual review.")
    weights=Path(a.base_weights)
    if not weights.is_file() or file_hash(weights)!=a.base_sha256:
        raise SystemExit("Supplied base/evaluation weights SHA256 mismatch")
    try:
        version=importlib.metadata.version("ultralytics")
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit("Use isolated .venv-det with requirements-v42-detector.txt; GPU inference environment is unchanged.")
    if version!="8.3.228":
        raise SystemExit("Expected the pinned optional ultralytics==8.3.228")
    from ultralytics import YOLO
    model=YOLO(str(weights))
    config={**vars(a),"ultralytics":version,"dataset_sha256":file_hash(a.dataset),
            "weights_sha256":file_hash(weights),"label_format":"YOLO xywh normalized",
            "training_started":not a.evaluate_only}
    write_json(out/"training_provenance.json",config)
    if not a.evaluate_only:
        model.train(data=str(Path(a.dataset).resolve()),epochs=a.epochs,patience=a.patience,
                    imgsz=a.imgsz,batch=a.batch,device=a.device,seed=a.seed,
                    project=str(out),name="train",exist_ok=False)
        best=Path(model.trainer.best)
        model=YOLO(str(best))
        config.update(best_weights=str(best),best_sha256=file_hash(best))
    result=model.val(data=str(Path(a.dataset).resolve()),split="test",imgsz=a.imgsz,
                     batch=a.batch,device=a.device,project=str(out),name="heldout_test")
    write_json(out/"evaluation.json",{"weights":config,"metrics":result.results_dict,
        "split":"test","dataset_group_checks":report["source_group_split_verified"]})

if __name__=="__main__":
    main()
