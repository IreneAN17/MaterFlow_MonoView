"""Semantic fixtures and IO contracts, not a benchmark of pretrained visual models."""
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import json
from pathlib import Path
import av
import numpy as np
from openpyxl import load_workbook
from PIL import Image
import pytest
import yaml

from materflow.config import REPO_ROOT, load_config
from materflow.schema import FIELDS, FrameRecord, field_evidence, stable_id, read_json, read_jsonl
from materflow.materials.context import load_run_context
from materflow.materials.prompts import compile_sg_inputs
from materflow.materials.events import propose_lab_events, resolve_event_candidates
from materflow.materials.identity import resolve_object_identities
from materflow.materials.lineage import reconstruct_lineage
from materflow.materials.ocr import extract_lab_observations, recognize_instrument_reading
from materflow.outputs.export import assemble_run_result, export_run_result
from materflow.perception.relations import parse_relation_output
from materflow.perception.grounding import merge_frame_detections, ground_relation_objects
from materflow.tracking.objects import history_frame
from materflow.video.sampling import sample_frames, build_time_windows, closed_job_bounds, crop_regions


@pytest.fixture
def context(tmp_path):
    config = load_config(REPO_ROOT / "configs/example_run.yaml")
    config["output_dir"] = tmp_path / "output"
    result = load_run_context(config)
    compile_sg_inputs(result, config["output_dir"])
    result["evidence_index"] = {}
    result["identities"] = {}
    return result


def empty_response():
    return {name: [] for name in ("objects", "relations", "texts", "flows", "states")}


def add_carrier(context, track, oid, category="container", reagent=None):
    obj = {"object_id": oid, "object_class": category, "type": "container"}
    if reagent:
        obj["reagent_id"] = reagent
    context["objects"][oid] = obj
    context["identities"][track] = {"object_id": oid, "field_status": "supported", "evidence_refs": []}


def obs(context, signal, time, payload, track=None, object_id=None):
    oid = stable_id("O", signal, time, payload, track)
    vid = stable_id("V", oid)
    context["evidence_index"][vid] = {"source_id": "example_video", "frame_id": int(time * 5) if time is not None else 0,
        "source_frame_index": int(time * 30) if time is not None else 0,
        "start_s": time, "end_s": time, "image_path": "synthetic_fixture.png", "box_xyxy": None}
    item = {"observation_id": oid, "source_id": "example_video", "start_s": time, "end_s": time,
            "signal_type": signal, "observed_value": "fixture", "unit": None, "object_id": object_id,
            "instrument_id": None, "value_basis": "direct", "evidence_refs": [vid], "notes": None,
            "payload": payload, "track_id": track}
    return field_evidence(item, FIELDS["Observations"])


def state(context, time, track, name):
    return obs(context, "visual_state", time, {"kind": "state", "state": name, "track_id": track}, track,
               context["identities"].get(track, {}).get("object_id"))


def flow(context, time, source, target, form="liquid", arrival=True):
    return obs(context, "material_flow", time, {"source_track_id": source, "target_track_id": target,
        "material_form": form, "arrival_visible": arrival,
        "source_object_id": context["identities"].get(source, {}).get("object_id"),
        "target_object_id": context["identities"].get(target, {}).get("object_id")}, target,
        context["identities"].get(target, {}).get("object_id"))


def derive(observations, context):
    candidates = propose_lab_events(observations, context)
    events = resolve_event_candidates(candidates, observations, context)
    return events, reconstruct_lineage(events, observations, context)


