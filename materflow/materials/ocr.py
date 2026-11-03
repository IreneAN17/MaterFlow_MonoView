"""Unified visual observations and conservative display interpretation."""
from copy import deepcopy
import json
from PIL import Image
from ..schema import FIELDS, field_evidence, stable_id, write_json
from ..video.sampling import crop_regions

ROI_PROMPT = ('只逐字识读这张局部图中的标签或仪表；保留负号、小数、百分号和单位，不补全。'
              '只输出 JSON: {"raw_text":"可读原文或空字符串","kind":"label或display或unknown",'
              '"value":null,"unit":null,"reading_kind":"setpoint或display或measured或unknown"}。'
              'value只有完整可读的数值才填数字，否则null；设备显示不自动代表样品实测。')


def roi_response_schema():
    from ..perception.relations import scene_response_schema
    source = scene_response_schema()["properties"]["texts"]["items"]["properties"]
    fields = ("raw_text", "kind", "value", "unit", "reading_kind")
    return {"type": "object", "properties": {k: source[k] for k in fields},
            "required": list(fields), "additionalProperties": False}


def _evidence(frame, request_id, key, context, box=None, **extra):
    eid = stable_id("V", frame["source_id"], frame["frame_id"], request_id, key)
    context.setdefault("evidence_index", {})[eid] = {
        "source_id": frame["source_id"], "frame_id": frame["frame_id"],
        "source_frame_index": frame["source_frame_index"], "start_s": frame["source_time_s"],
        "end_s": frame["source_time_s"], "image_path": frame["image_path"], "box_xyxy": box,
        "request_id": request_id, **extra}
    return eid


def _observation(frame, signal, value, payload, refs, object_track=None, unit=None):
    record = {"observation_id": stable_id("O", frame["source_id"], frame["frame_id"], signal, payload),
              "source_id": frame["source_id"], "start_s": frame["source_time_s"], "end_s": frame["source_time_s"],
              "signal_type": signal, "observed_value": value, "unit": unit, "object_id": None,
              "instrument_id": None, "value_basis": "direct", "evidence_refs": refs, "notes": None,
              "track_id": object_track, "payload": payload}
    supported = ["observation_id", "source_id", "signal_type", "observed_value", "value_basis", "evidence_refs"]
    if frame["source_time_s"] is not None:
        supported += ["start_s", "end_s"]
    field_evidence(record, FIELDS["Observations"], supported=supported)
    for name, item in payload.items():
        field_evidence(record, ["payload." + name], supported=["payload." + name] if item is not None else [])
    return record


def recognize_reagent_label(image, context):
    """ROI transcription using the already loaded backend; returns literal text, no identity."""
    raw = context["backend"].infer_images([image], ROI_PROMPT, response_schema=roi_response_schema())[0]
    context["roi_raw"] = raw
    return _parse_roi(raw)


def _parse_roi(raw):
    from ..perception.relations import decode_model_json
    data = decode_model_json(raw)
    if not isinstance(data, dict) or set(data) != {"raw_text", "kind", "value", "unit", "reading_kind"}:
        raise ValueError("Invalid ROI transcription JSON")
    from ..perception.relations import parse_relation_output
    probe = {"objects": [{"local_id": "obj_1", "object_class": None, "description": "ROI"}],
             "relations": [], "texts": [{"object": "obj_1", **data}], "flows": [], "states": []}
    parse_relation_output(json.dumps(probe))
    return data


def recognize_instrument_reading(text, instrument_profile):
    """Pure interpretation of a transcription and supported instrument/target bindings."""
    value, unit, kind = text.get("value"), text.get("unit"), text.get("reading_kind")
    if value is None or kind in {"setpoint", "unknown"} or not instrument_profile.get("bound"):
        return None
    category = instrument_profile.get("object_class")
    if category == "balance" and unit == "g":
        return {"signal_type": "mass_g", "value": value, "unit": "g", "net_mass_status": "unresolved"}
    if category == "temperature probe" and unit in {"°C", "C", "℃"} and kind == "measured" and instrument_profile.get("sample_measurement_supported"):
        return {"signal_type": "temperature_c", "value": value, "unit": "°C"}
    if category == "stirrer" and unit and unit.casefold() == "rpm" and kind == "measured" and instrument_profile.get("actual_feedback_supported"):
        return {"signal_type": "stirring_speed_rpm", "value": value, "unit": "rpm"}
    return None


