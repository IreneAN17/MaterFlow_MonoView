"""V4.2 contract/risk tests. Synthetic fixtures are never production recognition results."""
from pathlib import Path
from copy import deepcopy
import csv
import json
import sqlite3
import numpy as np
import pytest
from PIL import Image
from materflow.schema import write_json,read_json,write_jsonl,read_jsonl
from materflow.v42.context import DEFAULTS
from materflow.v42.catalog import match_label
from materflow.v42.clock import normalize_anchors,time_at
from materflow.v42.tracking import KLTTracker,gray_image,local_motion
from materflow.v42.intervals import aggregate_points
from materflow.v41.runtime import Ids

def config(tmp_path):
    return {**deepcopy(DEFAULTS),"sample_fps":5,"run_id":"synthetic_test_only","output_dir":tmp_path,
            "repo_root":tmp_path,"timezone":"Asia/Shanghai","ranges_s":[]}

def frame(fid,t):
    return {"source_id":"synthetic","frame_id":fid,"source_frame_index":fid,"source_time_s":t,
            "image_path":"synthetic_fixture.png","pts":fid,"time_base":"1/5","width":120,"height":80}

def entry(text,score=.99):
    return {"request_id":"fixture_ocr","blocks":[{"raw_text":text,"model_score":score}]}

def test_packaging_requires_every_field_and_rejects_same_brand_other_size():
    catalog=[{"packaging_catalog_id":"TEST_PKG","brand":"Acme","specification":"500mL",
              "required_match_fields":["brand","specification"]}]
    assert match_label([entry("Acme 500 mL")],catalog)["status"]=="matched"
    assert match_label([entry("Acme 250mL")],catalog)["status"]=="insufficient"
    assert match_label([entry("Acme")],catalog)["packaging_catalog_id"] is None
    assert match_label([entry("Acme 500mL",.5)],catalog)["status"]=="unreadable"

def test_packaging_unknown_ambiguous_and_catalog_missing_are_not_forced_matches():
    rows=[{"packaging_catalog_id":k,"brand":"Acme","required_match_fields":["brand"]}
          for k in ("TEST_A","TEST_B")]
    assert match_label([entry("Acme")],rows)["status"]=="ambiguous"
    assert match_label([entry("AcmeOther")],rows)["status"]=="insufficient"
    assert match_label([entry("Acme")],[])["status"]=="not_configured"

def test_same_packaging_does_not_merge_two_physical_objects():
    from materflow.v42.aggregate import bind_objects
    response={"objects":[{"local_id":"obj_1","object_class":"bottle","region_hint_xyxy":[.05,.1,.3,.7]},
                         {"local_id":"obj_2","object_class":"bottle","region_hint_xyxy":[.6,.1,.9,.7]}]}
    graph={"frame":frame(0,0),"display":[
        {"track_id":"physical_a","object_class":"bottle","box_xyxy":[.05,.1,.3,.7]},
        {"track_id":"physical_b","object_class":"bottle","box_xyxy":[.6,.1,.9,.7]}]}
    assert bind_objects(response,graph)=={"obj_1":"physical_a","obj_2":"physical_b"}

def test_instrument_is_not_a_material_transfer_carrier():
    from materflow.v42.aggregate import physical_flow_classes
    classes={"beaker":{"id_policy":"auto_instance"},"pipette":{"id_policy":"auto_instance"},
             "balance":{"id_policy":"class_only"}}
    assert physical_flow_classes({"source_class":"pipette","target_class":"beaker"},classes)
    assert not physical_flow_classes({"source_class":"pipette","target_class":"balance"},classes)
    assert not physical_flow_classes({"source_class":None,"target_class":"beaker"},classes)

def test_clock_cross_midnight_and_unbracketed_tail():
    rows=[{"source_id":"s","frame_id":i,"source_time_s":float(i),
           "raw_text":text,"model_score":.99} for i,text in enumerate(
           ("2026/10/06 23:59:59","2026/10/07 00:00:00"))]
    anchors=normalize_anchors(rows,"Asia/Shanghai")
    shots={"s:0":"one","s:1":"one"}
    assert time_at(.4,"s",anchors,shots)["datetime"].endswith("23:59:59+08:00")
    assert time_at(1.2,"s",anchors,shots)["status"]=="unresolved"
    assert time_at(.4,"s",anchors,{"s:0":"a","s:1":"b"})["status"]=="unresolved"

