from pathlib import Path
import os,time
os.environ["HF_DEACTIVATE_ASYNC_LOAD"]="1"
os.environ["TRITON_CACHE_DIR"]=str(Path("models/triton-cache").resolve())
from materflow.config import load_config
from materflow.backends.transformers_backend import TransformersBackend
from materflow.perception.relations import parse_relation_output
from materflow.schema import write_json,read_json
c=load_config("output/clear_video/run.yaml")
c.update(do_sample=True,temperature=0.7,top_p=0.8,top_k=20,repetition_penalty=1.0,presence_penalty=1.5,seed=42,
         model_name="Qwen/Qwen3.5-4B",model_revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
         bnb_skip_modules=["model.visual","lm_head"],offload_embeddings=False)
b=TransformersBackend(c)
prompt=Path("output/clear_video/prompts/lab_scene.txt").read_text(encoding="utf8")
context={"aliases":read_json("output/clear_video/annotations/alias_map.json")}
ids=[0,225,450,900]
t=time.monotonic()
raws=b.infer_images([f"output/clear_video/frames/clear_video/{i:08}.png" for i in ids],
                    [prompt+f"\nsource_id=clear_video, frame_id={i}, source_time_s={i/5}" for i in ids])
results=[]
for i,raw in zip(ids,raws):
    e={"frame_id":i,"raw":raw,"inference":b.last_inference,"model":b.version}
    try:
        e["response"]=parse_relation_output(raw,context);print("VALID",i,flush=True)
    except (ValueError,KeyError,TypeError) as error:
        e["error"]=str(error);print("INVALID",i,str(error),flush=True)
    results.append(e)
write_json("output/clear_video/diagnostics/batch4_probe.json",results)
print("Batch elapsed",time.monotonic()-t,"GPU peak",b.torch.cuda.max_memory_allocated(),flush=True)
