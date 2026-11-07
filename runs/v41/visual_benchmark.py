"""Isolated cold V4.1 visual benchmark; production outputs remain untouched."""
from pathlib import Path
import argparse,json,shutil,sys,time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import FrameRecord,read_json,read_jsonl,write_json
from materflow.v41.context import load_context
from materflow.v41.runtime import gpu_sample

class Progress:
    def update(self,message=None,**values):
        if message:print(message,flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--stage',choices=('ground','consolidate'),required=True);p.add_argument('--name',default='cold_v41');args=p.parse_args()
    c=load_config(args.config);original=c['output_dir'];folder=original/args.name;folder.mkdir(exist_ok=True)
    if (folder/(args.stage+'_benchmark.json')).exists():raise ValueError('Cold measurement already exists; select another --name rather than overwrite it')
    if args.stage=='ground' and (folder/'cache/ground').exists():raise ValueError('Ground cache exists; select a fresh --name for cold inference')
    if args.stage=='consolidate' and (folder/'cache/features').exists():raise ValueError('Feature cache exists; select a fresh --name for cold inference')
    for filename in ('shots.json','motion_scores.json'):shutil.copyfile(original/filename,folder/filename)
    c['output_dir']=folder;context=load_context(c);frames=[FrameRecord(**r) for r in read_jsonl(original/'frame_index.jsonl')];context['frames']=frames
    import torch
    torch.cuda.reset_peak_memory_stats();gpu_start=gpu_sample();start=time.perf_counter()
    if args.stage=='ground':
        from materflow.v41.vision import ground
        captions=[]
        for sid in context['sources']:captions.extend(read_json(original/'captions'/c['model_name'].replace('/','_')/(sid+'.json')))
        graphs=ground(frames,captions,context,Progress());write_json(folder/'frame_graphs.json',graphs)
        metrics=read_json(folder/'ground_metrics.json');assert metrics['cache_hits']==0 and metrics['forward_calls']>0
    else:
        from materflow.v41.vision import track
        tracking=track(read_json(folder/'frame_graphs.json'),context,Progress());write_json(folder/'tracking.json',tracking)
        metrics=read_json(folder/'tracking_metrics.json');assert metrics.get('feature_cache_hits',0)==0
    wall=time.perf_counter()-start
    report={'variant':'V4.1','stage':args.stage,'source_range_s':c.get('ranges_s'),'cache':'cold visual features/detections; model files already on disk','model_load_and_setup_s':wall-metrics['elapsed_s'],'inference_s':metrics['elapsed_s'],'total_s':wall,'gpu_start':gpu_start,'gpu_end':gpu_sample(),'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'peak_reserved_bytes':torch.cuda.max_memory_reserved(),'frames':len(frames),'metrics':metrics,'human_reference_used':False,'caption_timing_measured':False}
    write_json(folder/(args.stage+'_benchmark.json'),report);print(json.dumps(report,ensure_ascii=True),flush=True)
if __name__=='__main__':main()