def test_prepare_registry_contract_and_no_annotation_leak(context):
    compiled = context["compiled"]
    assert context["role_candidates"]["C_PRODUCT"] == ["filter_paper_1", "product_container_1"]
    assert compiled["capture"]["sources"][0]["run_offset_s"] is None
    assert compiled["capture"]["sources"][0]["time_scale"] is None
    assert compiled["capture"]["sources"][0]["is_continuous_clip"] is None
    assert compiled["initial_conditions_status"] == "unresolved"
    assert compiled["qwen_context"]["declared_context"]["execution_evidence"] is False
    assert "CMP_AGING_TIME" not in context["scene_prompt"]
    assert "lineage_effect" not in context["scene_prompt"]
    assert "field_status" not in json.dumps(compiled["qwen_context"])
    assert {row["field"] for row in compiled["qwen_context"]["observation_vocab"]} == {"signal_type", "value_basis"}
    assert read_json(context["config"]["output_dir"] / "annotations/relations.json") == ["hold", "on", "in", "flow into"]
    assert context["aliases"]["scene_relation"]["hold"] == ["hold"]
    assert context["aliases"]["object_class"]["reagent bottle"] == ["bottle"]
    assert "human" in read_json(context["config"]["output_dir"] / "annotations/objects.json")
    assert not (context["config"]["output_dir"] / "input_files/frames.txt").exists()


def test_label_only_and_literal_numbers_survive(context, tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (40, 30), "white").save(image)
    frame = FrameRecord("example_video", 8, 83, 2.73, str(image), width=40, height=30)
    data = empty_response()
    data["objects"] = [{"local_id": "obj_1", "object_class": "bottle", "description": "labelled bottle", "region_hint_xyxy": None}]
    data["texts"] = [{"object": "obj_1", "kind": "label", "raw_text": "−7.297%", "value": -7.297,
                      "unit": "%", "reading_kind": "unknown", "region_hint_xyxy": None}]
    parsed = parse_relation_output(json.dumps(data), context)
    assert parsed["parsed"] == []
    graph = {"frame": asdict(frame), "detections": [], "assignments": {"Q1:obj_1": None},
             "captions": [{"frame": asdict(frame), "response": parsed, "request_id": "Q1"}]}
    observations = extract_lab_observations([frame], {"frame_graphs": [graph], "local_to_track": {}}, None, context)
    assert len(observations) == 2
    assert observations[-1]["observed_value"] == "−7.297%"
    assert observations[-1]["payload"]["value"] == -7.297
    assert observations[-1]["start_s"] == 2.73
    assert observations[-1]["object_id"] is None
    class LabelOnlyDetector:
        version = {"fixture": True}
        def detect(self, image, category):
            assert category == "bottle"  # not label text, percentage or reagent ID
            return [{"object_class": category, "box_xyxy": [.1, .1, .8, .8], "box_px": [4, 3, 32, 24], "score": .8}]
    grounded = ground_relation_objects([frame], [{"frame": asdict(frame), "response": parsed, "request_id": "Q1"}], context, LabelOnlyDetector())
    assert len(grounded[0]["detections"]) == 1
    assert grounded[0]["relations"] == []
    assert recognize_instrument_reading({"value": 80, "unit": "°C", "reading_kind": "setpoint"},
        {"bound": True, "object_class": "temperature probe", "sample_measurement_supported": True}) is None
    assert recognize_instrument_reading({"value": 80, "unit": "°C", "reading_kind": "display"},
        {"bound": True, "object_class": "temperature probe"}) is None
    reading = recognize_instrument_reading({"value": -7.297, "unit": "g", "reading_kind": "display"},
        {"bound": True, "object_class": "balance"})
    assert reading["value"] == -7.297 and reading["net_mass_status"] == "unresolved"


def test_bad_json_and_references_are_errors(context):
    with pytest.raises(ValueError):
        parse_relation_output("```json\n{}\n```", context)
    data = empty_response()
    data["states"] = [{"object": "obj_9", "state": "stationary", "detail": "visible"}]
    with pytest.raises(ValueError, match="reference"):
        parse_relation_output(json.dumps(data), context)
    data = empty_response()
    data["flows"] = [{"source": None, "target": None, "material_form": "liquid", "arrival_visible": "false"}]
    with pytest.raises(ValueError, match="boolean"):
        parse_relation_output(json.dumps(data), context)


def test_unknown_label_and_tilt_do_not_force_identity_or_transfer(context):
    existence = obs(context, "visual_state", 0, {"kind": "object", "object_class": "bottle"}, "unknown")
    label = obs(context, "ocr_text", 0, {"kind": "label", "raw_text": "Unlisted ethanol"}, "unknown")
    context["identities"] = resolve_object_identities({}, [existence, label], context)
    assert context["identities"]["unknown"]["object_id"] is None
    assert context["identities"]["unknown"]["candidates"] == []
    events, lineage = derive([state(context, 0, "unknown", "container_tilted"), flow(context, 0, "unknown", "target", arrival=False)], context)
    assert events == [] and lineage["lineage"] == []


