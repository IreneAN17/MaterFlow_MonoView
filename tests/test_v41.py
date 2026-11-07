"""Contract fixtures here are synthetic; production results use real models only."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import pytest
from openpyxl import load_workbook
from materflow.config import load_config
from materflow.schema import FrameRecord,write_json,read_json,read_jsonl
from materflow.v41.context import load_context,FIELDS,CORE,core_evidence
from materflow.v41.runtime import Ids,signature
from materflow.v41.clock import parse_clock,map_time
from materflow.v41.vision import filter_conflicts
from materflow.v41.domain import bind_and_aggregate,derive_events,material_lineage
from materflow.v41.export import export
from materflow.v41.video import display_lines
ROOT=Path(__file__).resolve().parents[1]

class Progress:
    def update(self,*args,**kwargs):pass

@pytest.fixture
def ctx(tmp_path):
    source=tmp_path/'clip.mp4';source.touch()
    manifest=read_json(ROOT/'docs/V4.1/example_manifest_v4.1.json');manifest['run_id']='UNIT_V41'
    manifest['capture']['sources']=[{'source_id':'clear_video','file':str(source),'is_continuous_clip':False,'time_scale':None}]
    write_json(tmp_path/'manifest.json',manifest)
    c={'run_id':'UNIT_V41','registry_path':ROOT/'docs/V4.1/example_registry_v4.1.xlsx','manifest_path':tmp_path/'manifest.json','repo_root':ROOT,'prompt_template_path':ROOT/'configs/prompts/lab_frame_v4_1.template.txt','output_dir':tmp_path,'sample_fps':5,'caption_interval_s':2.5,'roi_reread':False,'model_name':'unit','max_track_gap_s':1.}
    return load_context(c)

def fixture_tracking(ctx,times=(0,.2,.4),categories=('beaker','balance'),shots=None):
    frames=[];graphs=[];tracks=[];mapping={};display={}
    for i,t in enumerate(times):
        f=FrameRecord('clear_video',i,int(t*30),t,'unit_fixture.png',width=100,height=80);frames.append(f);dets=[]
        for k,category in enumerate(categories):
            tid=f't{k}';did=f'd{i}_{k}';box=[.12+k*.42,.2,.4+k*.42,.8]
            det={'detection_id':did,'object_class':category,'box_xyxy':box,'score':.9}
            dets.append(det);mapping[did]=tid
            if i==0:tracks.append({'track_id':tid,'object_class':category,'history':[],'shot_id':'shot1'})
            tracks[k]['history'].append({**asdict(f),**det})
        graphs.append({'frame':asdict(f),'detections':dets,'captions':[],'assignments':{}})
    shot_map={f'clear_video:{i}':(shots[i] if shots else 'shot1') for i in range(len(times))}
    write_json(ctx['config']['output_dir']/'shots.json',shot_map)
    return frames,{'tracks':tracks,'frame_graphs':graphs,'detection_to_track':mapping,'local_to_track':{},'display':display}

def test_model_context_excludes_registered_facts_and_reagents(ctx):
    assert ctx['registry']['Reagents']
    assert ctx['actions']=={'stir','transfer','hold','filter'}
    text=json.dumps(ctx['qwen_context'])
    assert 'R_FORMAMIDE' not in text and 'SOP' not in text and 'registered_instance' not in text
    assert len(ctx['classes'])==12
    assert sum(r['id_policy']=='class_only' for r in ctx['classes'].values())==4

def test_v41_headers_match_actual_templates():
    for filename,names in {'example_observations_v4.1.xlsx':['Observations'],'example_events_v4.1.xlsx':['Events'],'example_lineage_v4.1.xlsx':['Materials','Lineage']}.items():
        book=load_workbook(ROOT/'docs/V4.1'/filename,read_only=True)
        for name in names+['Field Evidence','说明']:assert list(next(book[name].values))==FIELDS[name]
        book.close()

def test_public_ids_persist_without_sorting_or_reuse(tmp_path):
    path=tmp_path/'ids.json';a=Ids(path,'r')
    assert a.get('O','later')=='O001';assert a.get('O','earlier')=='O002'
    assert a.get('beaker','track-b')=='beaker_1'
    b=Ids(path,'r');assert b.get('O','later')=='O001';assert b.get('beaker','track-c')=='beaker_2'
    for i in range(998):b.get('E',str(i))
    assert b.get('E','998')=='E999';assert b.get('E','999')=='E1000'
    with pytest.raises(ValueError):Ids(path,'other')

@pytest.mark.parametrize('raw,expected',[('2026/09/08 10:00:32','2026-09-08 10:00:32'),('2026/09/0810:00:32','2026-09-08 10:00:32'),('2026/02/30 10:00:32',None),('unreadable',None)])
def test_clock_parser_keeps_unknown(raw,expected):assert parse_clock(raw)==expected

def test_clock_mapping_does_not_cross_cut_or_extrapolate():
    anchors=[{'source_id':'s','frame_id':0,'source_time_s':0.,'datetime':'2026-01-01 23:59:59','timezone':'Asia/Shanghai'},{'source_id':'s','frame_id':1,'source_time_s':1.,'datetime':'2026-01-02 00:00:00','timezone':'Asia/Shanghai'}]
    assert map_time(.5,'s',anchors,{'s:0':'a','s:1':'b'})['status']=='unresolved'
    assert map_time(.5,'s',anchors,{'s:0':'a','s:1':'a'})['datetime']=='2026-01-01 23:59:59'
    assert map_time(2,'s',anchors,{'s:0':'a','s:1':'a'})['status']=='unresolved'
    anchors[1]['datetime']='2026-01-02 08:00:00'
    assert map_time(.5,'s',anchors,{'s:0':'a','s:1':'a'})['status']=='unresolved'

def test_conflicts_remove_generic_duplicate_not_other_objects():
    d=[{'object_class':'container','box_xyxy':[.1,.1,.4,.6],'score':.8},{'object_class':'beaker','box_xyxy':[.1,.1,.4,.6],'score':.79},{'object_class':'beaker','box_xyxy':[.6,.1,.9,.6],'score':.85}]
    kept,rejected=filter_conflicts(d,{})
    assert len(kept)==2 and all(x['object_class']=='beaker' for x in kept)
    assert rejected[0]['reason']=='mutually_exclusive_category_conflict'

def test_aggregate_presence_ids_and_export(ctx):
    frames,tracking=fixture_tracking(ctx)
    rows=bind_and_aggregate(frames,tracking,None,ctx,Progress())
    assert len(rows)==2
    vessel=next(r for r in rows if r['object_class']=='beaker');instrument=next(r for r in rows if r['instrument_class']=='balance')
    assert vessel['object_id']=='beaker_1';assert vessel['start_s']==0 and vessel['end_s']==.4
    assert instrument['object_id'] is None and instrument['object_class'] is None
    assert instrument['field_evidence']['object_id']['field_status']=='not_applicable'
    assert len(read_jsonl(ctx['config']['output_dir']/'raw_observations.jsonl'))==6
    result={'observations':rows,'events':[],'materials':[],'lineage':[]}
    paths=export(result,ctx);book=load_workbook(paths[0],read_only=True)
    assert book['Observations'].max_row==3 and book['Field Evidence'].max_row==9
    assert list(next(book['Observations'].values))==FIELDS['Observations'];book.close()
    before=[r['observation_id'] for r in rows]
    assert [r['observation_id'] for r in bind_and_aggregate(frames,tracking,None,ctx,Progress())]==before

def test_presence_not_merged_over_gap_or_cut(ctx):
    frames,tracking=fixture_tracking(ctx,times=(0,.2,.4,1.,1.2,1.4),categories=('beaker',))
    rows=bind_and_aggregate(frames,tracking,None,ctx,Progress());assert len(rows)==2
    assert [(r['start_s'],r['end_s']) for r in rows]==[(0,.4),(1.,1.4)]
    frames,tracking=fixture_tracking(ctx,categories=('beaker',),shots=['a','a','b'])
    rows=bind_and_aggregate(frames,tracking,None,ctx,Progress());assert len(rows)==2

def test_readout_survives_unknown_sample(ctx):
    frames,tracking=fixture_tracking(ctx,categories=('balance',))
    g=tracking['frame_graphs'][0]
    response={'objects':[{'local_id':'obj_1','object_class':'balance','description':'readable display','classification_status':'supported'}],'relations':[],'states':[],'flows':[],'texts':[{'object':'obj_1','raw_text':'12.34 g','kind':'display','value':12.34,'unit':'g','reading_kind':'display'}]}
    g['captions']=[{'request_id':'q0','response':response}];g['assignments']={'q0:obj_1':'d0_0'};tracking['local_to_track']={'q0:obj_1':'t0'}
    rows=bind_and_aggregate(frames,tracking,None,ctx,Progress());mass=next(r for r in rows if r['signal_type']=='mass_g')
    assert mass['observed_value']==12.34 and mass['instrument_class']=='balance' and mass['object_id'] is None
    assert mass['field_evidence']['object_id']['field_status']=='unresolved'
    assert mass['evidence_refs']

def test_event_span_is_not_confirmed_duration_and_no_chemical_lineage(ctx):
    ctx['identities']={'t1':{'object_id':'bottle_1'},'t2':{'object_id':'beaker_1'}}
    raw=[{'uid':'raw1','observation_id':'raw1','source_id':'clear_video','start_s':2.,'end_s':2.,'signal_type':'material_flow','track_id':'t2','payload':{'source_track_id':'t1','target_track_id':'t2','arrival_visible':True,'material_form':'liquid'},'evidence_refs':['v1'],'shot_id':'shot1'}]
    ledger=Ids(ctx['config']['output_dir']/'id_map.json','UNIT_V41')
    events=derive_events([],raw,ctx,ledger,{'raw1':'O001'})
    assert events[0]['event_id']=='E001' and events[0]['start_s']==events[0]['end_s']==2.
    assert events[0]['interval_status']=='unresolved' and events[0]['source_container_id']=='bottle_1'
    materials,edges=material_lineage(events,ctx,ledger,{'tracks':[]})
    assert [m['material_id'] for m in materials]==['M001','S001']
    assert all(m['provenance_status']=='unresolved' and 'reagent_ids' not in m for m in materials)
    assert edges[0]['edge_id']=='L001' and edges[0]['source_material_id']=='M001'
    ctx['identities']['t1']['object_id']=None
    unknown=derive_events([],raw,ctx,ledger,{'raw1':'O001'})
    assert material_lineage(unknown,ctx,ledger,{'tracks':[]})==([],[])

def test_presentation_change_keeps_caption_cache_key(ctx,tmp_path):
    c=ctx['config'];c.update(model_name='Qwen/Qwen3.5-4B',model_revision='fixed',registry_path=ROOT/'docs/V4.1/example_registry_v4.1.xlsx')
    summary={'stages':{'prepare':{'artifacts':{'frame_index.jsonl':'a','clock_anchors.json':'old','context.json':'b'}},'consolidate':{'artifacts':{'observations.jsonl':'c'}}}}
    before=signature('caption',ctx,summary);derive=signature('derive',ctx,summary)
    c['video_crf']=28
    assert signature('caption',ctx,summary)==before and signature('derive',ctx,summary)!=derive
    summary['stages']['prepare']['artifacts']['clock_anchors.json']='new'
    assert signature('caption',ctx,summary)==before
    summary['stages']['prepare']['artifacts']['frame_index.jsonl']='changed'
    assert signature('caption',ctx,summary)!=before

def test_display_text_is_bounded_and_single_line_per_entry():
    lines=display_lines('first\nsecond\t'+('x'*1000),width=50,maximum=2)
    assert len(lines)==2 and all('\n' not in x and len(x)<=50 for x in lines) and lines[-1].endswith('...')


def test_partial_detector_label_is_mapped_only_when_unambiguous():
    from materflow.v41.vision import canonical_label
    assert canonical_label('cup',['weighing cup','bottle'])=='weighing cup'
    assert canonical_label('paper',['weighing paper','filter paper']) is None
    assert canonical_label('beaker bottle',['beaker','bottle']) is None
    assert canonical_label('keyboard',['beaker','bottle']) is None


def test_batched_sam_cache_and_late_video_object_tensor_contract(tmp_path):
    """Random tiny networks test tensor interfaces only, not production inference."""
    import numpy as np
    import torch
    from collections import Counter
    from PIL import Image
    from transformers import (Sam2Config,Sam2Model,Sam2Processor,Sam2ImageProcessorFast,Sam2VideoConfig,Sam2VideoModel,Sam2VideoProcessor,Sam2VideoVideoProcessor,Dinov2Config,Dinov2Model,BitImageProcessorFast)
    from materflow.v41.vision import BatchTrackingModels
    torch.set_num_threads(2)
    vision={'backbone_config':{'hidden_size':12,'image_size':[128,128],'embed_dim_per_stage':[12,24,48,96],'num_attention_heads_per_stage':[1,2,4,8],'blocks_per_stage':[1,1,1,1],'global_attention_blocks':[]},'backbone_channel_list':[96,48,24,12],'backbone_feature_sizes':[[32,32],[16,16],[8,8]]}
    kw={'vision_config':vision,'prompt_encoder_config':{'image_size':128},'image_size':128,'memory_attention_num_layers':1,'memory_attention_feed_forward_hidden_size':256,'memory_attention_rope_feat_sizes':[8,8]}
    m=BatchTrackingModels.__new__(BatchTrackingModels);m.torch=torch;m.device='cpu';m.dtype=torch.float32;m.metrics=Counter();m.cache=tmp_path;m.version={'unit':'random-tensor-contract'}
    processor=Sam2ImageProcessorFast(size={'height':128,'width':128},mask_size={'height':32,'width':32})
    m.image_processor=Sam2Processor(processor);m.image_model=Sam2Model(Sam2Config(**kw)).eval();m.video_processor=Sam2VideoProcessor(processor,Sam2VideoVideoProcessor(size={'height':128,'width':128}));m.video_model=Sam2VideoModel(Sam2VideoConfig(**kw)).eval()
    m.dino_processor=BitImageProcessorFast(size={'shortest_edge':32},crop_size={'height':32,'width':32});m.dino_model=Dinov2Model(Dinov2Config(hidden_size=32,num_hidden_layers=1,num_attention_heads=4,image_size=32,patch_size=8)).eval()
    actual_calls=Counter();m.image_model.vision_encoder.register_forward_hook(lambda *args:actual_calls.update(['image_encoder']))
    image=Image.fromarray(np.zeros((48,64,3),dtype=np.uint8));path=tmp_path/'unit.png';image.save(path)
    detections=[{'detection_id':'a','box_xyxy':[.1,.1,.4,.8]},{'detection_id':'b','box_xyxy':[.6,.1,.9,.8]}]
    first=m.batch_features(image,detections,str(path));second=m.batch_features(image,detections,str(path))
    assert len(first)==2 and first[0][0].shape==(48,64) and len(first[0][1])==32
    assert actual_calls['image_encoder']==1 and m.metrics['dino_batch_forwards']==1 and m.metrics['feature_cache_hits']==1
    np.testing.assert_array_equal(first[0][0],second[0][0])
    cache=next(tmp_path.glob('*.npz'));cache.write_bytes(b'incomplete cache fixture')
    m.batch_features(image,detections,str(path))
    assert actual_calls['image_encoder']==2 and m.metrics['feature_cache_rejected']==1

    session=m.video_processor.init_video_session(video=[image,image.copy(),image.copy()],inference_device='cpu',inference_state_device='cpu',video_storage_device='cpu',dtype=torch.float32)
    m.video_processor.add_inputs_to_inference_session(session,frame_idx=0,obj_ids=[1,2],input_boxes=[[[6,5,25,35],[38,5,58,35]]],original_size=(48,64))
    assert set(m.video_boxes(session,0))=={1,2}
    assert set(m.video_boxes(session,1))=={1,2}
    m.video_processor.add_inputs_to_inference_session(session,frame_idx=1,obj_ids=[3],input_boxes=[[[22,7,36,30]]],original_size=(48,64))
    assert set(m.video_boxes(session,1))=={1,2,3}
    assert set(m.video_boxes(session,2))=={1,2,3}


def test_sparse_detector_uses_explicit_sam_evidence_without_copying_text(ctx):
    frames,tracking=fixture_tracking(ctx,times=(0,.2,.4,.6,.8),categories=('beaker',))
    for i in (1,3):
        tracking['frame_graphs'][i]['detections']=[]
        tracking['display'][f'clear_video:{i}']=[{'track_id':'t0','object_class':'beaker','box_xyxy':[.12,.2,.4,.8],'evidence_kind':'SAM2_propagated','source_time_s':frames[i].source_time_s}]
    tracking['tracks'][0]['history']=[h for h in tracking['tracks'][0]['history'] if h['frame_id'] not in (1,3)]
    rows=bind_and_aggregate(frames,tracking,None,ctx,Progress())
    assert len(rows)==1 and rows[0]['start_s']==0 and rows[0]['end_s']==.8
    evidence=read_json(ctx['config']['output_dir']/'visual_evidence_index.json')
    kinds=[evidence[r].get('evidence_kind') for r in rows[0]['visual_evidence_refs']]
    assert kinds.count('video_segmentation')==2 and kinds.count('detector')==3
    assert all(o['signal_type']=='visual_state' for o in read_jsonl(ctx['config']['output_dir']/'raw_observations.jsonl'))


@pytest.mark.parametrize('raw,expected',[('2026/0908 10:00:43','2026-09-08 10:00:43'),('2026/09/0810:0044','2026-09-08 10:00:44'),('2026/09/0810-00:48','2026-09-08 10:00:48'),('2026/09/08 10:00:4',None),('DATE2026/09/08 10:00:44',None)])
def test_clock_normalizes_separators_without_adding_digits(raw,expected):
    from materflow.v41.time_formats import complete_clock
    assert complete_clock(raw)[0]==expected


def test_silent_renderer_preserves_variable_pts(tmp_path):
    import av
    import numpy as np
    from fractions import Fraction
    from materflow.v41.video import render
    source=tmp_path/'vfr.mp4'
    with av.open(str(source),'w') as container:
        stream=container.add_stream('libx264',rate=30);stream.width=96;stream.height=64;stream.pix_fmt='yuv420p';stream.codec_context.time_base=Fraction(1,1000)
        for pts in (0,40,100,120,250):
            frame=av.VideoFrame.from_ndarray(np.zeros((64,96,3),dtype=np.uint8),format='rgb24');frame.pts=pts;frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    with av.open(str(source)) as container:
        original=[Fraction(f.pts)*f.time_base for f in container.decode(video=0)]
    out=tmp_path/'result';out.mkdir();write_json(out/'shots.json',{});write_json(out/'time_mapping.json',{'anchors':[]})
    frames=[FrameRecord('s',0,0,float(original[0]),'unit_fixture.png'),FrameRecord('s',4,4,float(original[-1]),'unit_fixture.png')]
    context={'config':{'output_dir':out,'sample_fps':5,'ranges_s':[]},'identities':{},'source_paths':{'s':source}}
    render({'observations':[],'events':[]},{'display':{}},frames,context,Progress())
    with av.open(str(out/'s_annotated.mp4')) as container:
        assert len(container.streams.audio)==0
        actual=[Fraction(f.pts)*f.time_base for f in container.decode(video=0)]
    assert len(actual)==5 and [t-actual[0] for t in actual]==[t-original[0] for t in original]


def test_physical_alias_survives_later_category_clarification(tmp_path):
    from materflow.v41.runtime import Ids
    ids=Ids(tmp_path/'id_map.json','run')
    assert ids.instance('container','same-track')=='container_1'
    ids=Ids(tmp_path/'id_map.json','run')
    assert ids.instance('beaker','same-track')=='container_1'
    assert ids.instance('beaker','new-track')=='beaker_1'
    assert ids.data['instances']['same-track']['current_class']=='beaker'


def test_reactivated_record_does_not_keep_old_merge_redirect(tmp_path):
    from materflow.v41.runtime import Ids
    ids=Ids(tmp_path/'id_map.json','run')
    assert ids.get('O','old')=='O001'
    ids.reconcile('O',[],{'O001':'O002'})
    assert ids.get('O','old')=='O001'
    assert 'O001' not in ids.data['redirects'] and 'old' not in ids.data['retired']['O']


def test_inflight_code_change_quarantines_visual_cache(tmp_path,monkeypatch):
    from materflow.v41 import pipeline
    cache=tmp_path/'cache/ground';cache.mkdir(parents=True);(cache/'fixture.txt').write_text('synthetic cache marker')
    context={'config':{'output_dir':tmp_path}}
    monkeypatch.setattr(pipeline,'signature',lambda *a:'same')
    pipeline.ensure_frozen_dependencies('ground',context,{},'same')
    assert cache.exists()
    monkeypatch.setattr(pipeline,'signature',lambda *a:'changed')
    with pytest.raises(RuntimeError,match='changed while running'):
        pipeline.ensure_frozen_dependencies('ground',context,{},'same')
    assert not cache.exists()
    preserved=list((tmp_path/'diagnostics').glob('changed_dependencies_ground_*'))
    assert len(preserved)==1 and (preserved[0]/'fixture.txt').read_text()=='synthetic cache marker'
