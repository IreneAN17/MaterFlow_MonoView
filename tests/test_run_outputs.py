from dataclasses import asdict
from fractions import Fraction
import json
from pathlib import Path
import av
import numpy as np
from PIL import Image
import pytest
from materflow.schema import FrameRecord, read_json, write_json
from materflow.outputs.video import render_annotated_videos
from materflow.perception.relations import extract_frame_relations


def test_annotated_video_preserves_source_frames_and_timeline(tmp_path):
    source=tmp_path/'source.mkv'
    with av.open(str(source),'w') as target:
        stream=target.add_stream('ffv1',rate=24)
        stream.width,stream.height=320,240
        for i in range(48):
            frame=av.VideoFrame.from_ndarray(np.full((240,320,3),80,dtype=np.uint8),format='rgb24')
            frame.pts,frame.time_base=i,Fraction(1,24)
            for packet in stream.encode(frame):
                target.mux(packet)
        for packet in stream.encode():
            target.mux(packet)
    frames=[FrameRecord('s',i,i*24,float(i),'unused.png') for i in range(2)]
    graphs=[{'frame':asdict(f),'detections':[{'detection_id':f'D{i}','object_class':'bottle','score':.8,'box_xyxy':[.1,.5,.4,.9]}]} for i,f in enumerate(frames)]
    write_json(tmp_path/'frame_graphs/v1/s.json',graphs)
    write_json(tmp_path/'video_graphs/v1/s.json',{'detection_to_track':{'D0':'T1','D1':'T1'}})
    context={'config':{'output_dir':tmp_path,'sample_fps':1},'source_paths':{'s':source},'evidence_index':{},'identities':{}}
    paths=render_annotated_videos({'observations':[],'events':[]},frames,context)
    with av.open(str(paths[0])) as annotated:
        output=list(annotated.decode(video=0))
        assert len(output)==48
        assert float(output[0].pts*output[0].time_base)==0
        assert float(output[-1].pts*output[-1].time_base)==pytest.approx(47/24,abs=.002)
    assert read_json(paths[1])['human_reference_used'] is False
    assert read_json(paths[1])['source_frames_encoded']==48


def test_caption_checkpoint_is_bound_to_inputs_not_filenames(tmp_path):
    frames=[]
    for i in range(2):
        path=tmp_path/f'{i}.png'; Image.new('RGB',(16,16)).save(path)
        frames.append(FrameRecord('s',i,i,float(i),str(path)))
    empty=json.dumps({k:[] for k in ('objects','relations','texts','flows','states')})
    context={'config':{'output_dir':tmp_path,'clip_review':False},'model_tag':'fixture','scene_prompt':'Observe.',
             'run_fingerprint':'R1','aliases':{'object_class':{},'scene_relation':{}}}
    class Backend:
        version={'fixture':True}
        def __init__(self,fail=False):
            self.calls=[]; self.fail=fail
        def infer_images(self,images,prompt,**kwargs):
            self.calls.append(Path(images[0]).name)
            return ['invalid JSON' if self.fail and Path(images[0]).name=='1.png' else empty]
    first=Backend(fail=True)
    with pytest.raises(ValueError,match='both raw attempts saved'):
        extract_frame_relations(frames,first,context)
    assert first.calls==['0.png','1.png','1.png']
    partial=read_json(tmp_path/'captions/fixture/s.json')
    assert len(partial[1]['raw_attempts'])==2
    second=Backend()
    extract_frame_relations(frames,second,context)
    assert second.calls==['1.png']
    context['run_fingerprint']='R2'
    third=Backend()
    extract_frame_relations(frames,third,context)
    assert third.calls==['0.png','1.png']


def test_visible_stirring_target_can_remain_unbound_to_manifest():
    from materflow.materials.events import propose_lab_events
    existence={'observation_id':'O1','source_id':'s','start_s':1.,'end_s':1.,'track_id':'T1',
               'object_id':None,'payload':{'kind':'object','object_class':'beaker'},'evidence_refs':['V1']}
    motion={**existence,'observation_id':'O2','payload':{'kind':'state','state':'stirring_motion'}}
    existence['signal_type']=motion['signal_type']='visual_state'
    context={'config':{'sample_fps':5},'identities':{},'sources':{'s':{'is_continuous_clip':None}}}
    candidates=propose_lab_events([existence,motion],context)
    assert len(candidates)==1 and candidates[0]['action']=='stir'
    assert candidates[0]['target_container_id'] is None
    assert candidates[0]['target_track_id']=='T1'
    existence['payload']['object_class']='stirrer'
    assert propose_lab_events([existence,motion],context)==[]