def test_transfer_existing_content_two_edges_and_unknown_initial_provenance(context):
    add_carrier(context, "A", "source", reagent="R_FORMAMIDE")
    add_carrier(context, "B", "target", reagent="R_CTAB")
    context["manifest"]["initial_conditions_status"] = "supported"
    context["manifest"]["initial_conditions"] = [
        {"container_id": "source", "reagent_ids": ["R_FORMAMIDE"], "provenance_status": "supported"},
        {"container_id": "target", "reagent_ids": ["R_CTAB"], "provenance_status": "supported"}]
    observations = [flow(context, 0, "A", "B")]
    events, lineage = derive(observations, context)
    assert len(events) == 1
    assert {e["relation"] for e in lineage["lineage"]} == {"transferred_to", "contributed_to"}
    assert len({e["event_id"] for e in lineage["lineage"]}) == 1
    assert len(lineage["materials"]) == 3
    assert lineage["materials"][-1]["provenance_status"] == "supported"
    context["manifest"]["initial_conditions"] = []
    context["manifest"]["initial_conditions_status"] = "unresolved"
    events, lineage = derive(observations, context)
    assert len(lineage["lineage"]) == 1
    assert lineage["lineage"][0]["relation"] == "transferred_to"
    assert lineage["materials"][-1]["provenance_status"] == "unresolved"
    assert len(lineage["materials"]) == 2  # no invented pre-existing target material


def filtration_fixture(context, rounds=2):
    add_carrier(context, "A", "source", reagent="R_FORMAMIDE")
    add_carrier(context, "F", "actual_funnel", "filter funnel")
    add_carrier(context, "W", "unregistered_wash_bottle", "bottle")
    context["sources"]["example_video"]["is_continuous_clip"] = True
    observations = [obs(context, "visual_state", 0, {"kind": "object", "object_class": "filter funnel"}, "F", "actual_funnel"),
                    flow(context, 0, "A", "F", "suspension"), state(context, 0, "F", "solid_retained")]
    for i in range(rounds):
        time = 2 + 2*i
        observations += [flow(context, time, "W", "F"), state(context, time, "F", "liquid_contacting_retained_solid"),
                         state(context, time, "F", "liquid_draining"), state(context, time, "F", "vacuum_indicator_on")]
    return observations


def test_filter_two_washes_no_duplicate_transfer_or_ideal_filtrate(context):
    observations = filtration_fixture(context)
    events, lineage = derive(observations, context)
    assert [e["action"] for e in events] == ["filter", "wash", "wash"]
    assert [e["output_part"] for e in lineage["lineage"]] == ["retentate"] * 3
    assert [e["relation"] for e in lineage["lineage"]] == ["processed_into"] * 3
    processed = [m for m in lineage["materials"] if m["material_type"] == "sample_state"]
    assert len(processed) == 3
    assert {m["container_id"] for m in processed} == {"actual_funnel"}
    assert all(m["provenance_status"] == "unresolved" for m in processed)
    assert all(m["reagent_ids"] == ["R_FORMAMIDE"] for m in processed)
    assert lineage["lineage"][1]["source_material_id"] == processed[0]["material_id"]
    assert lineage["lineage"][2]["source_material_id"] == processed[1]["material_id"]
    single_round = [o for o in observations if o["start_s"] != 4]
    assert [e["action"] for e in derive(single_round, context)[0]] == ["filter", "wash"]


