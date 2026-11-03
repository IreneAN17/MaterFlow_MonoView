"""Stage 1: five-array visual records, plus a lossless SG-Ego triplet projection."""
from collections import Counter
from pathlib import Path
import copy
import importlib.metadata
from dataclasses import asdict
import json
import re
import time
from ..schema import normalize, validate_box, write_json, read_json, file_hash, stable_id

ARRAYS = ("objects", "relations", "texts", "flows", "states")


def scene_response_schema():
    """Type constraints only; no required observations, object counts or identities."""
    string = {"type": "string"}
    nullable_string = {"anyOf": [string, {"type": "null"}]}
    number = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    local = {"type": "string", "pattern": "^obj_[1-9][0-9]*$"}
    region = {"anyOf": [{"type": "null"}, {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 4, "maxItems": 4}]}
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    def arr(properties):
        return {"type": "array", "items": obj(properties)}
    return obj({
        "objects": arr({"local_id": local, "object_class": nullable_string, "description": string, "region_hint_xyxy": region}),
        "relations": arr({"subject": {"type": "string", "pattern": "^(obj_[1-9][0-9]*|camera_wearer)$"}, "relation": {"type": "string", "enum": ["hold", "in", "on"]}, "object": local}),
        "texts": arr({"object": local, "kind": {"type": "string", "enum": ["label", "display", "unknown"]}, "raw_text": string,
                      "value": number, "unit": nullable_string, "reading_kind": {"type": "string", "enum": ["setpoint", "display", "measured", "unknown"]}, "region_hint_xyxy": region}),
        "flows": arr({"source": {"anyOf": [local, {"type": "null"}]}, "target": {"anyOf": [local, {"type": "null"}]},
                      "material_form": {"type": "string", "enum": ["liquid", "solid", "suspension", "unknown"]}, "arrival_visible": {"type": "boolean"}}),
        "states": arr({"object": local, "state": string, "detail": string})})


