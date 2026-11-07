"""Legacy visual-stage benchmark on the exact selected images. No reference annotations."""
from pathlib import Path
from dataclasses import asdict
import argparse,copy,json,sys,time,gc,os
from PIL import Image,ImageChops
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.v41.runtime import gpu_sample
from materflow.config import load_config
from materflow.schema import FrameRecord,read_json,read_jsonl,write_json,file_hash
from materflow.materials.context import load_run_context
from materflow.video.sampling import build_time_windows

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);parser.add_argument("--stage",choices=("ground","consolidate"),required=True);args=parser.parse_args()
    selected_config=load_config(args.config);folder=selected_config['output_dir']/'baseline';folder.mkdir(exist_ok=True)
    old=load_config(ROOT/'output/clear_video/run.yaml');old['output_dir']=folder;context=load_run_context(old)
    frames=[FrameRecord(**r) for r in read_jsonl(selected_config['output_dir']/'frame_index.jsonl')]
    context['frames']=frames;context['windows']=build_time_windows(frames,old)
    import torch
    torch.set_num_threads(old.get('torch_num_threads',4))
    captions=read_json(ROOT/'output/clear_video/captions/Qwen_Qwen3.5-4B/clear_video.json');bytime={round(f.source_time_s,6):f for f in frames}
    used=[];provenance=[]
    for entry in captions:
        f=bytime.get(round(entry['frame']['source_time_s'],6))
        if f is None:continue
        with Image.open(entry['frame']['image_path']) as original,Image.open(f.image_path) as new:
            if original.size!=new.size or ImageChops.difference(original.convert('RGB'),new.convert('RGB')).getbbox() is not None:raise ValueError('Legacy caption image differs from benchmark image')
        provenance.append({'original_request_signature':entry['request_signature'],'original_frame':entry['frame'],'benchmark_frame':asdict(f),'pixel_identical':True})
        e=copy.deepcopy(entry);e['frame']=asdict(f);used.append(e)
    write_json(folder/'caption_cache_provenance.json',{'source_path':'output/clear_video/captions/Qwen_Qwen3.5-4B/clear_video.json','source_sha256':file_hash(ROOT/'output/clear_video/captions/Qwen_Qwen3.5-4B/clear_video.json'),'policy':'Original V4.0 captions reused only for legacy baseline; no V4.1 inference or caption timing claimed','entries':provenance})
    gpu_start=gpu_sample();start=time.perf_counter()
    def checkpoint(**values):
        write_json(folder/(args.stage+'_status.json'),{'stage':args.stage,'pid':os.getpid(),'elapsed_s':time.perf_counter()-start,**values})
    if args.stage=='ground':
        from materflow.perception.grounding import GroundingDINO,ground_relation_objects
        model=GroundingDINO(old);load_s=time.perf_counter()-start;count=[0];detect=model.detect
        def measured(*args,**kwargs):
            result=detect(*args,**kwargs);count[0]+=1
            if count[0]%25==0:
                checkpoint(status='running',forward_calls=count[0]);print(f"Legacy ground: {count[0]} forwards",flush=True)
            return result
        model.detect=measured;infer_start=time.perf_counter();graphs=ground_relation_objects(frames,used,context,model);inference_s=time.perf_counter()-infer_start
        write_json(folder/'frame_graphs.json',graphs);metrics={'model_load_s':load_s,'inference_s':inference_s,'forward_calls':count[0],'frames':len(frames),'detections':sum(len(g['detections']) for g in graphs),'model':model.version}
    else:
        from materflow.tracking.objects import TrackingModels,consolidate_object_tracks,link_window_tracks
        model=TrackingModels(old);load_s=time.perf_counter()-start;segment=model.segment_and_embed;propagate=model.propagate;counts={'image_encoder_forwards':0,'dino_forwards':0,'single_object_sessions':0}
        def measured_segment(*args,**kwargs):
            result=segment(*args,**kwargs);counts['image_encoder_forwards']+=1;counts['dino_forwards']+=1
            if counts['image_encoder_forwards']%25==0:
                checkpoint(status='running',counts=counts);print(f"Legacy consolidate: {counts}",flush=True)
            return result
        def measured_propagate(*args,**kwargs):counts['single_object_sessions']+=1;return propagate(*args,**kwargs)
        model.segment_and_embed=measured_segment;model.propagate=measured_propagate
        infer_start=time.perf_counter();tracking=link_window_tracks(consolidate_object_tracks(read_json(folder/'frame_graphs.json'),context,model),context);inference_s=time.perf_counter()-infer_start
        write_json(folder/'tracking.json',tracking);metrics={'model_load_s':load_s,'inference_s':inference_s,'counts':counts,'global_tracks':len(tracking['tracks']),'single_frame_tracks':sum(len(t['history'])==1 for t in tracking['tracks']),'models':model.version}
    metrics.update(gpu_start=gpu_start,gpu_end=gpu_sample(),total_s=time.perf_counter()-start,caption_cache='warm-existing-V4.0',caption_timing_measured=False,gpu_name=torch.cuda.get_device_name(),torch_num_threads=torch.get_num_threads(),human_reference_used=False)
    write_json(folder/(args.stage+'_metrics.json'),metrics);checkpoint(status='complete',metrics=metrics);print(json.dumps(metrics,ensure_ascii=True),flush=True)
if __name__=='__main__':main()