def test_caption_batch_keeps_independent_prompts_and_persists_all_raws(tmp_path):
    frames=[]
    for i in range(3):
        p=tmp_path/f"{i}.png"; Image.new("RGB",(16,16)).save(p)
        frames.append(FrameRecord("s",i,i,float(i),str(p)))
    empty=json.dumps({k:[] for k in ("objects","relations","texts","flows","states")})
    class Backend:
        version={"fixture":True}
        def __init__(self): self.calls=[]
        def infer_images(self,images,prompt,**kwargs):
            self.calls.append((list(images),prompt))
            return [empty]*len(images)
    b=Backend()
    context={"config":{"output_dir":tmp_path,"clip_review":False,"caption_batch_size":2},
             "model_tag":"fixture","scene_prompt":"Observe.","run_fingerprint":"R",
             "aliases":{"object_class":{},"scene_relation":{}}}
    entries=extract_frame_relations(frames,b,context)
    assert [len(images) for images,_ in b.calls]==[2,1]
    assert isinstance(b.calls[0][1],list)
    assert "frame_id=0" in b.calls[0][1][0] and "frame_id=1" in b.calls[0][1][1]
    assert [e["frame"]["frame_id"] for e in entries]==[0,1,2]
    assert all(e["response"] and len(e["raw_attempts"])==1 for e in entries)
    b2=Backend(); extract_frame_relations(frames,b2,context)
    assert b2.calls==[]


def test_model_json_envelope_never_repairs_or_accepts_prose():
    from materflow.perception.relations import decode_model_json,parse_relation_output
    data={k:[] for k in ("objects","relations","texts","flows","states")}
    raw=json.dumps(data)
    assert parse_relation_output("```json\n"+raw+"\n```")["objects"]==[]
    for broken in ("Here is JSON: "+raw,raw[:-1],"```json\n"+raw[:-1]+"\n```", "{\"x\":NaN}"):
        with pytest.raises(ValueError): decode_model_json(broken)


def test_caption_sampling_uses_timestamps_and_keeps_dense_frame_ids():
    from materflow.video.sampling import select_caption_frames
    frames=[FrameRecord("s",100+i,i,t,"unused.png") for i,t in enumerate([3.2,3.4,3.9,4.25,4.4,5.25])]
    selected=select_caption_frames(frames,{"sample_fps":5,"caption_fps":1})
    assert [f.frame_id for f in selected]==[100,103,105]
    assert len(frames)==6
    whole=[FrameRecord("s",i,i*6,i/5,"unused.png") for i in range(911)]
    assert len(select_caption_frames(whole,{"sample_fps":5,"caption_fps":1}))==183
    untimed=[FrameRecord("s",0,0,None,"unused.png")]
    assert select_caption_frames(untimed,{"sample_fps":5})==untimed
    with pytest.raises(ValueError,match="timestamps"):
        select_caption_frames(untimed,{"sample_fps":5,"caption_fps":1})


def test_sparse_captions_keep_dense_detection_without_copying_facts(tmp_path):
    from materflow.perception.grounding import ground_relation_objects
    from materflow.perception.relations import parse_relation_output
    from materflow.materials.ocr import extract_lab_observations
    frames=[]
    for i in (0,1,5):
        image=tmp_path/f"{i}.png";Image.new("RGB",(32,32)).save(image)
        frames.append(FrameRecord("s",i,i*6,i/5,str(image)))
    data={k:[] for k in ("objects","relations","texts","flows","states")}
    data["objects"]=[{"local_id":"obj_1","object_class":"beaker","description":"visible glass","region_hint_xyxy":[.1,.1,.8,.8]}]
    data["texts"]=[{"object":"obj_1","kind":"label","raw_text":"VISIBLE","value":None,"unit":None,"reading_kind":"unknown"}]
    response=parse_relation_output(json.dumps(data))
    captions=[{"frame":asdict(f),"request_id":f"Q{f.frame_id}","response":response} for f in (frames[0],frames[2])]
    class DetectorFixture:
        version={"fixture":True}
        def __init__(self):self.calls=[]
        def detect(self,image,label):
            self.calls.append(label)
            return [{"object_class":label,"box_xyxy":[.1,.1,.8,.8],"score":.9}]
    detector=DetectorFixture()
    context={"config":{"sample_fps":5,"caption_fps":1,"roi_reread":False}}
    graphs=ground_relation_objects(frames,captions,context,detector)
    assert detector.calls==["beaker"]*3
    assert graphs[1]["captions"]==[] and graphs[1]["relations"]==[]
    assert graphs[1]["query_prior_frames"]==[0,5]
    tracks={"frame_graphs":graphs,"local_to_track":{},"detection_to_track":{d["detection_id"]:"T1" for g in graphs for d in g["detections"]}}
    obs=extract_lab_observations(frames,tracks,None,context)
    middle=[o for o in obs if o["start_s"]==.2]
    assert len(middle)==1 and middle[0]["payload"]["model_source"]=="GroundingDINO"
    assert middle[0]["payload"]["kind"]=="object" and middle[0]["track_id"]=="T1"
    assert all(o["signal_type"]!="ocr_text" for o in middle)
    assert all(context["evidence_index"][r]["frame_id"]==1 for o in middle for r in o["evidence_refs"])