def decode_model_json(raw_output):
    """Accept one JSON value or an exact enclosing JSON code fence, never repairs."""
    envelope = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```\s*", raw_output, re.IGNORECASE)
    body = envelope.group(1) if envelope else raw_output
    reject_constant = lambda value: (_ for _ in ()).throw(ValueError(f"Invalid number: {value}"))
    try:
        return json.loads(body, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        # Some model tokenizers emit literal label line breaks/tabs in quotes.
        # Python's compatibility mode retains every character; it neither inserts
        # tokens nor fixes missing quotes/brackets/references. Reject other C0 bytes.
        if not error.msg.startswith("Invalid control character") or any(ord(c) < 32 and c not in "\t\n\r" for c in body):
            raise
        return json.loads(body, strict=False, parse_constant=reject_constant)


def parse_relation_output(raw_output, context=None):
    data = decode_model_json(raw_output)
    if not isinstance(data, dict) or set(data) != set(ARRAYS) or any(not isinstance(data[key], list) for key in ARRAYS):
        raise ValueError("Stage 1 requires exactly objects/relations/texts/flows/states arrays")
    for key in ARRAYS:
        if any(not isinstance(item, dict) for item in data[key]):
            raise ValueError(f"{key} elements must be objects")
    aliases = context.get("aliases", {}) if context else {}
    local_ids, refs, counts = set(), {}, Counter()
    unresolved = []
    for obj in data["objects"]:
        local = obj.get("local_id")
        if not isinstance(local, str) or not re.fullmatch(r"obj_[1-9][0-9]*", local) or local in local_ids:
            raise ValueError(f"Invalid/duplicate local_id: {local}")
        local_ids.add(local)
        if not isinstance(obj.get("description"), str):
            raise ValueError(f"Missing description: {local}")
        if obj.get("region_hint_xyxy") is not None:
            validate_box(obj["region_hint_xyxy"])
        label = obj.get("object_class")
        if label is not None and not isinstance(label, str):
            raise ValueError("object_class must be string or null")
        candidates = aliases.get("object_class", {}).get(normalize(label), []) if label is not None else []
        canonical = candidates[0] if len(candidates) == 1 else (label if context is None else None)
        obj["object_class_raw"], obj["object_class"] = label, canonical
        obj["classification_status"] = "supported" if canonical else "unresolved"
        counts[canonical or "object"] += 1
        refs[local] = f"{canonical or 'object'}_{counts[canonical or 'object']}"
    refs["camera_wearer"] = "human_1"

    def check_ref(value, nullable=False):
        if nullable and value is None:
            return
        if value not in local_ids and value != "camera_wearer":
            raise ValueError(f"Unresolved local object reference: {value}")

    triplets = []
    for relation in data["relations"]:
        check_ref(relation.get("subject"))
        check_ref(relation.get("object"))
        raw = relation.get("relation")
        candidates = aliases.get("scene_relation", {}).get(normalize(raw), [])
        canonical = candidates[0] if len(candidates) == 1 else (raw if context is None else None)
        if canonical == "flow into":
            raise ValueError("flow into belongs only in flows, never duplicate it in relations")
        if canonical not in {"hold", "in", "on"}:
            unresolved.append({"relation_raw": raw, "field_status": "unresolved"})
            relation["relation_raw"], relation["relation"] = raw, None
        else:
            relation["relation"] = canonical
            triplets.append([refs[relation["subject"]], canonical, refs[relation["object"]]])
    for text in data["texts"]:
        check_ref(text.get("object"))
        if not isinstance(text.get("raw_text"), str) or text.get("kind") not in {"label", "display", "unknown"}:
            raise ValueError("texts require raw_text and label/display/unknown kind")
        if text.get("reading_kind") not in {"setpoint", "display", "measured", "unknown"}:
            raise ValueError("Invalid reading_kind")
        if text.get("value") is not None and (isinstance(text["value"], bool) or not isinstance(text["value"], (int, float))):
            raise ValueError("text.value must be number or null")
        if text.get("unit") is not None and not isinstance(text["unit"], str):
            raise ValueError("text.unit must be string or null")
        if text.get("region_hint_xyxy") is not None:
            validate_box(text["region_hint_xyxy"])
    for flow in data["flows"]:
        check_ref(flow.get("source"), True)
        check_ref(flow.get("target"), True)
        if not isinstance(flow.get("arrival_visible"), bool) or flow.get("material_form") not in {"liquid", "solid", "suspension", "unknown"}:
            raise ValueError("flows require boolean arrival_visible and valid material_form")
        if flow["source"] and flow["source"] == flow["target"]:
            raise ValueError("A flow cannot enter its own source object")
        if flow["arrival_visible"] and flow["source"] and flow["target"]:
            triplets.append([refs[flow["source"]], "flow into", refs[flow["target"]]])
    for state in data["states"]:
        check_ref(state.get("object"))
        if not isinstance(state.get("state"), str) or not isinstance(state.get("detail"), str):
            raise ValueError("states require state and detail strings")
    return {**data, "parsed": triplets, "local_to_class_ref": refs, "unresolved": unresolved}


def _load_caption_reuse(context, backend):
    path = context["config"].get("caption_reuse_path")
    if not path:
        return None
    config, path = context["config"], Path(path)
    meta = read_json(path)
    root = config["repo_root"]
    entries_path = root / meta["entries_file"]
    if file_hash(entries_path) != meta["entries_sha256"]:
        raise ValueError("Caption import file changed")
    if meta["model_version"] != getattr(backend, "version", {}):
        raise ValueError("Caption import model or generation parameters differ")
    for key, digest in meta["input_hashes"].items():
        if file_hash(config[key]) != digest:
            raise ValueError(f"Caption import input changed: {key}")
    for relative, digest in meta["backend_files"].items():
        if file_hash(root / relative) != digest:
            raise ValueError(f"Caption import backend changed: {relative}")
    for package, version in meta["environment"].items():
        if importlib.metadata.version(package) != version:
            raise ValueError(f"Caption import environment changed: {package}")
    if stable_id("SCHEMA", scene_response_schema()) != meta["schema_fingerprint"]:
        raise ValueError("Caption import output schema changed")
    output = config["output_dir"]
    if file_hash(output / "prompts/lab_scene.txt") != meta["scene_prompt_sha256"] or file_hash(output / "annotations/alias_map.json") != meta["aliases_sha256"]:
        raise ValueError("Caption import compiled prompt or aliases changed")
    meta["path"] = str(path)
    meta["entries"] = {(e["frame"]["source_id"], e["frame"]["frame_id"]): e
                       for e in read_json(entries_path) if e.get("response") and not e.get("clip_window")}
    return meta


def _reuse_caption(frame, prompt, signature, image_digest, context, backend, imported):
    if imported is None:
        return None
    old = imported["entries"].get((frame.source_id, frame.frame_id))
    if not old or old["frame"] != asdict(frame):
        return None
    expected = stable_id("REQ", imported["previous_run_fingerprint"], prompt, image_digest, backend.version)
    if old.get("request_signature") != expected:
        return None
    parsed = parse_relation_output(old["raw"], context)
    if parsed != old["response"]:
        raise ValueError(f"Caption import parse changed at frame {frame.frame_id}")
    entry = copy.deepcopy(old)
    entry["response"] = parsed
    entry["request_signature"] = signature
    entry["reuse_provenance"] = {"source_manifest": imported["path"],
        "source_entries_sha256": imported["entries_sha256"], "original_request_signature": expected,
        "original_run_fingerprint": imported["previous_run_fingerprint"],
        "verified_unchanged": ["frame", "image_sha256", "prompt", "model", "dependencies", "schema", "aliases", "parsed_response"],
        "backend_compatibility": imported.get("backend_compatibility")}
    return entry


def extract_frame_relations(frames, backend, context):
    output = context["config"]["output_dir"] / "captions" / context["model_tag"]
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    path = output / f"{frames[0].source_id}.json" if frames else None
    saved = {e["frame"]["frame_id"]: e for e in read_json(path) if not e.get("clip_window")} if path and path.is_file() else {}
    imported = _load_caption_reuse(context, backend)
    batch_size = context["config"].get("caption_batch_size", 1)
    pending = []
    def flush():
        if not pending:
            return
        started = time.monotonic()
        prompts = [item[1] for item in pending]
        raws = backend.infer_images([item[0].image_path for item in pending],
                                    prompts[0] if len(pending) == 1 else prompts, response_schema=scene_response_schema())
        if len(raws) != len(pending):
            raise ValueError("Backend returned wrong number of independent responses")
        # Persist every raw result before validating any response in this batch.
        for (_, _, entry), raw in zip(pending, raws):
            entry.update(raw=raw, attempt=1)
            entry["raw_attempts"].append(raw)
        write_json(path, entries)
        for frame, prompt, entry in pending:
            for attempt in range(2):
                try:
                    entry["response"] = parse_relation_output(entry["raw"], context)
                    entry.pop("parse_error", None)
                    break
                except (ValueError, TypeError, KeyError) as error:
                    entry["parse_error"] = str(error)
                    write_json(path, entries)
                    if attempt:
                        raise ValueError(f"Stage 1 {frame.source_id}/frame {frame.frame_id}: {error}; both raw attempts saved at {path}") from error
                    retry = prompt + f"\n上次响应结构校验失败：{error}。重新观察同一图像，严格输出合法紧凑 JSON；五数组齐全，引用须对应 objects，坐标为0到1归一化xyxy，不重复列举同一实物，不输出解释或推理。"
                    raw = backend.infer_images([frame.image_path], retry, response_schema=scene_response_schema())[0]
                    entry.update(raw=raw, attempt=2)
                    entry["raw_attempts"].append(raw)
                    write_json(path, entries)
            entry["batch_elapsed_s"] = time.monotonic() - started
            write_json(path, entries)
            print(f"caption {frame.source_id}: validated frame {frame.frame_id}, {len(entries)}/{len(frames)} scheduled, t={frame.source_time_s}, batch={len(pending)}, {entry['batch_elapsed_s']:.1f}s", flush=True)
        pending.clear()

    for caption_index, frame in enumerate(frames):
        prompt = context["scene_prompt"] + f"\n程序元信息（原样附回由程序完成）：source_id={frame.source_id}, frame_id={frame.frame_id}, source_time_s={frame.source_time_s}"
        image_digest = file_hash(frame.image_path)
        signature = stable_id("REQ", context.get("run_fingerprint"), prompt, image_digest,
                              getattr(backend, "version", {}))
        previous = saved.get(frame.frame_id, {})
        if previous.get("request_signature") == signature and previous.get("response"):
            flush()
            entries.append(previous)
            context.setdefault("caption_artifacts", set()).add(path)
            print(f"caption {frame.source_id}: reused validated frame {frame.frame_id}", flush=True)
            continue
        verified_import = _reuse_caption(frame, prompt, signature, image_digest, context, backend, imported)
        if verified_import is not None:
            flush()
            entries.append(verified_import)
            write_json(path, entries)
            context.setdefault("caption_artifacts", set()).add(path)
            print(f"caption {frame.source_id}: imported verified frame {frame.frame_id}, {caption_index+1}/{len(frames)}", flush=True)
            continue
        entry = {"frame": asdict(frame), "request_signature": signature, "raw_attempts": [],
                 "request_id": stable_id("Q", frame.source_id, frame.frame_id)}
        entries.append(entry)
        context.setdefault("caption_artifacts", set()).add(path)
        pending.append((frame, prompt, entry))
        if len(pending) >= batch_size:
            flush()
    flush()
    if path is not None:
        write_json(path, entries)
    # Only ambiguous key windows receive a joint ordered-image request.
    if context["config"]["clip_review"]:
        reviewed = 0
        by_id = {entry["frame"]["frame_id"]: entry for entry in entries}
        for window in context.get("windows", []):
            selected_ids = [i for i in window["frame_ids"] if i in by_id]
            selected = [by_id[i] for i in selected_ids]
            if reviewed >= context["config"]["max_clip_reviews_per_source"]:
                break
            ambiguous = any(any(s["state"] == "container_tilted" for s in e["response"]["states"]) or
                            any(not f["arrival_visible"] for f in e["response"]["flows"]) for e in selected)
            times = [entry["frame"]["source_time_s"] for entry in selected]
            source = context["sources"][window["source_id"]]
            if not ambiguous or len(selected) < 2 or source.get("is_continuous_clip") is not True or any(t is None for t in times):
                continue
            prompt = context["scene_prompt"] + "\n这是有序多图输入。仅报告可明确归属到某个输入帧的新证据。只输出 {\"frames\":[{\"frame_id\":实际输入帧号,\"observation\":上述五数组对象}]}；没有新增证据时 frames=[]。不可确定归属时不报告，不输出事件。输入帧号依次为 " + json.dumps(selected_ids)
            raw = backend.infer_clip([e["frame"]["image_path"] for e in selected], times, prompt)
            review = {"window": window, "raw": raw}
            review_path = output / f"{window['source_id']}_clip_{window['start']}_{window['end']}.json"
            write_json(review_path, review)
            context.setdefault("caption_artifacts", set()).add(review_path)
            try:
                data = decode_model_json(raw)
            except ValueError as error:
                raise ValueError(f"Invalid clip JSON at {review_path}: {error}") from error
            if not isinstance(data, dict) or set(data) != {"frames"} or not isinstance(data["frames"], list):
                raise ValueError(f"Invalid clip JSON at {review_path}")
            for item in data["frames"]:
                if item["frame_id"] not in selected_ids:
                    raise ValueError(f"Unknown clip frame at {review_path}")
                try:
                    parsed = parse_relation_output(json.dumps(item["observation"], ensure_ascii=False), context)
                except (ValueError, TypeError, KeyError) as error:
                    raise ValueError(f"Invalid clip observation {window['source_id']}/frame {item['frame_id']} at {review_path}: {error}") from error
                # Local IDs are independent per response. Keep them in a separate evidence record.
                entries.append({"frame": by_id[item["frame_id"]]["frame"], "raw": json.dumps(item["observation"], ensure_ascii=False),
                                "response": parsed, "clip_window": window,
                                "request_id": stable_id("Q", window, item["frame_id"])})
            review["parsed"] = data
            write_json(review_path, review)
            reviewed += 1
        if frames:
            write_json(output / f"{frames[0].source_id}.json", entries)
    return entries
