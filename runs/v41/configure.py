"""Generate an actual manifest/YAML; never modify the reference workbooks."""
from pathlib import Path
import argparse,json,sys,copy,re,math
import yaml
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.schema import read_json,write_json,file_hash

def main():
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group();mode.add_argument("--benchmark",action="store_true");mode.add_argument("--full",action="store_true");parser.add_argument("--start",type=float,default=75);parser.add_argument("--seconds",type=float,default=25);parser.add_argument("--name",default="v41_75_100");args=parser.parse_args()
    if not math.isfinite(args.start) or not math.isfinite(args.seconds) or args.start<0 or args.seconds<=0:parser.error("positive finite time range required")
    if args.name in (".","..") or not re.fullmatch(r"[A-Za-z0-9_.-]+",args.name):parser.error("name must be one safe directory component")
    output=ROOT/("output/benchmarks/"+args.name if args.benchmark else "output/clear_video_v4_1");output.mkdir(parents=True,exist_ok=True)
    manifest=read_json(ROOT/"docs/V4.1/example_manifest_v4.1.json")
    manifest["run_id"]=manifest["run_id"]+("_"+args.name if args.benchmark else "_V41")
    manifest["registry_file"]="docs/V4.1/example_registry_v4.1.xlsx";manifest["capture"]["sources"]=[{"source_id":"clear_video","view":"ego","file":"raw/clear_video.mp4","time_scale":None,"is_continuous_clip":False}]
    manifest["status"]={"is_template":False,"human_review_complete":False};write_json(output/"manifest.json",manifest)
    base=yaml.safe_load((ROOT/"output/clear_video/run.yaml").read_text(encoding="utf-8")) if (ROOT/"output/clear_video/run.yaml").is_file() else {}
    allowed="model_name model_revision grounding_model grounding_revision sam2_model sam2_revision dinov2_model dinov2_revision model_cache_dir device quantization bnb_skip_modules offload_embeddings max_image_pixels max_new_tokens local_files_only attention_backend sequential_weight_loading gpu_memory_fraction torch_num_threads do_sample temperature top_p top_k presence_penalty repetition_penalty seed caption_batch_size allow_batch_fallback structured_output".split()
    c={k:base[k] for k in allowed if k in base}
    portable={"model_cache_dir":"models/hub","quantization":"nf4","bnb_skip_modules":["model.visual","lm_head"],"offload_embeddings":False,"max_image_pixels":262144,"max_new_tokens":2048,"local_files_only":True,"attention_backend":"sdpa_efficient","sequential_weight_loading":True,"gpu_memory_fraction":.9,"torch_num_threads":4,"do_sample":True,"temperature":.7,"top_p":.8,"top_k":20,"presence_penalty":1.5,"repetition_penalty":1.,"seed":42,"caption_batch_size":4,"allow_batch_fallback":True,"structured_output":True}
    for key,value in portable.items():c.setdefault(key,value)
    c.update(model_name="Qwen/Qwen3.5-4B",model_revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",grounding_model="IDEA-Research/grounding-dino-tiny",grounding_revision="a2bb814dd30d776dcf7e30523b00659f4f141c71",sam2_model="facebook/sam2.1-hiera-tiny",sam2_revision="de431c4043854a71d8101e17995dfe596bf101a5",dinov2_model="facebook/dinov2-small",dinov2_revision="ed25f3a31f01632728cabb09d1542f84ab7b0056")
    c.update(run_id=manifest["run_id"],registry_path="docs/V4.1/example_registry_v4.1.xlsx",manifest_path=(output/"manifest.json").relative_to(ROOT).as_posix(),output_dir=output.relative_to(ROOT).as_posix(),prompt_template_path="configs/prompts/lab_frame_v4_1.template.txt",timezone=manifest["collection"]["timezone"],sample_fps=5,detection_fps=5,caption_interval_s=2.5,caption_active_fps=1,motion_threshold=.075,cut_threshold=.38,clock_roi=[.78,.93,.997,.985],work_roi=[0,.1,1,1],box_threshold=.4,text_threshold=.25,class_thresholds={"oven":.55,"container":.45},conflict_iou=.9,conflict_margin=.04,track_chunk_frames=40,max_track_gap_s=1.,appearance_threshold=.72,relevance_policy="interaction_readout_foreground",annotated_video=True,roi_reread=True,clip_review=False,caption_reuse_path=None,ranges_s=[[args.start,args.start+args.seconds]] if args.benchmark else [])
    c.setdefault("device","cuda");c.setdefault("quantization","nf4");c.setdefault("structured_output",True);c.setdefault("attention_backend","sdpa_efficient");c.setdefault("local_files_only",True)
    (output/"run.yaml").write_text(yaml.safe_dump(c,sort_keys=False,allow_unicode=True),encoding="utf-8")
    write_json(output/"input_provenance.json",{"registry_sha256":file_hash(ROOT/c["registry_path"]),"manifest_template_sha256":file_hash(ROOT/"docs/V4.1/example_manifest_v4.1.json"),"actual_manifest_sha256":file_hash(output/"manifest.json"),"human_reference_used":False,"actions_policy":"latest user request: stir/transfer/hold/filter","audio_policy":"latest user request: no audio","profile":c})
    print(output/"run.yaml")
if __name__=="__main__":main()
