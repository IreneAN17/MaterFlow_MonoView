"""Create this run from explicit registry/manifest inputs plus the verified GPU profile."""
from pathlib import Path
import json,sys,hashlib
import yaml
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.materials.context import load_run_context
from materflow.schema import file_hash,write_json
registry=Path("Video_sample/Clear Video/clear_video_registry_v4.0.xlsx")
manifest=Path("Video_sample/Clear Video/clear_video_manifest_v4.0.json")
m=json.loads((ROOT/manifest).read_text(encoding="utf-8-sig"))
output=ROOT/"output/clear_video"
output.mkdir(parents=True,exist_ok=True)
c={"run_id":m["run_id"],"registry_path":registry.as_posix(),"manifest_path":manifest.as_posix(),"output_dir":"output/clear_video",
   "model_name":"Qwen/Qwen3.5-4B","model_revision":"851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
   "grounding_model":"IDEA-Research/grounding-dino-tiny","grounding_revision":"a2bb814dd30d776dcf7e30523b00659f4f141c71",
   "sam2_model":"facebook/sam2.1-hiera-tiny","sam2_revision":"de431c4043854a71d8101e17995dfe596bf101a5",
   "dinov2_model":"facebook/dinov2-small","dinov2_revision":"ed25f3a31f01632728cabb09d1542f84ab7b0056",
   "model_cache_dir":"models/hub","sample_fps":5,"caption_fps":1,"window_frames":10,"window_stride":8,
   "device":"cuda","quantization":"nf4","bnb_skip_modules":["model.visual","lm_head"],"offload_embeddings":False,
   "max_image_pixels":262144,"max_new_tokens":2048,"local_files_only":True,
   "annotated_video":True,"roi_reread":True,"clip_review":True,
   "attention_backend":"sdpa_efficient","sequential_weight_loading":True,"gpu_memory_fraction":.9,"torch_num_threads":4,
   "do_sample":True,"temperature":.7,"top_p":.8,"top_k":20,"presence_penalty":1.5,"repetition_penalty":1.,"seed":42,
   "caption_batch_size":4,"allow_batch_fallback":True,"structured_output":True}
for relative in ("diagnostics/sparse_before_unicode_fix/reuse_manifest.json", "diagnostics/sparse_before_wire_compat/reuse_manifest.json", "diagnostics/5fps_before_sparse/reuse_manifest.json"):
    reuse=output/relative
    if reuse.is_file():
        c["caption_reuse_path"]=reuse.relative_to(ROOT).as_posix()
        break
path=output/"run.yaml"
path.write_text("# Generated from the explicit registry and manifest; model/sampling values are the local inference profile.\n"+yaml.safe_dump(c,sort_keys=False,allow_unicode=True),encoding="utf8")
context=load_run_context(load_config(path))
write_json(output/"input_provenance.json",{
    "generated_from":{str(p.as_posix()):file_hash(ROOT/p) for p in (registry,manifest)},
    "run_id_source":"manifest.run_id","video_sources":list(context["sources"].values()),
    "human_reference_used":False,
    "human_reference_policy":"Human observation/event/lineage rows are not read or used for inference.",
    "inference_profile":c,
    "weight_policy":"Official pretrained checkpoints at pinned commits; no training or fabricated outputs.",
    "sampling_selection":"User explicitly selected 1fps Qwen and 5fps detection/tracking.",
    "model_selection":"User explicitly selected 4B to complete the entire pipeline; prior 9B outputs are diagnostic only."})
print(path)
