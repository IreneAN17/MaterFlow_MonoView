"""Deterministically compile semantic inputs; no LLM or observed example rows."""
from pathlib import Path
import json
from ..schema import normalize, file_hash, write_json


def compile_sg_inputs(context, output_dir):
    output = Path(output_dir)
    registry, protocol, manifest = context["registry"], context["protocol"], context["manifest"]
    separator = protocol.get("list_separator") or ";"
    split = lambda value: [v.strip() for v in str(value or "").split(separator) if v.strip()]
    enabled = set(split(protocol.get("model_context_sheets"))) - set(split(protocol.get("annotation_only_sheets")))
    allowed_vocab = set(split(protocol.get("model_vocab_fields"))) & {"signal_type", "value_basis"}
    aliases = {"object_class": {}, "scene_relation": {}, "reagent": {}}

    def add(namespace, canonical, values):
        for value in [canonical, *values]:
            key = normalize(value)
            targets = aliases[namespace].setdefault(key, [])
            if canonical not in targets:
                targets.append(canonical)

    for row in registry["Object Classes"]:
        add("object_class", row["object_class"], split(row.get("aliases")))
    for row in registry["Relations"]:
        add("scene_relation", row["relation"], split(row.get("aliases")))
    for row in registry["Entities"]:
        if row["entity_type"] == "reagent":
            add("reagent", row["entity_id"], [row["name_or_role"], *split(row.get("aliases"))])
    for obj in manifest["object_instances"]:
        if obj.get("reagent_id") and obj.get("tag_text"):
            add("reagent", obj["reagent_id"], [obj["tag_text"]])
    classes = list(dict.fromkeys(r["object_class"] for r in registry["Object Classes"]))
    relations = list(dict.fromkeys(r["relation"] for r in registry["Relations"]))
    if "hold" in relations and "human" not in classes:
        classes.append("human")
        add("object_class", "human", ["camera_wearer"])

    def select(sheet, columns):
        if sheet not in enabled and sheet != "输入词表":
            return []
        return [{key: row[key] for key in columns if row.get(key) is not None} for row in registry[sheet]]

    qwen = {
        "schema_version": manifest["schema_version"], "registry_version": manifest["protocol"].get("registry_version"),
        "protocol_id": manifest["protocol"].get("protocol_id"), "plan_policy": "overridable soft prior",
        "declared_context": {"scene_description": manifest["protocol"].get("scene_description"),
                             "source": "manifest.protocol", "execution_evidence": False},
        "object_classes": select("Object Classes", ["object_class", "name_zh", "aliases"]),
        "scene_relations": select("Relations", ["relation", "name_zh", "definition"]),
        "entities": select("Entities", ["entity_id", "entity_type", "name_or_role"]),
        "event_semantics_for_evidence_search_only": select("Event Types", ["action", "definition", "key_roles", "primary_evidence"]),
        "planned_steps_only": select("SOP", ["step_id", "planned_step", "reagent_role", "target_role"]),
        "observation_vocab": [r for r in select("输入词表", ["field", "value", "含义"]) if r["field"] in allowed_vocab],
        "registered_instance_candidates": [
            {**{key: obj[key] for key in ("object_id", "type", "object_class", "reagent_id", "role_id", "tag_text", "visual_description") if obj.get(key) is not None},
             "identity_basis": "registered"} for obj in manifest["object_instances"]],
        "actor_reference": {"camera_wearer": "visible grasping only; not a registered experimental role"},
    }
    config = context["config"]
    compiled = {"run_id": config["run_id"], "capture": manifest["capture"],
                "initial_conditions": manifest.get("initial_conditions", []),
                "initial_conditions_status": manifest.get("initial_conditions_status"),
                "role_candidates": context["role_candidates"], "qwen_context": qwen,
                "source_files": {key: {"path": str(config[key]), "sha256": file_hash(config[key])}
                                 for key in ("registry_path", "manifest_path", "prompt_template_path")}}
    template = config["prompt_template_path"].read_text(encoding="utf-8-sig")
    if template.count("${LAB_CONTEXT_JSON}") != 1:
        raise ValueError("Template must contain exactly one ${LAB_CONTEXT_JSON}")
    scene = template.replace("${LAB_CONTEXT_JSON}", json.dumps(qwen, ensure_ascii=False, separators=(",", ":")))
    for name, value in (("objects", classes), ("relations", relations), ("alias_map", aliases)):
        write_json(output / "annotations" / (name + ".json"), value)
    write_json(output / "context.json", compiled)
    (output / "prompts").mkdir(parents=True, exist_ok=True)
    (output / "prompts/lab_scene.txt").write_text(scene, encoding="utf-8")
    (output / "prompts/lab_frame.template.txt").write_text(template, encoding="utf-8")
    (output / "input_files").mkdir(parents=True, exist_ok=True)
    (output / "input_files/videos.txt").write_text("".join(f"{sid}\t{source['file']}\n" for sid, source in context["sources"].items()), encoding="utf-8")
    context.update({"compiled": compiled, "aliases": aliases, "scene_prompt": scene})
    return compiled
