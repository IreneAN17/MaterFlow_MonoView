from pathlib import Path
import json
from materflow.v42.ids import Ids
from materflow.v42.progress import Progress
from materflow.v42.tasks import TaskLedger
from materflow.schema import write_json,read_json

def test_allocations_survive_crash_before_json_snapshot(tmp_path):
    p=tmp_path/'id_map.json'
    a=Ids(p,'test');assert a.get('O','first')=='O001';a.db.close()
    assert not p.exists()
    b=Ids(p,'test');assert b.get('O','first')=='O001';assert b.get('O','second')=='O002';b.flush()
    assert read_json(p)['maps']['O']=={'first':'O001','second':'O002'}

def test_legacy_ids_migrate_without_renumbering(tmp_path):
    p=tmp_path/'id_map.json'
    write_json(p,{'run_id':'test','maps':{'O':{'old':'O009'}},'retired':{}})
    a=Ids(p,'test');assert a.get('O','old')=='O009';assert a.get('O','new')=='O010';a.flush()

def test_heartbeat_does_not_rewrite_large_manifest(tmp_path):
    p=tmp_path/'run_summary.json'
    summary={'stages':{'scan':{'status':'running','artifacts':{'x':'z'*100000}}}}
    write_json(p,summary);before=p.read_bytes()
    progress=Progress(p,summary,'scan');progress.update('one task')
    assert p.read_bytes()==before
    assert (tmp_path/'status.json').stat().st_size<4096
    progress.update(status='complete')
    assert read_json(p)['stages']['scan']['status']=='complete'

def test_task_failure_does_not_erase_independent_success(tmp_path):
    ledger=TaskLedger(tmp_path/'tasks.sqlite')
    ledger.set('bad','failed_permanent',error='Unresolved obj_3')
    ledger.set('good','succeeded',output_ref='valid.json')
    assert dict(ledger.db.execute('SELECT task_id,state FROM tasks'))=={'bad':'failed_permanent','good':'succeeded'}
    ledger.set('bad','running',attempt=0)
    assert ledger.db.execute('SELECT state FROM tasks WHERE task_id="good"').fetchone()[0]=='succeeded'
    ledger.close()


def test_semantic_bad_reference_isolated_and_only_failed_retried(tmp_path,monkeypatch):
    import pytest
    from materflow.v42 import semantic
    from materflow.backends import factory
    folder=tmp_path/'internal';folder.mkdir()
    picture=tmp_path/'input.png';picture.write_bytes(b'fixture')
    frames=[{'source_id':'synthetic','frame_id':i,'source_time_s':i,'image_path':str(picture)} for i in range(2)]
    jobs=[{'kind':'scene','frame':f,'frames':[f],'shot_id':'shot','candidates':[],'selection_reasons':['test']} for f in frames]
    monkeypatch.setattr(semantic,'plan_tasks',lambda ctx:jobs)
    monkeypatch.setattr(semantic,'prompt_for',lambda job,ctx:'same visual question')
    monkeypatch.setattr(semantic,'request_key',lambda job,prompt,ctx:'test'+str(job['frame']['frame_id']))
    class Store:
        def __init__(self,ctx):pass
        def fetch(self,rows):return iter(rows)
    monkeypatch.setattr(semantic,'FrameStore',Store)
    good=json.dumps({'objects':[],'relations':[],'texts':[],'flows':[],'states':[]})
    bad=json.dumps({'objects':[],'relations':[{'subject':'camera_wearer','relation':'hold','object':'obj_3'}],'texts':[],'flows':[],'states':[]})
    class Backend:
        version={'synthetic_test':True};last_inference={};healthy=False;calls=[]
        def infer_images(self,images,prompts,**kwargs):
            self.calls.append(len(images))
            return [good if self.healthy or len(images)==2 and i==1 else bad for i in range(len(images))]
    model=Backend();monkeypatch.setattr(factory,'create_backend',lambda c:model)
    c={'output_dir':tmp_path,'semantic_batch_size':2,'semantic_max_attempts':2,'semantic_budget':None}
    ctx={'config':c,'actions':{'hold'},'classes':{},'aliases':{'object_class':{},'scene_relation':{'hold':['hold']}}}
    class Progress:
        def update(self,*a,**k):pass
    with pytest.raises(semantic.PendingWork):semantic.run_semantic(ctx,Progress())
    assert (folder/'cache/semantic/test1.json').is_file()
    assert not (folder/'cache/semantic/test0.json').exists()
    assert read_json(folder/'semantic_failures.json')[0]['request_id']=='test0'
    model.healthy=True;model.calls=[]
    semantic.run_semantic(ctx,Progress())
    assert model.calls==[1]
    assert read_json(folder/'semantic_metrics.json')['cache_hits']==1


def test_framestore_context_hash_computed_once(tmp_path,monkeypatch):
    from materflow.v42 import io
    source=tmp_path/'source';source.write_bytes(b'original bytes')
    calls=[];original=io.file_hash
    def counted(path):calls.append(path);return original(path)
    monkeypatch.setattr(io,'file_hash',counted)
    ctx={'config':{'output_dir':tmp_path},'source_paths':{'s':source}}
    for _ in range(3):io.FrameStore(ctx)
    assert calls==[source]

def test_power_nested_exception_releases_same_thread(monkeypatch):
    from types import SimpleNamespace
    from materflow.v42 import power
    import ctypes,pytest
    calls=[]
    kernel=SimpleNamespace(SetThreadExecutionState=lambda flags:calls.append(flags) or 1)
    monkeypatch.setattr(power,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**kw:kernel)
    with pytest.raises(ValueError):
        with power.keep_awake():
            with power.keep_awake():raise ValueError('failure')
    assert calls==[0x80000001,0x80000000]