def test_second_precision_clock_rejects_incomplete_or_isolated_bad_value():
    rows=[{"source_id":"s","frame_id":i,"source_time_s":float(i),"raw_text":v,"model_score":.99}
          for i,v in enumerate(("2026/10/06 09:00:00","2026/10/06 19:00:00","2026/10/06 09:00:02"))]
    result=normalize_anchors(rows,"Asia/Shanghai")
    assert result[1]["datetime"] is None
    assert normalize_anchors([{**rows[0],"raw_text":"2026/10/06 09:00:0"}],"Asia/Shanghai")[0]["datetime"] is None

def test_ordered_schema_preflight_and_false_boundary_coordinates_are_not_asserted():
    from lmformatenforcer import JsonSchemaParser
    from materflow.v42.semantic import joint_schema,validate_response
    body={"response":{k:[] for k in ("objects","relations","texts","flows","states")},
          "state_spans":[],"action":None,"start_visible":False,"end_visible":False,
          "start_frame_id":10,"end_frame_id":10,"detail":"synthetic test"}
    parser=JsonSchemaParser(joint_schema([10,11]))
    raw=json.dumps(body,separators=(",",":"))
    for char in raw:
        parser=parser.add_character(char)
    assert parser.can_end()
    normalized=validate_response(raw,{"kind":"joint","frames":[{"frame_id":10},{"frame_id":11}]},
                                 {"actions":{"transfer"},"aliases":{}})
    assert normalized["start_frame_id"] is None and normalized["end_frame_id"] is None
    assert normalized["boundary_validation_notes"]
    assert json.loads(raw)["start_frame_id"]==10

def test_optical_update_uses_real_pixels_and_never_outputs_unverified_predictions(tmp_path):
    c=config(tmp_path);tracker=KLTTracker(c)
    rng=np.random.default_rng(42)
    pixels=rng.integers(0,256,(80,120,3),dtype=np.uint8)
    first=Image.fromarray(pixels);second=Image.fromarray(np.roll(pixels,2,axis=1))
    detection={"object_class":"beaker","box_xyxy":[.2,.2,.7,.8],"score":.9,"detection_id":"synthetic_d"}
    a=tracker.update(frame(0,0),first,gray_image(first,640),[detection],True,"shot")
    b=tracker.update(frame(1,.2),second,gray_image(second,640),[],False,"shot")
    assert len(a)==len(b)==1 and a[0]["track_id"]==b[0]["track_id"]
    assert b[0]["evidence_kind"]=="optical_flow"
    assert abs(b[0]["box_xyxy"][0]-.2-2/120)<.01
    blank=Image.fromarray(np.zeros_like(pixels));weak=KLTTracker(c)
    weak.update(frame(0,0),blank,gray_image(blank,640),[deepcopy(detection)],True,"shot")
    assert weak.update(frame(1,.2),blank,gray_image(blank,640),[],False,"shot")==[]

def test_camera_compensation_does_not_turn_whole_frame_motion_into_local_action():
    rng=np.random.default_rng(3)
    gray=rng.integers(0,256,(120,160),dtype=np.uint8)
    shifted=np.roll(gray,2,axis=1)
    score,matrix,camera=local_motion(gray,shifted)
    assert matrix is not None and camera>0
    assert score<.03

def setup_presence(connection,times,shot="one"):
    connection.execute("CREATE TABLE presence(tid TEXT,sid TEXT,t REAL,shot TEXT)")
    connection.executemany("INSERT INTO presence VALUES(?,?,?,?)",[("t","s",t,shot) for t in times])

def point(uid,t,kind="object",signal="visual_state",value="beaker"):
    return {"uid":uid,"observation_id":uid,"source_id":"s","start_s":t,"end_s":t,
        "track_id":"t","shot_id":"one","signal_type":signal,"observed_value":value,
        "unit":None,"object_class":"beaker","object_id":"beaker_1","instrument_class":None,
        "payload":{"kind":kind},"visual_evidence_refs":["v_"+uid],"evidence_refs":[],
        "value_basis":"direct","field_evidence":{},"notes":None}

