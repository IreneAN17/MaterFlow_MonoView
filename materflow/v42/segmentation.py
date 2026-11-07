"""Optional on-demand SAM2 image review, separate from full video tracking."""
from pathlib import Path
from collections import Counter
import numpy as np
from ..schema import file_hash,stable_id,write_json,read_json
from ..perception.grounding import box_iou
from ..tracking.objects import mask_box
from .io import internal,pixel_hash

class SegmentationReview:
    def __init__(self,context):
        import torch
        from transformers import Sam2Model,Sam2Processor
        c=context["config"];self.torch=torch;self.device=c["device"]
        self.dtype=torch.bfloat16 if self.device.startswith("cuda") else torch.float32
        kw={"revision":c["sam2_revision"],"local_files_only":c["local_files_only"],
            "cache_dir":c.get("model_cache_dir")}
        self.processor=Sam2Processor.from_pretrained(c["sam2_model"],**kw)
        self.model=Sam2Model.from_pretrained(c["sam2_model"],**kw).to(self.device,dtype=self.dtype).eval()
        self.version={"model":c["sam2_model"],"commit":c["sam2_revision"],
                      "preprocessing":self.processor.image_processor.to_dict(),
                      "implementation":file_hash(Path(__file__))}
        self.cache=internal(context)/"cache/masks";self.cache.mkdir(parents=True,exist_ok=True)
        self.metrics=Counter();self.artifacts=set()

    def review(self,image,detections):
        if not detections:
            return
        key=stable_id("MASK42",pixel_hash(image),[d["box_xyxy"] for d in detections],self.version)
        path=self.cache/(key+".npz");meta=path.with_suffix(".json")
        masks=None
        if path.exists() and meta.exists():
            try:
                info=read_json(meta)
                if info["key"]==key and info["sha256"]==file_hash(path):
                    with np.load(path,allow_pickle=False) as data:
                        if data["masks"].shape==(len(detections),image.height,image.width):
                            masks=data["masks"].astype(bool)
                    if masks is not None:
                        self.metrics["cache_hits"]+=1
            except (OSError,ValueError,KeyError):
                pass
        if masks is None:
            w,h=image.size
            boxes=[[v*(w if i%2==0 else h) for i,v in enumerate(d["box_xyxy"])] for d in detections]
            inputs=self.processor(images=image,input_boxes=[boxes],return_tensors="pt").to(self.device)
            inputs["pixel_values"]=inputs["pixel_values"].to(self.dtype)
            with self.torch.inference_mode():
                out=self.model(**inputs,multimask_output=False)
            masks=self.processor.post_process_masks(out.pred_masks.cpu(),inputs["original_sizes"].cpu())[0][:,0].numpy().astype(bool)
            self.metrics["image_encoder_forwards"]+=1
            temporary=path.with_name(path.stem+".pending.npz")
            np.savez_compressed(temporary,masks=masks);temporary.replace(path)
            write_json(meta,{"key":key,"sha256":file_hash(path),"model":self.version})
        self.artifacts.update((path,meta))
        for detection,mask in zip(detections,masks):
            box=mask_box(mask)
            detection["mask_evidence_path"]=str(path)
            detection["mask_box_xyxy"]=box
            if box and box_iou(box,detection["box_xyxy"])>.2:
                detection["detector_box_xyxy"]=detection["box_xyxy"]
                detection["box_xyxy"]=box
                detection["localization_refinement"]="SAM2_image"
