from pathlib import Path
import os,json
os.environ["HF_DEACTIVATE_ASYNC_LOAD"]="1"
os.environ["TRITON_CACHE_DIR"]=str(Path("models/triton-cache").resolve())
from materflow.config import load_config
from materflow.backends.transformers_backend import TransformersBackend
from materflow.materials.context import load_run_context
from materflow.materials.prompts import compile_sg_inputs
from materflow.perception.relations import parse_relation_output
from materflow.schema import write_json
c=load_config("output/clear_video/run.yaml")
c.update(model_name="Qwen/Qwen3.5-9B",model_revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
         bnb_skip_modules=["model.visual"],offload_embeddings=True,
         do_sample=True,temperature=.7,top_p=.8,top_k=20,presence_penalty=1.5,repetition_penalty=1.,seed=42)
context=load_run_context(c)
compile_sg_inputs(context,Path("output/clear_video/diagnostics/semantics9b_final"))
b=TransformersBackend(c)
results=[]
for i in [450,0,900]:
    raw=b.infer_images([f"output/clear_video/frames/clear_video/{i:08}.png"],context["scene_prompt"]+f"\nsource_id=clear_video,frame_id={i},source_time_s={i/5}")[0]
    e={"frame_id":i,"raw":raw,"model":b.version,"inference":b.last_inference}
    try:
        e["response"]=parse_relation_output(raw,context);print("VALID",i,flush=True)
    except (ValueError,TypeError,KeyError) as error:
        e["error"]=str(error);print("INVALID",i,str(error),flush=True)
    results.append(e);write_json("output/clear_video/diagnostics/9b_final_probe.json",results)