def test_overlap_determinism_boundaries_and_export_contract(context):
    observations = filtration_fixture(context)
    candidates = propose_lab_events(observations, context)
    events = resolve_event_candidates(candidates + deepcopy(candidates), observations, context)
    assert [e["action"] for e in events] == ["filter", "wash", "wash"]
    assert all(e["interval_status"] == "unresolved" and e["start_s"] is None and e["end_s"] is None for e in events)
    lineage = reconstruct_lineage(events, observations, context)
    assert (events, lineage) == derive(observations, context)
    result = assemble_run_result(events, observations, lineage)
    paths = export_run_result(result, context)
    assert len(paths) == 3
    mapping = {"observations_v4.0.xlsx": ["Observations"], "events_v4.0.xlsx": ["Events"], "lineage_v4.0.xlsx": ["Materials", "Lineage"]}
    for path in paths:
        name = Path(path).name
        workbook = load_workbook(path, read_only=True)
        assert workbook.sheetnames == mapping[name] + ["Field Evidence", "说明"]
        # Frozen original task contract; live human reference workbooks can have extra review columns.
        expected_headers = read_json(Path(__file__).parent / "fixtures/export_headers_v4.json")
        for sheet in workbook.sheetnames:
            assert list(next(workbook[sheet].iter_rows(max_row=1, values_only=True))) == expected_headers[sheet]
        if name == "events_v4.0.xlsx":
            assert workbook["Events"].cell(2, FIELDS["Events"].index("start_s") + 1).value is None
        workbook.close()


@pytest.mark.parametrize("length", [1, 10, 11, 18, 19, 22])
def test_window_tail_and_history_mapping(length):
    frames = [FrameRecord("s", i, 40 + i*7, 1.1 + i*.23, "frame.png") for i in range(length)]
    windows = build_time_windows(frames, {"window_frames": 10, "window_stride": 8})
    assert set(i for w in windows for i in w["frame_ids"]) == set(range(length))
    assert windows[-1]["end"] == length
    assert closed_job_bounds(windows[-1]) == f"{windows[-1]['start']}-{length-1}"
    last = windows[-1]
    mapped = history_frame(frames[last["start"]:last["end"]], last["end"]-last["start"]-1)
    assert mapped["frame_id"] == length-1
    assert mapped["source_frame_index"] == 40+(length-1)*7
    assert mapped["source_time_s"] == pytest.approx(1.1+(length-1)*.23)


def test_real_pyav_pts_sampling_and_unknown_image_time(tmp_path):
    path = tmp_path / "vfr.mkv"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=10)
        stream.width, stream.height = 64, 48
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index, pts in enumerate([0, 100, 400, 900]):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index*50, dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    sampled = sample_frames(path, {"source_id": "vfr", "output_dir": tmp_path, "sample_fps": 5})
    assert [f.source_frame_index for f in sampled] == [0, 2, 3]
    assert [f.source_time_s for f in sampled] == pytest.approx([0, .4, .9])
    assert sampled[-1].source_time_s != sampled[-1].frame_id / 5
    image = tmp_path / "still.png"
    Image.new("RGB", (80, 60)).save(image)
    still = sample_frames(image, {"source_id": "still", "output_dir": tmp_path, "sample_fps": 5})
    assert still[0].source_time_s is None
    crop = crop_regions(image, [{"box_xyxy": [.25, .25, .75, .75]}])[0]
    assert crop["crop_xyxy_original_px"] == [20, 15, 60, 45]
    assert crop["image"].size == (40, 30)


def test_nms_keeps_two_bottles_and_deduplicates_only_same_box():
    detections = [{"object_class": "bottle", "box_xyxy": [0, 0, .2, .3], "score": .8},
                  {"object_class": "bottle", "box_xyxy": [.6, 0, .8, .3], "score": .9},
                  {"object_class": "bottle", "box_xyxy": [0, 0, .2, .3], "score": .7}]
    assert len(merge_frame_detections(detections)) == 2


def test_grasp_is_not_hold_and_stationary_is_not_age(context):
    relation = obs(context, "visual_state", 0, {"kind": "relation", "subject_track_id": "camera_wearer", "relation": "hold", "object_track_id": "B"})
    assert derive([relation, state(context, 0, "B", "stationary")], context)[0] == []


def test_supported_boundaries_still_need_declared_continuity(context):
    add_carrier(context, "B", "sample_carrier")
    start = state(context, 1, "B", "sample_placed_in_oven")
    end = state(context, 3, "B", "sample_removed_from_oven")
    for item in (start, end):
        item["payload"]["temporal_input"] = "ordered_images"
    events, _ = derive([start, end], context)
    assert len(events) == 1 and events[0]["action"] == "hold"
    assert events[0]["start_s"] == 1 and events[0]["end_s"] == 3
    assert events[0]["interval_status"] == "unresolved"
    assert "duration_s" not in events[0]
    context["sources"]["example_video"]["is_continuous_clip"] = True
    assert derive([start, end], context)[0][0]["interval_status"] == "supported"


