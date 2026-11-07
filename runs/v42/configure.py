"""Derive a separate V4.2 registry, runtime manifest and YAML. No training/inference."""
from pathlib import Path
import argparse
import copy
import json
import re
import sys
import yaml
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from materflow.schema import read_json,write_json,file_hash

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--full",action="store_true")
    p.add_argument("--start",type=float,default=75)
    p.add_argument("--seconds",type=float,default=30)
    p.add_argument("--name",default="v42_75_105")
    a=p.parse_args()
    import math
    if not re.fullmatch("[A-Za-z0-9_.-]+",a.name) or a.name in (".",".."):
        p.error("safe output name required")
    if not all(math.isfinite(v) for v in (a.start,a.seconds)) or a.start<0 or a.seconds<=0:
        p.error("positive finite source range required")
    docs=ROOT/"docs/V4.2";docs.mkdir(parents=True,exist_ok=True)
    source=ROOT/"docs/V4.1/example_registry_v4.1.xlsx"
    registry=docs/"registry_v4.2.xlsx"
    if not registry.exists():
        book=load_workbook(source)
        for row in book["Protocol"]:
            if row[0].value=="schema_version":
                row[1].value="ChemFlow-v4.2"
        book.save(registry)
        book.close()
    out=ROOT/("output/clear_video_v4_2" if a.full else "output/benchmarks/"+a.name)
    out.mkdir(parents=True,exist_ok=True)
    if (out/"run_summary.json").exists():
        raise ValueError("Existing run: resume its YAML instead of regenerating inputs")
    manifest=read_json(ROOT/"docs/V4.1/example_manifest_v4.1.json")
    manifest["schema_version"]="ChemFlow-v4.2"
    manifest["run_id"]="MaterFlow_"+("V42_FULL" if a.full else a.name)
    manifest["registry_file"]=registry.relative_to(ROOT).as_posix()
    manifest["capture"]["sources"]=[{"source_id":"clear_video","view":"ego","file":"raw/clear_video.mp4",
                                     "is_continuous_clip":False,"time_scale":None}]
    manifest["status"]={"is_template":False,"human_review_complete":False}
    manifest["outputs"]={"observations":"observations_v4.2.xlsx","events":"events_v4.2.xlsx",
                         "lineage":"lineage_v4.2.xlsx","time_basis":"source_wall_clock"}
    write_json(out/"manifest.json",manifest)
    profile_path=ROOT/"output/benchmarks/v41_75_100/run.yaml"
    profile=yaml.safe_load(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    keep=("model_name model_revision grounding_model grounding_revision sam2_model sam2_revision "
          "dinov2_model dinov2_revision model_cache_dir device quantization bnb_skip_modules "
          "offload_embeddings max_image_pixels max_new_tokens local_files_only attention_backend "
          "sequential_weight_loading gpu_memory_fraction torch_num_threads do_sample temperature top_p "
          "top_k presence_penalty repetition_penalty seed allow_batch_fallback structured_output").split()
    c={k:profile[k] for k in keep if k in profile}
    c.update(model_name="Qwen/Qwen3.5-4B",model_revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
        grounding_model="IDEA-Research/grounding-dino-tiny",grounding_revision="a2bb814dd30d776dcf7e30523b00659f4f141c71",
        sam2_model="facebook/sam2.1-hiera-tiny",sam2_revision="de431c4043854a71d8101e17995dfe596bf101a5",
        dinov2_model="facebook/dinov2-small",dinov2_revision="ed25f3a31f01632728cabb09d1542f84ab7b0056",
        model_cache_dir="models/hub",device="cuda",quantization="nf4",bnb_skip_modules=["model.visual","lm_head"],
        max_image_pixels=262144,max_new_tokens=2048,local_files_only=True,structured_output=True,
        attention_backend="sdpa_efficient",sequential_weight_loading=True,gpu_memory_fraction=.9,
        torch_num_threads=4,do_sample=True,temperature=.7,top_p=.8,top_k=20,presence_penalty=1.5,
        repetition_penalty=1.,seed=42,allow_batch_fallback=True,
        run_id=manifest["run_id"],registry_path=registry.relative_to(ROOT).as_posix(),
        manifest_path=(out/"manifest.json").relative_to(ROOT).as_posix(),
        output_dir=out.relative_to(ROOT).as_posix(),prompt_template_path="configs/prompts/lab_frame_v4_2.template.txt",
        timezone=manifest["collection"]["timezone"],sample_fps=5,
        detector_backend="grounding_dino",tracker_backend="klt",detector_fallback=None,
        detection_idle_fps=1.,detection_active_fps=2.5,track_gap_s=.8,
        scene_interval_s=12.,active_scene_interval_s=4.,semantic_cooldown_s=2.,
        local_motion_threshold=.045,display_change_threshold=.025,semantic_batch_size=2,
        joint_review=True,joint_review_min_gap_s=4.,max_clip_images=3,semantic_budget=None,
        ocr_python=".venv-ocr/Scripts/python.exe",ocr_label_views=3,ocr_display_interval_s=2.,
        ocr_retry_interval_s=8.,ocr_score_threshold=.85,clock_interval_s=1.,
        catalog_path=None,classes_path=None,gallery_index_path=None,custom_detector_weights=None,
        custom_class_map=None,segmentation_backend="off",measurement_resolutions={},
        box_threshold=.3,text_threshold=.25,
        class_thresholds={"balance":.25,"oven":.55,"container":.5,"hand":.35},
        clock_roi=[.78,.93,.997,.985],work_roi=[0,.08,1,1],
        annotated_video=True,roi_reread=False,clip_review=False,chunk_seconds=60.,
        ranges_s=[] if a.full else [[a.start,a.start+a.seconds]])
    (out/"run.yaml").write_text(yaml.safe_dump(c,allow_unicode=True,sort_keys=False),encoding="utf-8")
    write_json(out/"input_provenance.json",{
        "source_registry_sha256":file_hash(source),"derived_registry_sha256":file_hash(registry),
        "actual_video_sha256":file_hash(ROOT/"raw/clear_video.mp4"),
        "classes_source":"existing user-confirmed V4.1 classes; no invented 20-class list",
        "packaging_catalog":"not_configured","custom_detector_weights":"not_supplied",
        "human_reference_used":False,"configuration_generated_only":True})
    print(out/"run.yaml")

if __name__=="__main__":
    main()
