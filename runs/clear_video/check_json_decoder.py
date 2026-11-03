import os,json
os.environ["LMFE_MAX_JSON_ARRAY_LENGTH"]="0"
from transformers import AutoTokenizer
from lmformatenforcer import JsonSchemaParser,TokenEnforcer
from materflow.backends.transformers_backend import json_tokenizer_data,json_prefix_function
from materflow.perception.relations import scene_response_schema
from materflow.schema import write_json
t=AutoTokenizer.from_pretrained("Qwen/Qwen3.5-4B",revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",cache_dir="models/hub",local_files_only=True)
data=json_tokenizer_data(t)
response={"objects":[{"local_id":"obj_1","object_class":"bottle","description":"蓝色丁腈手套 🧪 𠀀 45%", "region_hint_xyxy":None}],"relations":[],"texts":[{"object":"obj_1","kind":"display","raw_text":"−7.297 g 45%", "value":-7.297,"unit":"g","reading_kind":"display","region_hint_xyxy":None}],"flows":[{"source":None,"target":"obj_1","material_form":"unknown","arrival_visible":False}],"states":[]}
allowed=json_prefix_function(t,data,scene_response_schema())
import torch
ids=t.encode("Test prompt.")
for token in t.encode(json.dumps(response,ensure_ascii=False,separators=(",",":")),add_special_tokens=False):
    options=allowed(0,torch.tensor(ids,dtype=torch.long))
    assert t.eos_token_id not in options
    assert token in options,(token,t.decode([token]))
    ids.append(token)
assert t.eos_token_id in allowed(0,torch.tensor(ids,dtype=torch.long))
write_json("output/clear_video/diagnostics/json_decoder_check.json",{"passed":True,"tokenizer_commit":"851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a","cases":["Chinese byte fallback including 腈","astral Unicode","no premature EOS","Unicode minus","percent","negative numeric","boolean","null","EOS"],"not_visual_validation":True})
print("JSON decoder actual-tokenizer checks passed")