def test_same_ocr_pixels_share_compute_not_identity(tmp_path,monkeypatch):
    from PIL import Image
    from materflow.v42 import ocr
    picture=tmp_path/'image.png';Image.new('RGB',(100,60),'white').save(picture)
    frame={'source_id':'synthetic','frame_id':0,'source_time_s':0,'width':100,'height':60,'image_path':str(picture)}
    choices=[{'frame':frame,'track_id':'physical-'+str(i),'object_class':'balance','kind':'display_candidate','box_xyxy':[0,0,1,1]} for i in range(100)]
    monkeypatch.setattr(ocr,'select_regions',lambda c:choices)
    class Store:
        def __init__(self,c):pass
        def fetch(self,rows):return iter(rows)
    monkeypatch.setattr(ocr,'FrameStore',Store)
    class Worker:
        manifest={'synthetic_test':True};starts=1;calls=0;startup_s=0
        def iter_results(self,items,mode):
            for item in items:
                self.calls+=1
                yield {**item,'raw_text':'','model_score':0,'blocks':[],'elapsed':None}
    worker=Worker();monkeypatch.setattr(ocr,'client',lambda c:worker)
    class Progress:
        def update(self,*a,**kw):pass
    ctx={'config':{'output_dir':tmp_path,'ocr_score_threshold':.85,'display_change_threshold':.025}}
    ocr.run_ocr(ctx,Progress())
    rows=list(ocr.records(tmp_path/'internal/ocr_results.jsonl'))
    assert worker.calls==1 and len(rows)==100
    assert len({r['track_id'] for r in rows})==100
    assert read_json(tmp_path/'internal/ocr_metrics.json')['inflight_duplicates_coalesced']==99


def test_worker_crash_replays_only_uncommitted_request(tmp_path):
    import sys
    from materflow.v42.ocr_service import OCRWorkerClient
    script=tmp_path/'runs/v42/ocr_worker.py';script.parent.mkdir(parents=True)
    script.write_text("import sys,json,os\nfrom pathlib import Path\nprint(json.dumps({'type':'ready','manifest':{'fixture':True}}),flush=True)\nfor line in sys.stdin:\n r=json.loads(line);item=r['item'];marker=Path(item['crop_path'])\n if not marker.exists():\n  marker.write_text('one crash');os._exit(7)\n print(json.dumps({'type':'result','request_id':item['request_id'],'result':item}),flush=True)\n",encoding='utf-8')
    worker=OCRWorkerClient({'repo_root':tmp_path,'ocr_python':Path(sys.executable)})
    try:
        values=list(worker.iter_results([{'request_id':'first','crop_path':str(tmp_path/'marker')}],'clock'))
        assert values[0]['request_id']=='first'
        assert worker.starts==2 and worker.restarts==1 and worker.calls==1
    finally:worker.close()


def test_partition_migration_rejects_corrupt_source_before_commit(tmp_path):
    import pytest
    from runs.v42.partition_migrate import migrate
    from materflow.schema import file_hash
    source=tmp_path/'old';folder=source/'internal/scan';folder.mkdir(parents=True)
    data=folder/'part.jsonl';data.write_text('{}\n',encoding='utf-8')
    meta={'path':str(data),'sha256':file_hash(data),'rows':1,'source_id':'s','start_s':0,'end_s':.2,'input_signature':'old',
          'end_state':{'active':{}},'rejected':[{'reason':'synthetic low score'}]}
    write_json(folder/'part.meta.json',meta)
    dest=tmp_path/'migrated';report=migrate(source,dest,True)
    assert report['partitions']==1 and report['max_index_entry_bytes']<2048
    entry=json.loads((dest/'partitions.jsonl').read_text())
    assert 'rejected' not in entry and 'end_state' not in entry
    commit=read_json(entry['commit_ref'])
    assert all(file_hash(Path(path))==digest for path,digest in commit['files'].items())
    data.write_text('corrupted',encoding='utf-8')
    failed=tmp_path/'failed'
    with pytest.raises(ValueError):migrate(source,failed,True)
    assert not (failed/'commits').exists()


def test_worker_protocol_forces_utf8_under_legacy_parent_encoding(tmp_path,monkeypatch):
    import sys
    from materflow.v42.ocr_service import OCRWorkerClient
    monkeypatch.setenv('PYTHONIOENCODING','cp936');monkeypatch.setenv('PYTHONUTF8','0')
    script=tmp_path/'runs/v42/ocr_worker.py';script.parent.mkdir(parents=True)
    code="import sys,json\nprint(json.dumps({'type':'ready','manifest':{'fixture':'中文引擎'}},ensure_ascii=False),flush=True)\nfor line in sys.stdin:\n r=json.loads(line);item=r['item'];print(json.dumps({'type':'result','request_id':item['request_id'],'result':{'raw_text':'−0.10 g 标签'}},ensure_ascii=False),flush=True)\n"
    script.write_text(code,encoding='utf-8')
    worker=OCRWorkerClient({'repo_root':tmp_path,'ocr_python':Path(sys.executable)})
    try:
        rows=list(worker.iter_results([{'request_id':'瓶一','crop_path':'中文路径'}],'regions'))
        assert worker.manifest['fixture']=='中文引擎'
        assert rows[0]['raw_text']=='−0.10 g 标签'
    finally:worker.close()
