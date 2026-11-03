"""Five explicit disk-backed stages; no inference during derive and no filename cache."""
from dataclasses import asdict
import importlib.metadata
from pathlib import Path
import platform
import re
from .config import load_config
from .schema import FrameRecord, file_hash, read_json, read_jsonl, stable_id, write_json, write_jsonl
from .materials.context import load_run_context
from .materials.prompts import compile_sg_inputs

STAGES = ("prepare", "caption", "ground", "consolidate", "derive")


def _fingerprint(config, context):
    files = {key: file_hash(config[key]) for key in ("registry_path", "manifest_path", "prompt_template_path")}
    if config.get("caption_reuse_path"):
        files["caption_reuse_path"] = file_hash(config["caption_reuse_path"])
    source_stats = {}
    for sid, path in context["source_paths"].items():
        paths = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
        source_stats[sid] = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else (str(p), None, None) for p in paths]
    package = Path(__file__).parent
    files["code"] = stable_id("", [(str(p.relative_to(package)), file_hash(p)) for p in sorted(package.rglob("*.py"))])
    return stable_id("RUN", {k: str(v) if isinstance(v, Path) else v for k, v in config.items()}, files, source_stats)


def _check_stage(summary, name, fingerprint, output):
    item = summary.get("stages", {}).get(name, {})
    if summary.get("fingerprint") != fingerprint or item.get("status") != "complete":
        raise ValueError(f"Missing/stale upstream {name}; run --stage {name} with this configuration")
    for relative, digest in item["artifacts"].items():
        path = output / relative
        if not path.is_file() or file_hash(path) != digest:
            raise ValueError(f"Upstream artifact changed/missing: {path}; rerun --stage {name}")