def test_visibility_does_not_bridge_missing_frame_or_cut(tmp_path):
    ctx={"config":config(tmp_path)}
    con=sqlite3.connect(":memory:");setup_presence(con,[0,.2,.6])
    rows,mapping,intervals=aggregate_points([point("a",0),point("b",.2),point("c",.6)],ctx,con,Ids(tmp_path/"ids.json","synthetic_test_only"))
    assert len(rows)==2 and rows[0]["end_s"]==.2 and rows[1]["start_s"]==.6
    assert intervals[0]["unknown_segments"]==[]

def test_sparse_repeated_state_can_form_evidence_envelope_but_not_confirm_duration(tmp_path):
    ctx={"config":config(tmp_path)}
    con=sqlite3.connect(":memory:");setup_presence(con,[i/5 for i in range(21)])
    a,b=point("a",0,"state",value="stirring_motion"),point("b",4,"state",value="stirring_motion")
    rows,_,intervals=aggregate_points([a,b],ctx,con,Ids(tmp_path/"ids.json","synthetic_test_only"))
    assert len(rows)==1 and rows[0]["interval_status"]=="unresolved"
    assert intervals[0]["unknown_segments"] and intervals[0]["confirmed_processing_duration"] is None

def test_readout_plateau_retains_transcriptions_and_does_not_self_reference(tmp_path):
    ctx={"config":config(tmp_path)};con=sqlite3.connect(":memory:")
    setup_presence(con,[i/5 for i in range(21)])
    points=[]
    for i,t in enumerate((0,2,4)):
        text=point("text"+str(i),t,"display","ocr_text","40.00g")
        text.update(object_class=None,object_id=None,instrument_class="balance")
        num=point("number"+str(i),t,"reading","mass_g",40.)
        num.update(unit="g",object_class=None,object_id=None,instrument_class="balance")
        num["payload"].update(raw_text="40.00g",reading_kind="display",transcription_uids=[text["uid"]])
        points.extend((text,num))
    rows,_,intervals=aggregate_points(points,ctx,con,Ids(tmp_path/"ids.json","synthetic_test_only"))
    number=next(r for r in rows if r["signal_type"]=="mass_g")
    assert len(number["numeric_values"])==3 and number["value_basis"]=="derived"
    assert number["evidence_refs"] and number["observation_id"] not in number["evidence_refs"]
    assert number["display_resolution"]==.01

@pytest.mark.parametrize("hold_bottle,label_matched",[(False,True),(True,False)])
def test_three_layer_gate_rejects_label_only_and_unmatched_held_bottle(tmp_path,hold_bottle,label_matched):
    from materflow.v42.aggregate import aggregate
    c=config(tmp_path);folder=tmp_path/"internal";folder.mkdir()
    classes={"bottle":{"id_policy":"auto_instance"},"beaker":{"id_policy":"auto_instance"}}
    ctx={"config":c,"classes":classes}
    graphs=[]
    for i,t in enumerate((0,.2,.4)):
        shown=[{"track_id":tid,"object_class":cls,"box_xyxy":box,"evidence_kind":"detector",
                "source_time_s":t,"last_detection_s":t} for tid,cls,box in
               (("tb","bottle",[.6,.1,.9,.8]),("tk","beaker",[.1,.1,.4,.8]))]
        graphs.append({"frame":frame(i,t),"shot_id":"one","display":shown,"detections":[]})
    part=folder/"part.jsonl";write_jsonl(part,graphs);write_json(folder/"scan_partitions.json",[{"path":str(part)}])
    write_jsonl(folder/"ocr_results.jsonl",[])
    objs=[{"local_id":"obj_1","object_class":"beaker","description":"synthetic beaker",
           "classification_status":"supported","region_hint_xyxy":[.1,.1,.4,.8]},
          {"local_id":"obj_2","object_class":"bottle","description":"synthetic bottle",
           "classification_status":"supported","region_hint_xyxy":[.6,.1,.9,.8]}]
    relations=[{"subject":"camera_wearer","relation":"hold","object":"obj_1"}]
    if hold_bottle:
        relations.append({"subject":"camera_wearer","relation":"hold","object":"obj_2"})
    write_json(folder/"semantic_results.json",[{"frame":frame(1,.2),"kind":"scene","request_id":"SYNTHETIC",
        "response":{"objects":objs,"relations":relations,"flows":[],"states":[],"texts":[]}}])
    write_json(folder/"packaging_matches.json",{"tb":{"status":"matched" if label_matched else "not_configured",
                                                    "packaging_catalog_id":"SYNTHETIC" if label_matched else None}})
    class Progress:
        def update(self,*a,**k):pass
    aggregate(ctx,Progress())
    identities=read_json(folder/"identities.json")
    assert identities["tk"]["business_admitted"] is True
    assert identities["tb"]["business_admitted"] is False
    assert identities["tb"]["object_id"] is None
    assert all(r.get("object_class")!="bottle" for r in read_jsonl(folder/"observations.jsonl"))