def test_checkpoint_import_requires_original_signature_and_equal_raw_parse(tmp_path):
    import copy
    from types import SimpleNamespace
    from materflow.perception.relations import _reuse_caption,parse_relation_output
    from materflow.schema import file_hash,stable_id
    image=tmp_path/"image.png";Image.new("RGB",(16,16)).save(image)
    frame=FrameRecord("s",0,0,0.,str(image))
    raw=json.dumps({k:[] for k in ("objects","relations","texts","flows","states")})
    context={"aliases":{}};backend=SimpleNamespace(version={"fixture":"v1"});prompt="Observe this image."
    digest=file_hash(image)
    origin=stable_id("REQ","OLD",prompt,digest,backend.version)
    old={"frame":asdict(frame),"raw":raw,"response":parse_relation_output(raw,context),"request_signature":origin}
    imported={"entries":{("s",0):old},"previous_run_fingerprint":"OLD","path":"audit.json","entries_sha256":"audited"}
    result=_reuse_caption(frame,prompt,"NEW",digest,context,backend,imported)
    assert result["request_signature"]=="NEW" and result["reuse_provenance"]["original_request_signature"]==origin
    assert old["request_signature"]==origin
    assert _reuse_caption(frame,prompt+"changed","NEW",digest,context,backend,imported) is None
    assert _reuse_caption(frame,prompt,"NEW",digest,context,SimpleNamespace(version={"fixture":"v2"}),imported) is None
    Image.new("RGB",(16,16),"red").save(image)
    assert _reuse_caption(frame,prompt,"NEW",file_hash(image),context,backend,imported) is None
    changed=copy.deepcopy(imported);changed["entries"][("s",0)]["response"]["unresolved"]=[{"changed":True}]
    with pytest.raises(ValueError,match="parse changed"):
        _reuse_caption(frame,prompt,"NEW",digest,context,backend,changed)


def test_sparse_caption_windows_use_only_present_input_frame_ids(tmp_path):
    frames=[]
    for i in (0,5):
        p=tmp_path/f"{i}.png";Image.new("RGB",(16,16)).save(p)
        frames.append(FrameRecord("s",i,i*6,i/5,str(p)))
    raw=json.dumps({"objects":[{"local_id":"obj_1","object_class":"beaker","description":"visible"}],"relations":[],"texts":[],"flows":[],"states":[{"object":"obj_1","state":"container_tilted","detail":"tilt visible"}]})
    class BackendFixture:
        version={"fixture":True}
        def infer_images(self,images,prompt,**kwargs):return [raw]*len(images)
        def infer_clip(self,*args):pytest.fail("Unknown continuity must not receive temporal review")
    context={"config":{"output_dir":tmp_path,"clip_review":True,"max_clip_reviews_per_source":10},
             "model_tag":"fixture","scene_prompt":"Observe.","aliases":{},"run_fingerprint":"R",
             "sources":{"s":{"is_continuous_clip":None}},
             "windows":[{"source_id":"s","frame_ids":list(range(10)),"times_s":[i/5 for i in range(10)],"start":0,"end":10}]}
    entries=extract_frame_relations(frames,BackendFixture(),context)
    assert [e["frame"]["frame_id"] for e in entries]==[0,5]


def test_json_literal_label_whitespace_is_lossless_not_structural_repair():
    from materflow.perception.relations import decode_model_json
    value="Label\t−7.297 g\n45%\rnext line"
    wire='{"raw_text":"'+value+'"}'
    assert decode_model_json(wire)=={"raw_text":value}
    assert decode_model_json(json.dumps({"raw_text":value},ensure_ascii=False))==decode_model_json(wire)
    for broken in (wire[:-1],wire[:-2],'{"raw_text":"a'+chr(0)+'b"}', '{"raw_text":'+chr(0)+'null}', '{"raw_text":"a\nb","value":NaN}'):
        with pytest.raises(ValueError):decode_model_json(broken)