def extract_lab_observations(frames, tracks, backend, context):
    observations = []
    reread = set()
    for graph in tracks["frame_graphs"]:
        frame = graph["frame"]
        detections = {d["detection_id"]: d for d in graph["detections"]}
        if not graph["captions"]:
            for det in graph["detections"]:
                tid = tracks.get("detection_to_track", {}).get(det["detection_id"])
                tid = tid or stable_id("UT", frame["source_id"], frame["frame_id"], det["detection_id"])
                request = stable_id("GD", det["detection_id"])
                eid = _evidence(frame, request, "detector_object", context, det["box_xyxy"],
                                evidence_kind="detector", model=context["config"].get("grounding_model"),
                                model_revision=context["config"].get("grounding_revision"))
                payload = {"kind": "object", "object_class": det["object_class"],
                           "description": det["object_class"], "track_id": tid,
                           "classification_status": "supported", "model_source": "GroundingDINO",
                           "detection_score": det["score"]}
                observations.append(_observation(frame, "visual_state", det["object_class"], payload, [eid], tid))
        for entry in graph["captions"]:
            response, request_id = entry["response"], entry["request_id"]
            local_objects = {obj["local_id"]: obj for obj in response["objects"]}

            def ref(local):
                if local is None or local == "camera_wearer":
                    return local
                # Missing detection retains frame-local evidence without inventing continuity.
                return tracks["local_to_track"].get(request_id + ":" + local) or stable_id("UT", frame["source_id"], frame["frame_id"], request_id, local)

            def box(local):
                det = detections.get(graph["assignments"].get(request_id + ":" + str(local)))
                return det["box_xyxy"] if det else None

            for obj in response["objects"]:
                local = obj["local_id"]
                payload = {"kind": "object", "object_class": obj["object_class"], "description": obj["description"],
                           "object_class_raw": obj.get("object_class_raw"),
                           "classification_status": obj["classification_status"], "track_id": ref(local)}
                eid = _evidence(frame, request_id, "object:" + local, context, box(local))
                observations.append(_observation(frame, "visual_state", obj["description"], payload, [eid], ref(local)))
            for index, relation in enumerate(response["relations"]):
                payload = {"kind": "relation", "subject_track_id": ref(relation["subject"]), "relation": relation["relation"],
                           "object_track_id": ref(relation["object"]), "relation_raw": relation.get("relation_raw")}
                eid = _evidence(frame, request_id, f"relation:{index}", context, box(relation["object"]))
                observations.append(_observation(frame, "visual_state", f"{payload['subject_track_id']} {relation['relation'] or relation.get('relation_raw')} {payload['object_track_id']}", payload, [eid], ref(relation["subject"])))
            for index, flow in enumerate(response["flows"]):
                payload = {"source_track_id": ref(flow["source"]), "target_track_id": ref(flow["target"]),
                           "material_form": flow["material_form"], "arrival_visible": flow["arrival_visible"]}
                eid = _evidence(frame, request_id, f"flow:{index}", context, box(flow["target"]),
                                source_box_xyxy=box(flow["source"]), clip_window=entry.get("clip_window"))
                observations.append(_observation(frame, "material_flow", f"{payload['source_track_id'] or '?'} → {payload['target_track_id'] or '?'}", payload, [eid], ref(flow["target"])))
            for index, state in enumerate(response["states"]):
                payload = {"kind": "state", "state": state["state"], "detail": state["detail"], "track_id": ref(state["object"]),
                           "temporal_input": "ordered_images" if entry.get("clip_window") else "single_image"}
                eid = _evidence(frame, request_id, f"state:{index}", context, box(state["object"]), clip_window=entry.get("clip_window"))
                observations.append(_observation(frame, "visual_state", state["state"], payload, [eid], ref(state["object"])))
            for index, text in enumerate(response["texts"]):
                local = text["object"]
                payload = {key: text.get(key) for key in ("raw_text", "kind", "value", "unit", "reading_kind")}
                payload.update({"track_id": ref(local), "object_class": local_objects[local]["object_class"]})
                eid = _evidence(frame, request_id, f"text:{index}", context, box(local))
                observations.append(_observation(frame, "ocr_text", text["raw_text"], payload, [eid], ref(local)))
                unclear = not text["raw_text"].strip() or any(char in text["raw_text"] for char in "?？�")
                key = (ref(local), text["kind"])
                if unclear and backend is not None and context["config"]["roi_reread"] and box(local) and key not in reread:
                    region = {"box_xyxy": text.get("region_hint_xyxy") or box(local)}
                    crop = crop_regions(frame["image_path"], [region])[0]
                    crop_id = stable_id("ROI", eid)
                    crop_path = context["config"]["output_dir"] / "crops" / (crop_id + ".png")
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    crop["image"].save(crop_path)
                    raw = backend.infer_images([crop["image"]], ROI_PROMPT, response_schema=roi_response_schema())[0]
                    write_json(crop_path.with_suffix(".json"), {"raw": raw, "frame": frame,
                        "crop_xyxy_original_px": crop["crop_xyxy_original_px"], "original_size": crop["original_size"]})
                    context.setdefault("crop_artifacts", []).extend([crop_path, crop_path.with_suffix(".json")])
                    try:
                        parsed = _parse_roi(raw)
                    except (ValueError, TypeError, KeyError) as error:
                        raise ValueError(f"ROI {frame['source_id']}/frame {frame['frame_id']} at {crop_path}: {error}") from error
                    improved = {**payload, **parsed, "roi_id": crop_id}
                    roi_eid = _evidence(frame, request_id, crop_id, context, box(local), crop_path=str(crop_path),
                                        crop_xyxy_original_px=crop["crop_xyxy_original_px"], original_size=crop["original_size"])
                    observations.append(_observation(frame, "ocr_text", parsed["raw_text"], improved, [roi_eid], ref(local)))
                    reread.add(key)
    # Same observation from overlapped clips/windows has one record, with all original evidence.
    unique = {}
    for observation in observations:
        oid = observation["observation_id"]
        if oid in unique:
            unique[oid]["evidence_refs"] = sorted(set(unique[oid]["evidence_refs"] + observation["evidence_refs"]))
            for evidence in unique[oid]["field_evidence"].values():
                evidence["evidence_refs"] = unique[oid]["evidence_refs"]
        else:
            unique[oid] = observation
    return list(unique.values())