def make_dataset(tmp_path,cross_group=False):
    import yaml
    rows=[]
    for i,split in enumerate(("train","val","test")):
        image=tmp_path/"images"/split/"image.png";image.parent.mkdir(parents=True)
        # Distinct captures, not near-identical adjacent video frames.
        pixels=np.random.default_rng(i).integers(0,256,(32,40,3),dtype=np.uint8)
        Image.fromarray(pixels).save(image)
        label=tmp_path/"labels"/split/"image.txt";label.parent.mkdir(parents=True)
        label.write_text("0 0.5 0.5 0.5 0.5\n")
        rows.append({"image_path":image.relative_to(tmp_path).as_posix(),"source_group_id":"shared" if cross_group else split,
                     "split":split})
    definition=tmp_path/"dataset.yaml";definition.write_text(yaml.safe_dump({"path":".","train":"images/train",
            "val":"images/val","test":"images/test","names":{0:"beaker"}}))
    manifest=tmp_path/"split.csv"
    with manifest.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["image_path","source_group_id","split"]);writer.writeheader();writer.writerows(rows)
    return definition,manifest

def test_dataset_format_checks_do_not_claim_annotation_accuracy(tmp_path):
    from materflow.v42.dataset import validate_dataset
    data,manifest=make_dataset(tmp_path)
    result=validate_dataset(data,manifest)
    assert result["status"]=="format_checks_passed" and result["instances_per_class"]["beaker"]==3
    assert result["annotation_completeness_verified"] is False
    assert result["accuracy_evaluated"] is False

def test_dataset_rejects_capture_group_leakage(tmp_path):
    from materflow.v42.dataset import validate_dataset
    data,manifest=make_dataset(tmp_path,True)
    result=validate_dataset(data,manifest)
    assert result["status"]=="invalid" and any("group crosses" in e for e in result["errors"])

def test_gallery_without_reference_data_does_not_load_a_model(tmp_path,monkeypatch):
    from materflow.v42 import gallery
    monkeypatch.setattr(gallery,"Encoder",lambda *a:pytest.fail("No model should load for empty user data"))
    path=tmp_path/"index.json"
    gallery.build_index({"config":config(tmp_path)},[],path)
    assert read_json(path)["status"]=="not_configured"

def test_schema_uses_actual_excel_dates_and_never_public_source_seconds(tmp_path):
    from datetime import datetime
    from openpyxl import Workbook,load_workbook
    from materflow.v42.export import FIELDS,sheet
    for name in ("Observations","Events","Materials","Lineage"):
        assert "start_s" not in FIELDS[name] and "end_s" not in FIELDS[name]
    book=Workbook();book.remove(book.active)
    sheet(book,"Observations",[{"observation_id":"O001","start_time":"2026-10-06T09:45:20+08:00",
                                "end_time":"2026-10-06T09:46:30+08:00","observed_value":"=unsafe_formula"}])
    path=tmp_path/"fixture.xlsx";book.save(path);loaded=load_workbook(path)
    assert isinstance(loaded["Observations"]["C2"].value,datetime)
    value=loaded["Observations"].cell(2,FIELDS["Observations"].index("observed_value")+1)
    assert value.data_type=="s" and value.value=="=unsafe_formula"

