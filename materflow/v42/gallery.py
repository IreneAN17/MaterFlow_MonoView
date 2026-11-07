"""Optional real DINOv2 retrieval. No reference images means no model is loaded."""
from pathlib import Path
import importlib.metadata
import numpy as np
from PIL import Image
from ..schema import read_json,write_json,file_hash,stable_id

class Encoder:
    def __init__(self,c):
        import torch
        from transformers import AutoImageProcessor,Dinov2Model
        self.torch=torch;self.device=c["reference_device"]
        kw={"revision":c["dinov2_revision"],"local_files_only":c["local_files_only"],
            "cache_dir":c.get("model_cache_dir")}
        self.processor=AutoImageProcessor.from_pretrained(c["dinov2_model"],**kw)
        self.model=Dinov2Model.from_pretrained(c["dinov2_model"],**kw).to(self.device).eval()
        self.version={"model":c["dinov2_model"],"commit":c["dinov2_revision"],
                      "transformers":importlib.metadata.version("transformers"),
                      "preprocessing":self.processor.to_dict()}
    def encode(self,paths):
        features=[]
        for start in range(0,len(paths),8):
            images=[]
            for path in paths[start:start+8]:
                with Image.open(path) as im:
                    images.append(im.convert("RGB"))
            inputs=self.processor(images=images,return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                f=self.model(**inputs).last_hidden_state[:,0].float().cpu().numpy()
            f/=np.maximum(np.linalg.norm(f,axis=1,keepdims=True),1e-8)
            features.extend(f.tolist())
        return features

def build_index(context,catalog,path):
    refs=[]
    for item in catalog:
        for rel in item["reference_image_paths"]:
            p=(context["config"]["repo_root"]/rel).resolve()
            if not p.is_file():
                raise FileNotFoundError(p)
            refs.append({"packaging_catalog_id":item["packaging_catalog_id"],"path":str(p),
                         "sha256":file_hash(p),"reference_group_id":item.get("reference_group_id")})
    if not refs:
        write_json(path,{"status":"not_configured","references":[]})
        return
    encoder=Encoder(context["config"])
    for row,feature in zip(refs,encoder.encode([r["path"] for r in refs])):
        row["feature"]=feature
    write_json(path,{"status":"ready","model":encoder.version,"references":refs})

def match_queries(context,groups,catalog):
    index=read_json(context["config"]["gallery_index_path"])
    if index.get("status")!="ready" or not index.get("references"):
        return {}
    known={r["packaging_catalog_id"] for r in catalog}
    if any(r["packaging_catalog_id"] not in known for r in index["references"]):
        raise ValueError("Reference gallery catalog IDs mismatch")
    for row in index["references"]:
        if file_hash(row["path"])!=row["sha256"]:
            raise ValueError("Reference image changed since indexing")
    encoder=Encoder(context["config"])
    if encoder.version!=index["model"]:
        raise ValueError("Gallery model/preprocessing differs")
    tids=[tid for tid,rows in groups.items() if rows]
    images=[groups[tid][0]["crop_path"] for tid in tids]
    matrix=np.asarray([r["feature"] for r in index["references"]])
    output={}
    for tid,feature in zip(tids,encoder.encode(images)):
        similarities=matrix@np.asarray(feature)
        scores={}
        for row,score in zip(index["references"],similarities):
            k=row["packaging_catalog_id"];scores[k]=max(scores.get(k,-1),float(score))
        output[tid]=[{"packaging_catalog_id":k,"cosine_similarity":v,
                      "decision":"ranking_only_not_identity"} for k,v in sorted(scores.items(),key=lambda x:-x[1])]
        ranking=output[tid]
        if ranking:
            margin=ranking[0]["cosine_similarity"]-ranking[1]["cosine_similarity"] if len(ranking)>1 else None
            ranking[0]["runner_up_margin"]=margin
            ranking[0]["visual_ambiguity"]="unopposed_reference_only" if margin is None else "ambiguous" if margin<context["config"]["catalog_match_margin"] else "separated_candidates_not_calibrated_identity"
    return output