def promote_readings(observations, context):
    result = list(observations)
    for obs in observations:
        if obs["signal_type"] != "ocr_text" or obs["payload"].get("kind") != "display":
            continue
        obj = context["objects"].get(obs.get("object_id"), {})
        same_frame = [o for o in observations if o["source_id"] == obs["source_id"] and o["start_s"] == obs["start_s"]]
        states = [o for o in same_frame if o.get("track_id") == obs.get("track_id") and o["payload"].get("kind") == "state"]
        state_names = {o["payload"].get("state") for o in states}
        relations = [o for o in same_frame if o["payload"].get("kind") == "relation" and o["payload"].get("subject_track_id") == obs.get("track_id") and o["payload"].get("relation") == "in"]
        sample_relation = next((o for o in relations if o["payload"].get("object_id")), None)
        profile = {"bound": bool(obj), "object_class": obj.get("object_class"),
                   "sample_measurement_supported": "probe_measuring_sample" in state_names and sample_relation is not None,
                   "actual_feedback_supported": "actual_speed_feedback" in state_names}
        reading = recognize_instrument_reading(obs["payload"], profile)
        if reading is None:
            continue
        item = deepcopy(obs)
        item.update({"observation_id": stable_id("O", obs["observation_id"], reading["signal_type"]),
                     "signal_type": reading["signal_type"], "observed_value": reading["value"], "unit": reading["unit"],
                     "instrument_id": obj["object_id"], "object_id": sample_relation["payload"]["object_id"] if sample_relation else None,
                     "payload": {**reading, "reading_kind": obs["payload"]["reading_kind"], "transcription_observation_id": obs["observation_id"]}})
        item["field_evidence"] = {}
        refs = sorted(set(obs["evidence_refs"] + [ref for o in states + ([sample_relation] if sample_relation else []) for ref in o["evidence_refs"]]))
        item["evidence_refs"] = refs
        field_evidence(item, FIELDS["Observations"], refs, supported=("observation_id", "source_id", "signal_type", "observed_value", "unit", "instrument_id", "value_basis", "evidence_refs"))
        for name, value in item["payload"].items():
            field_evidence(item, ["payload." + name], refs,
                           supported=["payload." + name] if value is not None and name != "net_mass_status" else [])
        result.append(item)
    return result