def test_catalog_and_export_changes_do_not_invalidate_semantic_requests(tmp_path):
    from materflow.v42.runtime import dependencies
    c=config(tmp_path)
    root=Path(__file__).resolve().parents[1]
    c.update(repo_root=root,prompt_template_path=root/"configs/prompts/lab_frame_v4_2.template.txt",
             model_name="test_not_loaded",model_revision="test",max_new_tokens=10)
    ctx={"config":c,"classes":{"beaker":{"id_policy":"auto_instance"}},
         "visual_context":{"actions":[{"action":"transfer"}]}}
    summary={"stages":{k:{"artifacts":{"fixture":"digest"}} for k in ("prepare","scan","ocr","aggregate","derive")}}
    semantic_before=dependencies("semantic",ctx,summary)
    identify_before=dependencies("identify",ctx,summary)
    export_before=dependencies("export",ctx,summary)
    catalog=tmp_path/"catalog.json";catalog.write_text("[]")
    c["catalog_path"]=catalog;c["video_crf"]=17
    assert dependencies("identify",ctx,summary)!=identify_before
    assert dependencies("export",ctx,summary)!=export_before
    assert dependencies("semantic",ctx,summary)==semantic_before

def test_two_image_ordered_plan_keeps_its_anchor(tmp_path,monkeypatch):
    from materflow.v42 import semantic
    c=config(tmp_path);c["max_clip_images"]=2
    folder=tmp_path/"internal";folder.mkdir();write_jsonl(folder/"ocr_results.jsonl",[])
    rows=[{"frame":frame(i,i/5),"shot_id":"same","local_motion":0 if i<3 else .2,
           "display":[]} for i in range(10)]
    monkeypatch.setattr(semantic,"scan_rows",lambda context:iter(rows))
    jobs=semantic.plan_tasks({"config":c})
    joints=[r for r in jobs if r["kind"]=="joint"]
    assert joints
    assert all(len(r["frames"])<=2 and r["frame"]["frame_id"] in {f["frame_id"] for f in r["frames"]} for r in joints)

def test_frame_cache_checks_source_content_and_reconstructs_corruption(tmp_path):
    import av
    from fractions import Fraction
    from materflow.v42.io import source_frames,FrameStore
    source=tmp_path/"synthetic.mp4"
    def generate(color):
        with av.open(str(source),"w") as container:
            stream=container.add_stream("libx264",rate=2);stream.width=96;stream.height=64;stream.pix_fmt="yuv420p"
            for i in range(2):
                pixels=np.zeros((64,96,3),dtype=np.uint8);pixels[:,:,color]=255
                f=av.VideoFrame.from_ndarray(pixels,format="rgb24");f.pts=i;f.time_base=Fraction(1,2)
                for packet in stream.encode(f):container.mux(packet)
            for packet in stream.encode():container.mux(packet)
    generate(0)
    ctx={"config":config(tmp_path),"source_paths":{"synthetic":source}}
    row,_=next(source_frames(ctx))
    store=FrameStore(ctx);saved=next(store.fetch([row]))
    path=Path(saved["image_path"])
    with Image.open(path) as im:first=np.asarray(im).copy()
    path.write_bytes(b"corrupt fixture")
    assert not store.valid(row)
    restored=next(store.fetch([row]))
    assert store.valid(row)
    with Image.open(restored["image_path"]) as im:np.testing.assert_array_equal(np.asarray(im),first)
    generate(2)
    newer={"config":config(tmp_path),"source_paths":{"synthetic":source}}
    second=next(FrameStore(newer).fetch([row]))
    with Image.open(second["image_path"]) as im:assert not np.array_equal(np.asarray(im),first)

