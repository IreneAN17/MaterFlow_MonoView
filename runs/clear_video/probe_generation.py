from pathlib import Path
import json, os
os.environ["HF_DEACTIVATE_ASYNC_LOAD"]="1"
os.environ["TRITON_CACHE_DIR"]=str(Path("models/triton-cache").resolve())
from materflow.config import load_config
from materflow.backends.transformers_backend import TransformersBackend
from materflow.perception.relations import parse_relation_output
from materflow.schema import write_json,read_json
config=load_config("output/clear_video/run.yaml")
config.update(do_sample=True,temperature=0.7,top_p=0.8,top_k=20,repetition_penalty=1.1,seed=42)
b=TransformersBackend(config)
context={"aliases":read_json("output/clear_video/annotations/alias_map.json")}
prompt=Path("output/clear_video/prompts/lab_scene.txt").read_text(encoding="utf8")
results=[]
for i in [0,450,900]:
    raw=b.infer_images([Path(f"output/clear_video/frames/clear_video/{i:08}.png")],prompt)[0]
    entry={"frame_id":i,"raw":raw,"inference":b.last_inference,"model":b.version}
    try:
        entry["response"]=parse_relation_output(raw,context)
        print("VALID",i,{k:len(entry["response"][k]) for k in ("objects","relations","texts","flows","states")},flush=True)
    except (ValueError,TypeError,KeyError) as e:
        entry["error"]=str(e);print("INVALID",i,str(e),flush=True)
    results.append(entry)
    write_json("output/clear_video/diagnostics/generation_probe.json",results)
