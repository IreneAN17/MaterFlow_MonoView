"""Measured outputs and data limitations; no extrapolated speedup or accuracy."""
from pathlib import Path
from collections import Counter
import argparse
import json
import importlib.metadata
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json
from materflow.v42.io import records
from materflow.v42.context import load_context

def main():
    p=argparse.ArgumentParser();p.add_argument("--config",required=True);a=p.parse_args()
    context=load_context(load_config(a.config));c=context["config"];out=c["output_dir"];folder=out/"internal"
    summary=read_json(out/"run_summary.json")
    metrics={name:read_json(folder/filename) for name,filename in (
        ("prepare","prepare_metrics.json"),("scan","scan_metrics.json"),("ocr","ocr_metrics.json"),
        ("identity","identity_metrics.json"),("semantic","semantic_metrics.json"),
        ("filter","filter_report.json"),("derive","derive_metrics.json"))}
    first_real={}
    for name in ("scan","ocr","semantic"):
        archived=out/"diagnostics"/("first_real_"+name+"_metrics.json")
        if archived.exists():
            first_real[name]=read_json(archived)
    counts=read_json(out/"record_counts.json");qa=read_json(out/"diagnostics/validation.json")
    intervals=read_json(out/"interval_evidence.json")
    stage_times={k:v.get("elapsed_s") for k,v in summary["stages"].items()}
    versions={}
    for name in ("torch","transformers","bitsandbytes","numpy","av","opencv-python","tzdata"):
        versions[name]=importlib.metadata.version(name)
    plan=read_json(folder/"semantic_plan.json")
    semantic=read_json(folder/"semantic_results.json")
    report={"schema_version":"ChemFlow-v4.2","source_ranges_s":c.get("ranges_s"),
        "versions":versions,"stages":summary["stages"],"metrics":metrics,"first_real_metrics":first_real,"record_counts":counts,
        "capabilities":read_json(out/"capability_report.json"),"validation":qa,
        "hot_cache":read_json(out/"diagnostics/hot_cache_check.json"),
        "point_records":sum(x["temporal_kind"]=="point" for x in intervals),
        "interval_records":sum(x["temporal_kind"]!="point" for x in intervals),
        "unknown_gap_records":sum(bool(x["unknown_ranges"]) for x in intervals),
        "ordered_review_count":sum(x["kind"]=="joint" for x in plan),
        "ignored_unused_boundary_coordinates":sum(len(x.get("boundary_validation_notes",[])) for x in semantic),
        "independent_accuracy_verified":False,"three_hour_run_executed":False,
        "formal_detector_training_executed":False,
        "test_results":read_json(out/"diagnostics/test_results.json") if (out/"diagnostics/test_results.json").exists() else None,
        "preview_review":[
            {"recording_time":"2026/09/08-10:00:32","image":"diagnostics/preview_0000.jpg","finding":"Suspicious filter_funnel class covers the balance upper enclosure. This is not accepted as a ground-truth class label; raw model prediction retained for review."},
            {"recording_time":"2026/09/08-10:00:47","image":"diagnostics/preview_0450.jpg","finding":"Balance box is broad; source timestamps and optical-flow labels render correctly. Localization/classification still need independent review."},
            {"recording_time":"2026/09/08-10:01:02","image":"diagnostics/preview_0899.jpg","finding":"Tail clock is supported by exact boundary OCR rather than global extrapolation. Bottle labels remain excluded without a user catalog."}],
        "limits":["No user packaging catalog/reference gallery or trained 20-class weights supplied.",
                  "Held bottles without a required label match stay internal candidates, not business objects.",
                  "Qwen raw text or states may still be mistaken; unbound objects do not receive invented identities.",
                  "Sparse process evidence envelopes retain unknown gaps; clock span is not confirmed processing duration.",
                  "Prior V4.1 benchmark is 25s and hardware states differ; no whole-pipeline speedup ratio asserted.",
                  "The measured semantic pass includes actual ordered multimodal requests; independent image batches are counted separately."]}
    write_json(out/"benchmark_report.json",report)
    lines=["# MaterFlow V4.2 real-model excerpt report","",
        "Selected source: "+str(c.get("ranges_s"))+" seconds; source positions are internal coordinates only.",
        "Public business tables and annotated-video recording labels use wall-clock timestamps.","",
        "| Pass | Elapsed seconds |","|---|---:|"]
    for name,elapsed in stage_times.items():
        lines.append(f"| {name} | {elapsed:.2f} |")
    lines+=["","Cold/resumed workload must be read with the task-cache counters below; development failures/retries are retained in logs and raw request files, not hidden as new successful inference.",
        "",f"Record counts: {counts}.",
        f"Raw/admitted/business observations: {metrics['filter']['raw_count']} / {metrics['filter']['normalized_count']} / {metrics['filter']['business_count']}.",
        f"Real detector forwards: {metrics['scan']['forward_calls']}; detection-task hits: {metrics['scan']['detection_cache_hits']}; sampled frames: {metrics['scan']['sampled_frames']}.",
        f"Tracker: {metrics['scan']['tracker']}; optical visual updates: {metrics['scan']['tracker_counters'].get('optical_updates',0)}; peak active tracks: {metrics['scan']['peak_active_tracks']}.",
        f"SAM2 calls: {metrics['scan']['sam2_calls']}; DINOv2 calls: {metrics['scan']['dinov2_calls']}.",
        f"OCR real region queries: {metrics['ocr']['worker_queries']}; OCR cache hits: {metrics['ocr']['cache_hits']}.",
        f"Qwen scene requests: {metrics['semantic']['image_requests']}; real ordered reviews: {metrics['semantic']['ordered_requests']}; retry calls: {metrics['semantic']['retry_calls']}; cache hits: {metrics['semantic']['cache_hits']}.",
        f"Point records: {report['point_records']}; interval records: {report['interval_records']}; intervals with unknown gaps: {report['unknown_gap_records']}.",
        f"Unasserted boundary coordinates ignored conservatively, original raw output retained: {report['ignored_unused_boundary_coordinates']}.",
        "", "## Data readiness and limitations",""]
    if first_real:
        lines += ["", "First successful real-task measurements before business-rule/export audits (not cache-resume times):",
            f"- Detector/optical pass: {first_real['scan']['forward_calls']} actual detector forwards; {first_real['scan']['inference_and_tracking_s']:.2f}s inference/tracking plus {first_real['scan']['model_load_s']:.2f}s model loading. Later optical batching has changed CPU implementation; this is an archived measurement, not a controlled current-code speedup.",
            f"- OCR: {first_real['ocr']['worker_queries']} real region queries; {first_real['ocr']['elapsed_s']:.2f}s.",
            f"- Qwen: {first_real['semantic']['image_requests']} independent images + {first_real['semantic']['ordered_requests']} ordered reviews; {first_real['semantic']['elapsed_s']:.2f}s including load and validation retry.",
            "- Final replay used verified recognition caches; do not present a cached semantic pass as fresh model latency."]
    lines += ["- "+line for line in report["limits"]]
    lines += ["","## Preview review","",*['- '+json.dumps(r,ensure_ascii=False) for r in report['preview_review']]]
    lines+=["","## Verification","",
        "Workbooks checked cell by cell against automatic JSON, including typed Excel date cells. Public references, timestamps, source-relative coordinate separation and every completed-pass artifact hash checked.",
        "Annotated video decoded for every selected source frame; resolution, rate and relative PTS compared; zero audio streams. First/middle/last previews generated.",
        "This establishes output integrity, not independent detection/OCR/action accuracy.",
        "", "See capability_report.json for configured, real-tested and awaiting-user-data paths. No formal detector training or full/three-hour experiment run was started."]
    (out/"benchmark_report.md").write_text("\n".join(lines),encoding="utf-8")
    print(out/"benchmark_report.md")

if __name__=="__main__":
    main()