def test_committed_partition_resume_restores_track_and_rejection_evidence(tmp_path,monkeypatch):
    from materflow.v42 import scan as scanner
    c=config(tmp_path);c.update(chunk_seconds=.4,detection_idle_fps=5.,detection_active_fps=5.)
    ctx={"config":c,"classes":{"beaker":{"id_policy":"auto_instance"}},"stage_signature":"synthetic_version"}
    folder=tmp_path/"internal";folder.mkdir()
    write_json(folder/"shots.json",{f"synthetic:{i}":"one" for i in range(4)})
    pixels=np.random.default_rng(4).integers(0,256,(80,120,3),dtype=np.uint8)
    monkeypatch.setattr(scanner,"source_frames",lambda context:iter((frame(i,i/5),Image.fromarray(pixels)) for i in range(4)))
    instances=[]
    class Detector:
        def __init__(self,*a):
            self.calls=0;self.hits=0;self.corrupt=0;self.version={"synthetic_test":True};instances.append(self)
        def query(self,image):
            self.calls+=1
            return [{"object_class":"beaker","box_xyxy":[.2,.2,.7,.8],"score":.9,"evidence_kind":"detector"}],[{"reason":"synthetic_low_score"}]
    monkeypatch.setattr(scanner,"create_detector",Detector)
    class Progress:
        def update(self,*a,**k):pass
    scanner.scan(ctx,Progress())
    parts=read_json(folder/"scan_partitions.json")
    assert len(parts)==2 and instances[-1].calls==4
    Path(parts[1]["path"]).unlink();Path(parts[1]["path"]).with_suffix(".meta.json").unlink()
    scanner.scan(ctx,Progress())
    assert instances[-1].calls==2
    metrics=read_json(folder/"scan_metrics.json")
    assert metrics["committed_partitions_reused"]==1 and metrics["sampled_frames"]==4
    assert len(read_jsonl(folder/"scan_rejections.jsonl"))==4
    tids={d["track_id"] for g in scanner.scan_rows(ctx) for d in g["display"]}
    assert len(tids)==1
    scanner.scan(ctx,Progress())
    assert instances[-1].calls==0
    assert read_json(folder/"scan_metrics.json")["committed_partitions_reused"]==2

def test_real_evidence_contract_builds_physical_lineage_only_for_carriers(tmp_path):
    from materflow.v42.aggregate import aggregate
    from materflow.v42.derive import derive
    c=config(tmp_path);folder=tmp_path/"internal";folder.mkdir()
    classes={"beaker":{"id_policy":"auto_instance"},"weighing cup":{"id_policy":"auto_instance"}}
    ctx={"config":c,"classes":classes,"actions":{"transfer"},"sources":{"synthetic":{"is_continuous_clip":False}}}
    graphs=[]
    for i,t in enumerate((0,.2,.4)):
        shown=[{"track_id":tid,"object_class":cls,"box_xyxy":box,"evidence_kind":"detector",
                "source_time_s":t,"last_detection_s":t} for tid,cls,box in
                (("source","beaker",[.1,.1,.4,.8]),("target","weighing cup",[.6,.1,.9,.8]))]
        graphs.append({"frame":frame(i,t),"shot_id":"one","display":shown,"detections":[]})
    path=folder/"part.jsonl";write_jsonl(path,graphs);write_json(folder/"scan_partitions.json",[{"path":str(path)}])
    write_json(folder/"shots.json",{f"synthetic:{i}":"one" for i in range(3)})
    write_json(folder/"clock_anchors.json",[])
    write_jsonl(folder/"ocr_results.jsonl",[]);write_json(folder/"packaging_matches.json",{})
    objs=[{"local_id":"obj_1","object_class":"beaker","description":"synthetic source",
           "classification_status":"supported","region_hint_xyxy":[.1,.1,.4,.8]},
          {"local_id":"obj_2","object_class":"weighing cup","description":"synthetic target",
           "classification_status":"supported","region_hint_xyxy":[.6,.1,.9,.8]}]
    write_json(folder/"semantic_results.json",[{"frame":frame(1,.2),"kind":"scene","request_id":"SYNTHETIC",
        "response":{"objects":objs,"relations":[],"flows":[{"source":"obj_1","target":"obj_2",
                    "material_form":"liquid","arrival_visible":True}],"states":[],"texts":[]}}])
    class Progress:
        def update(self,*a,**k):pass
    aggregate(ctx,Progress());derive(ctx,Progress())
    result=read_json(folder/"derived.json")
    assert [e["event_id"] for e in result["events"]]==["E001"]
    assert {m["material_id"] for m in result["materials"]}=={"M001","S001"}
    assert result["lineage"][0]["edge_id"]=="L001"
    assert result["lineage"][0]["source_material_id"]=="M001"
    assert result["lineage"][0]["target_material_id"]=="S001"
    assert result["events"][0]["interval_status"]=="unresolved"
    assert all(m["provenance_status"]=="unresolved" for m in result["materials"])
