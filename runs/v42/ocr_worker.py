"""Real CPU OCR in its isolated environment. No target catalog is input."""
from pathlib import Path
import argparse
import hashlib
import json
import importlib.metadata
import time
import sys
import numpy as np
from PIL import Image
import rapidocr_onnxruntime
from rapidocr_onnxruntime import RapidOCR

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--mode", choices=("clock", "regions"), required=True)
    args = p.parse_args()
    started = time.perf_counter()
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    models = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
              for f in Path(rapidocr_onnxruntime.__file__).parent.rglob("*.onnx")}
    versions = {n: importlib.metadata.version(n) for n in
                ("rapidocr-onnxruntime", "onnxruntime", "numpy", "opencv-python")}
    results = []
    for item in json.loads(Path(args.input).read_text(encoding="utf-8-sig")):
        image = np.asarray(Image.open(item["crop_path"]).convert("RGB"))[:, :, ::-1].copy()
        output, elapsed = engine(image, use_det=args.mode=="regions", use_cls=args.mode=="regions")
        blocks = []
        if output:
            if args.mode == "clock":
                blocks = [{"raw_text": str(output[0][0]), "model_score": float(output[0][1]), "quad_px": None}]
            else:
                blocks = [{"quad_px": np.asarray(row[0]).tolist(), "raw_text": str(row[1]),
                           "model_score": float(row[2])} for row in output]
        results.append({**item, "raw_text": "\n".join(b["raw_text"] for b in blocks),
                        "model_score": min((b["model_score"] for b in blocks), default=0.),
                        "blocks": blocks, "elapsed": elapsed})
    report = {"results": results, "engine": "RapidOCR-ONNXRuntime", "versions": versions,
              "model_sha256": models, "mode": args.mode, "elapsed_s": time.perf_counter()-started,
              "scores_are_calibrated_probabilities": False}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def serve():
    started=time.perf_counter()
    # Reserve stdout exclusively for UTF-8 JSONL protocol.
    protocol=sys.stdout;sys.stdout=sys.stderr
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    init_s=time.perf_counter()-started
    stamp=time.perf_counter()
    manifest={'engine':'RapidOCR-ONNXRuntime','versions':{n:importlib.metadata.version(n) for n in ('rapidocr-onnxruntime','onnxruntime','numpy','opencv-python')},
        'model_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(rapidocr_onnxruntime.__file__).parent.rglob('*.onnx')},
        'providers':sorted({provider for component in (engine.text_det,engine.text_cls,engine.text_rec) for provider in getattr(getattr(component,'infer',getattr(component,'session',None)),'session').get_providers()}),'preprocessing_version':'RGB-to-BGR;clock-rec-only;regions-det-cls-rec-v1',
        'scores_are_calibrated_probabilities':False}
    def send(value):protocol.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');protocol.flush()
    send({'type':'ready','manifest':manifest,'engine_init_s':init_s,'model_manifest_s':time.perf_counter()-stamp})
    for line in sys.stdin:
        request=json.loads(line);item=request['item'];mode=request['mode'];t=time.perf_counter()
        try:
            image=np.asarray(Image.open(item['crop_path']).convert('RGB'))[:,:,::-1].copy()
            load_s=time.perf_counter()-t;t=time.perf_counter()
            output,elapsed=engine(image,use_det=mode=='regions',use_cls=mode=='regions')
            blocks=[]
            if output:
                if mode=='clock':blocks=[{'raw_text':str(output[0][0]),'model_score':float(output[0][1]),'quad_px':None}]
                else:blocks=[{'quad_px':np.asarray(row[0]).tolist(),'raw_text':str(row[1]),'model_score':float(row[2])} for row in output]
            result={**item,'raw_text':'\n'.join(b['raw_text'] for b in blocks),'model_score':min((b['model_score'] for b in blocks),default=0.),'blocks':blocks,'elapsed':elapsed,
                'load_crop_s':load_s,'inference_s':time.perf_counter()-t}
            send({'type':'result','request_id':item.get('request_id'),'result':result})
        except Exception as error:send({'type':'result','request_id':item.get('request_id'),'error':type(error).__name__+': '+str(error)})

if __name__ == "__main__":
    if "--serve" in sys.argv:serve()
    else:main()