def test_same_carrier_does_not_inherit_contents_across_unknown_gap(context):
    add_carrier(context, "A", "source_A", reagent="R_FORMAMIDE")
    add_carrier(context, "B", "target")
    add_carrier(context, "C", "source_C", reagent="R_CTAB")
    observations = [flow(context, 0, "A", "B"), flow(context, 3, "C", "B")]
    events, lineage = derive(observations, context)
    assert len(events) == 2
    assert [e["relation"] for e in lineage["lineage"]] == ["transferred_to", "transferred_to"]
    assert all(m["provenance_status"] == "unresolved" for m in lineage["materials"])
    context["sources"]["example_video"]["is_continuous_clip"] = True
    _, continuous = derive(observations, context)
    assert [e["relation"] for e in continuous["lineage"]] == ["transferred_to", "transferred_to", "contributed_to"]
    assert continuous["materials"][-1]["provenance_status"] == "unresolved"


def test_cross_source_order_is_not_invented(context):
    add_carrier(context, "A", "source")
    add_carrier(context, "B", "target")
    context["sources"]["example_video"]["is_continuous_clip"] = True
    context["sources"]["source_z"] = {"source_id": "source_z", "is_continuous_clip": True, "run_offset_s": None}
    first = flow(context, 0, "A", "B")
    second = state(context, 1, "B", "stirring_motion")
    second["source_id"] = "source_z"
    _, lineage = derive([first, second], context)
    transferred = next(e for e in lineage["lineage"] if e["relation"] == "transferred_to")
    processed = next(e for e in lineage["lineage"] if e["relation"] == "processed_into")
    assert processed["source_material_id"] != transferred["target_material_id"]


def test_stage_disk_contract_and_model_free_rederive(tmp_path, monkeypatch):
    import materflow.backends.factory as factory
    import materflow.perception.grounding as grounding
    import materflow.tracking.objects as tracking
    from materflow.pipeline import process_run
    image = tmp_path / "still.png"
    Image.new("RGB", (64, 48)).save(image)
    manifest = read_json(REPO_ROOT / "configs/example_manifest_filter.json")
    manifest["capture"]["sources"][0]["file"] = str(image)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = {"run_id": "EXAMPLE_RUN", "registry_path": str(REPO_ROOT / "docs/V4.0/example_registry_v4.0.xlsx"),
              "manifest_path": str(manifest_path), "output_dir": str(tmp_path / "run"), "device": "cpu"}
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    calls = []

    class EmptyFixtureBackend:
        version = {"fixture": True}
        def infer_images(self, images, prompt, **kwargs):
            calls.append("images")
            return [json.dumps(empty_response()) for _ in images]
        def infer_clip(self, *args):
            pytest.fail("A single untimed still is not a temporal clip")

    class EmptyFixtureDetector:
        version = {"fixture": True}
        def __init__(self, config):
            pass
        def detect(self, *args):
            pytest.fail("No Stage 1 objects")

    class EmptyFixtureTracking:
        version = {"fixture": True}
        def __init__(self, config):
            pass

    monkeypatch.setattr(factory, "create_backend", lambda config: EmptyFixtureBackend())
    monkeypatch.setattr(grounding, "GroundingDINO", EmptyFixtureDetector)
    monkeypatch.setattr(tracking, "TrackingModels", EmptyFixtureTracking)
    process_run(config_path, "all")
    assert calls == ["images"]
    summary = read_json(tmp_path / "run/run_summary.json")
    assert all(s["status"] == "complete" for s in summary["stages"].values())
    monkeypatch.setattr(factory, "create_backend", lambda config: pytest.fail("derive called a model"))
    result = process_run(config_path, "derive")
    assert result["events"] == []
    process_run(config_path, "derive")  # stable upstream hashes; export never poisons re-derivation
    config["sample_fps"] = 3
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        process_run(config_path, "caption")
