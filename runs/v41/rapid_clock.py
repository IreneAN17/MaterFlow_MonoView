"""CPU-only pretrained OCR; kept separate from the GPU inference environment."""
import argparse,json,hashlib,importlib.metadata,sys
from pathlib import Path
import rapidocr_onnxruntime
from rapidocr_onnxruntime import RapidOCR
from PIL import Image
import numpy as np

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--input",required=True);parser.add_argument("--output",required=True);args=parser.parse_args()
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    modeldir=Path(rapidocr_onnxruntime.__file__).parent
    models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in modeldir.rglob("*.onnx")}
    versions={name:importlib.metadata.version(name) for name in ("rapidocr-onnxruntime","onnxruntime","numpy","opencv-python")}
    result=[]
    for item in json.loads(Path(args.input).read_text(encoding="utf-8-sig")):
        image=np.asarray(Image.open(item["crop_path"]).convert("RGB"))[:,:,::-1].copy()
        reading,elapsed=engine(image,use_det=False,use_cls=False)
        text="";score=0.
        if reading:
            text=str(reading[0][0]);score=float(reading[0][1])
        result.append({**item,"raw_text":text,"model_score":score,"engine":"RapidOCR-ONNXRuntime","versions":versions,"pretrained_model_sha256":models,"elapsed":elapsed,"recognition_score_is_calibrated_probability":False})
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__":main()
