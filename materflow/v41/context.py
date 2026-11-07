from pathlib import Path
import json
from openpyxl import load_workbook
from ..schema import normalize, file_hash, write_json

VERSION = "ChemFlow-v4.1"
FIELDS = {
 "Observations":"observation_id source_id start_s end_s signal_type observed_value unit object_class object_id instrument_class value_basis evidence_refs notes".split(),
 "Events":"event_id action source_id start_s end_s source_container_id target_container_id interval_status evidence_refs notes".split(),
 "Materials":"material_id material_type container_id created_by_event_id provenance_status notes".split(),
 "Lineage":"edge_id source_material_id relation target_material_id event_id output_part edge_status notes".split(),
 "Field Evidence":"record_id field field_status evidence_refs reviewer notes".split(), "说明":["key","value","meaning"]}
CORE = {"Observations":["observed_value","object_class","object_id","instrument_class"],
 "Events":["action","source_container_id","target_container_id"],"Materials":["container_id"],"Lineage":[]}

def load_context(config):
    from ..schema import read_json
    manifest=read_json(config["manifest_path"])
    if manifest.get("schema_version")!=VERSION or manifest.get("run_id")!=config["run_id"]: raise ValueError("V4.1 manifest/run_id mismatch")
    names=("Protocol","Object Classes","Event Types","Reagents","Relations","输入词表")
    with_book=load_workbook(config["registry_path"],read_only=True,data_only=True)
    try:
        registry={}
        for name in names:
            if name not in with_book.sheetnames: raise ValueError("Missing registry sheet: "+name)
            it=with_book[name].iter_rows(values_only=True); headers=next(it)
            registry[name]=[{k:v for k,v in zip(headers,row) if k is not None} for row in it if any(v is not None for v in row)]
    finally: with_book.close()
    protocol={r["field"]:r["value"] for r in registry["Protocol"]}
    if protocol.get("schema_version")!=VERSION: raise ValueError("Registry must be V4.1")
    classes={r["object_class"]:r for r in registry["Object Classes"]}
    if len(classes)!=len(registry["Object Classes"]) or any(r.get("id_policy") not in ("auto_instance","class_only") for r in classes.values()): raise ValueError("Invalid category/id_policy")
    actions={r["action"] for r in registry["Event Types"]}
    if not actions or not actions <= {"stir","transfer","hold","filter"}: raise ValueError("This run permits stir/transfer/hold/filter only")
    sources={s["source_id"]:s for s in manifest["capture"]["sources"]}
    if len(sources)!=len(manifest["capture"]["sources"]): raise ValueError("Duplicate source_id")
    paths={sid:(config["repo_root"]/s["file"]).resolve() for sid,s in sources.items()}
    for path in paths.values():
        if not path.is_file(): raise FileNotFoundError(path)
    aliases={"object_class":{},"scene_relation":{},"reagent":{}}
    for sheet,key,space in (("Object Classes","object_class","object_class"),("Relations","relation","scene_relation")):
        for row in registry[sheet]:
            for alias in [row[key],*str(row.get("aliases") or "").split(";")]:
                if alias.strip():
                    targets=aliases[space].setdefault(normalize(alias),[])
                    if row[key] not in targets:targets.append(row[key])
    # Reagents is loaded for program-side schema/provenance only, never model context or identity guessing.
    qwen={"object_classes":[{k:r[k] for k in ("object_class","name_zh")} for r in classes.values()],
          "relations":[{k:r[k] for k in ("relation","definition")} for r in registry["Relations"]],
          "actions":[{k:r[k] for k in ("action","definition","primary_evidence")} for r in registry["Event Types"]],
          "vocabulary":[{"field":r["field"],"value":r["value"]} for r in registry["输入词表"] if r["field"] in ("signal_type","value_basis")]}
    template=config["prompt_template_path"].read_text(encoding="utf-8-sig")
    if template.count("${LAB_CONTEXT_JSON}")!=1:raise ValueError("One context placeholder required")
    return {"config":config,"manifest":manifest,"registry":registry,"protocol":protocol,"classes":classes,"actions":actions,
            "sources":sources,"source_paths":paths,"aliases":aliases,"objects":{},"role_candidates":{},
            "scene_prompt":template.replace("${LAB_CONTEXT_JSON}",json.dumps(qwen,ensure_ascii=False,separators=(",",":"))),"qwen_context":qwen}

def core_evidence(record,sheet,refs=(),supported=(),not_applicable=()):
    record["field_evidence"]={name:{"field_status":"not_applicable" if name in not_applicable else "supported" if name in supported else "unresolved",
       "evidence_refs":list(refs),"reviewer":None,"notes":None} for name in CORE[sheet]}
    return record