def process_run(config_path, stage="all"):
    if stage not in (*STAGES, "all"):
        raise ValueError(f"Unknown stage: {stage}")
    config = load_config(config_path)
    context = load_run_context(config)
    output = config["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "run_summary.json"
    fingerprint = _fingerprint(config, context)
    summary = read_json(summary_path) if summary_path.is_file() else {}
    if stage in {"all", "prepare"}:
        summary = {"run_id": config["run_id"], "fingerprint": fingerprint, "config": config, "stages": {},
                   "models": {}, "environment": {"python": platform.python_version()}, "inputs": {}}
        for key in ("torch", "transformers", "av", "openpyxl"):
            try:
                summary["environment"][key] = importlib.metadata.version(key)
            except importlib.metadata.PackageNotFoundError:
                summary["environment"][key] = None
        for key in ["registry_path", "manifest_path", "prompt_template_path"] + (["caption_reuse_path"] if config.get("caption_reuse_path") else []):
            summary["inputs"][key] = {"path": str(config[key]), "sha256": file_hash(config[key])}
    elif summary.get("fingerprint") != fingerprint:
        raise ValueError("Configuration, code or source input changed. Rerun prepare; upstream outputs will not be silently reused.")
    context["model_tag"] = re.sub(r"[^A-Za-z0-9_.-]", "_", config["model_name"])
    context["run_fingerprint"] = fingerprint
    selected = STAGES if stage == "all" else (stage,)
    backend = None
    last_result = None
    for current in selected:
        if current != "prepare":
            _check_stage(summary, STAGES[STAGES.index(current)-1], fingerprint, output)
            context["compiled"] = read_json(output / "context.json")
            context["aliases"] = read_json(output / "annotations/alias_map.json")
            context["scene_prompt"] = (output / "prompts/lab_scene.txt").read_text(encoding="utf-8")
        # Rerunning a stage invalidates downstream completion, even if filenames remain.
        for downstream in STAGES[STAGES.index(current):]:
            summary["stages"].pop(downstream, None)
        summary["stages"][current] = {"status": "running"}
        print(f"Starting {current}: {output}", flush=True)
        write_json(summary_path, summary)
        try:
            artifacts = []
            if current == "prepare":
                from .video.sampling import sample_frames, build_time_windows, closed_job_bounds, select_caption_frames, IMAGE_SUFFIXES
                from .video.reader import inspect_video
                compile_sg_inputs(context, output)
                frames, windows, capture_info = [], [], {}
                for sid, source in context["sources"].items():
                    path = context["source_paths"][sid]
                    if path.is_file() and path.suffix.lower() not in IMAGE_SUFFIXES:
                        capture_info[sid] = inspect_video(path)
                    sampled = sample_frames(context["source_paths"][sid], {**config, **source, "source_id": sid})
                    if sid not in capture_info:
                        capture_info[sid] = {"path": str(path), "kind": "images", "frames": len(sampled)}
                    frames.extend(sampled)
                    windows.extend(build_time_windows(sampled, config))
                write_json(output / "capture_info.json", capture_info)
                write_jsonl(output / "frame_index.jsonl", frames)
                write_json(output / "windows.json", windows)
                caption_frames = select_caption_frames(frames, config)
                write_jsonl(output / "caption_frame_index.jsonl", caption_frames)
                write_json(output / "analysis_policy.json", {
                    "detection_tracking_fps": config["sample_fps"],
                    "caption_fps": config.get("caption_fps") or config["sample_fps"],
                    "detection_frames": len(frames), "caption_frames": len(caption_frames),
                    "caption_frame_ids": {sid: [f.frame_id for f in caption_frames if f.source_id == sid] for sid in context["sources"]},
                    "evidence_policy": "OCR, states and flows only on actual Qwen input frames. Detector-only frames provide physical class/box evidence, never propagated text or material flow.",
                    "human_reference_used": False})
                (output / "input_files/frames.txt").write_text("".join(f"{f.source_id}\t{f.frame_id}\t{f.source_frame_index}\t{f.source_time_s}\t{f.image_path}\n" for f in frames), encoding="utf-8")
                (output / "input_files/windows.txt").write_text("".join(f"{w['source_id']},{closed_job_bounds(w)}\n" for w in windows), encoding="utf-8")
                artifacts = [output / p for p in ("context.json", "annotations/objects.json", "annotations/relations.json", "annotations/alias_map.json", "prompts/lab_scene.txt", "capture_info.json", "frame_index.jsonl", "caption_frame_index.jsonl", "analysis_policy.json", "windows.json", "input_files/videos.txt", "input_files/frames.txt", "input_files/windows.txt")]
                # Images are upstream artifacts too, not just paths in a trusted index.
                artifacts.extend(Path(f.image_path) for f in frames if Path(f.image_path).is_relative_to(output))
            else:
                frames = [FrameRecord(**record) for record in read_jsonl(output / "frame_index.jsonl")]
                windows = read_json(output / "windows.json")
                if current == "caption":
                    from .backends.factory import create_backend
                    from .perception.relations import extract_frame_relations
                    if backend is None:
                        backend = create_backend(config)
                    context.setdefault("model_versions", {})["qwen"] = backend.version
                    caption_frames = [FrameRecord(**record) for record in read_jsonl(output / "caption_frame_index.jsonl")]
                    print(f"Qwen schedule: {len(caption_frames)} frames; detection/tracking: {len(frames)} frames", flush=True)
                    for sid in context["sources"]:
                        context["windows"] = [w for w in windows if w["source_id"] == sid]
                        extract_frame_relations([f for f in caption_frames if f.source_id == sid], backend, context)
                    artifacts = sorted(context.get("caption_artifacts", []))
                elif current == "ground":
                    from .perception.grounding import GroundingDINO, ground_relation_objects
                    detector = GroundingDINO(config)
                    for sid in context["sources"]:
                        captions = read_json(output / "captions" / context["model_tag"] / (sid + ".json"))
                        graphs = ground_relation_objects([f for f in frames if f.source_id == sid], captions, context, detector)
                        path = output / "frame_graphs/v1" / (sid + ".json")
                        write_json(path, graphs)
                        artifacts.append(path)
                    del detector
                elif current == "consolidate":
                    from .tracking.objects import TrackingModels, consolidate_object_tracks, link_window_tracks
                    from .materials.ocr import extract_lab_observations, promote_readings
                    from .materials.identity import resolve_object_identities
                    models = TrackingModels(config)
                    combined = {"tracks": [], "frame_graphs": [], "local_to_track": {}, "detection_to_track": {}}
                    for sid in context["sources"]:
                        context["frames"] = [f for f in frames if f.source_id == sid]
                        context["windows"] = [w for w in windows if w["source_id"] == sid]
                        graphs = read_json(output / "frame_graphs/v1" / (sid + ".json"))
                        tracks = link_window_tracks(consolidate_object_tracks(graphs, context, models), context)
                        path = output / "video_graphs/v1" / (sid + ".json")
                        write_json(path, tracks)
                        artifacts.append(path)
                        combined["tracks"].extend(tracks["tracks"])
                        combined["frame_graphs"].extend(tracks["frame_graphs"])
                        combined["local_to_track"].update(tracks["local_to_track"])
                        combined["detection_to_track"].update(tracks["detection_to_track"])
                    del models
                    import gc
                    import sys
                    gc.collect()
                    if "torch" in sys.modules and sys.modules["torch"].cuda.is_available():
                        sys.modules["torch"].cuda.empty_cache()
                    if config["roi_reread"] and backend is None:
                        # Lazily create only if an actual unclear ROI requires another request.
                        class LazyBackend:
                            def infer_images(self, images, prompt, **kwargs):
                                nonlocal backend
                                from .backends.factory import create_backend
                                if backend is None:
                                    backend = create_backend(config)
                                    context.setdefault("model_versions", {})["qwen"] = backend.version
                                return backend.infer_images(images, prompt, **kwargs)
                        roi_backend = LazyBackend()
                    else:
                        roi_backend = backend
                    observations = extract_lab_observations(frames, combined, roi_backend, context)
                    identities = resolve_object_identities(combined, observations, context)
                    observations = promote_readings(observations, context)
                    write_jsonl(output / "observations.jsonl", observations)
                    write_json(output / "identities.json", identities)
                    write_json(output / "visual_evidence_index.json", context.get("evidence_index", {}))
                    write_json(output / "evidence_index.json", context.get("evidence_index", {}))
                    artifacts += [output / p for p in ("observations.jsonl", "identities.json", "visual_evidence_index.json")]
                    artifacts += context.get("crop_artifacts", [])
                elif current == "derive":
                    from .materials.events import propose_lab_events, resolve_event_candidates
                    from .materials.state import check_event_state_consistency
                    from .materials.lineage import reconstruct_lineage
                    from .materials.sop import align_events_to_sop
                    from .outputs.export import assemble_run_result, export_run_result
                    observations = read_jsonl(output / "observations.jsonl")
                    context["evidence_index"] = read_json(output / "visual_evidence_index.json")
                    context["identities"] = read_json(output / "identities.json")
                    events = resolve_event_candidates(propose_lab_events(observations, context), observations, context)
                    check_event_state_consistency(events, context)
                    align_events_to_sop(events, context)
                    lineage = reconstruct_lineage(events, observations, context)
                    last_result = assemble_run_result(events, observations, lineage)
                    artifacts = [Path(p) for p in export_run_result(last_result, context)]
                    artifacts.append(output / "derived.json")
                    if config.get("annotated_video"):
                        from .outputs.video import render_annotated_videos
                        artifacts.extend(render_annotated_videos(last_result, frames, context))
            summary["models"].update(context.get("model_versions", {}))
            summary["stages"][current] = {"status": "complete", "artifacts": {str(p.relative_to(output)): file_hash(p) for p in artifacts}}
            summary["error"] = None
        except Exception as error:
            # Error reporting only. Never manufacture empty graphs or observations on failure.
            summary["stages"][current] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
            write_json(summary_path, summary)
            raise
        write_json(summary_path, summary)
        print(f"Completed {current}", flush=True)
    return last_result or summary
