"""Read only the selected registry and manifest, never reference sheets."""
from collections import defaultdict
from pathlib import Path
import re
from openpyxl import load_workbook
from ..schema import ACTIONS, RELATIONS, SCHEMA_VERSION, read_json

REQUIRED = {
    "Protocol": {"field", "value"}, "Object Classes": {"object_class", "aliases"},
    "Relations": {"relation", "aliases"},
    "Entities": {"entity_id", "entity_type", "name_or_role"},
    "Event Types": {"action", "definition", "primary_evidence"},
    "SOP": {"step_id", "planned_step", "reagent_role", "target_role"},
    "输入词表": {"field", "value"},
}


def _rows(workbook, name, required):
    iterator = workbook[name].iter_rows(values_only=True)
    headers = list(next(iterator))
    if not required <= set(headers):
        raise ValueError(f"registry.{name}: missing headers {sorted(required - set(headers))}")
    return [{key: value for key, value in zip(headers, values) if key is not None}
            for values in iterator if any(v is not None for v in values)]


def load_run_context(config):
    workbook = load_workbook(config["registry_path"], read_only=True, data_only=True)
    try:
        missing = set(REQUIRED) - set(workbook.sheetnames)
        if missing:
            raise ValueError(f"Missing registry sheets: {sorted(missing)}")
        registry = {name: _rows(workbook, name, required) for name, required in REQUIRED.items()}
    finally:
        workbook.close()
    protocol = {row["field"]: row["value"] for row in registry["Protocol"]}
    for sheet, key in (("Object Classes", "object_class"), ("Relations", "relation"), ("Entities", "entity_id"),
                       ("Event Types", "action"), ("SOP", "step_id")):
        identifiers = [r[key] for r in registry[sheet]]
        if any(not isinstance(i, str) or not i.strip() for i in identifiers) or len(identifiers) != len(set(identifiers)):
            raise ValueError(f"Missing/duplicate {sheet}.{key}")
    manifest = read_json(config["manifest_path"])
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Expected schema_version {SCHEMA_VERSION}")
    if manifest.get("run_id") != config["run_id"]:
        raise ValueError("manifest.run_id differs from config.run_id")
    if protocol.get("schema_version") not in (None, SCHEMA_VERSION):
        raise ValueError("registry schema_version differs from manifest")
    actions = {row["action"] for row in registry["Event Types"]}
    relations = {row["relation"] for row in registry["Relations"]}
    if not actions <= ACTIONS or not relations <= RELATIONS:
        raise ValueError(f"Unsupported action/relation: {actions - ACTIONS}, {relations - RELATIONS}")
    objects = manifest.get("object_instances", [])
    ids = [obj["object_id"] for obj in objects]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate manifest object_id")
    entities = {row["entity_id"] for row in registry["Entities"]}
    for step in registry["SOP"]:
        for key in ("reagent_role", "target_role"):
            if step.get(key) is not None and step[key] not in entities:
                raise ValueError(f"Unregistered SOP {key}: {step[key]}")
    planned = manifest.get("protocol", {}).get("planned_step_ids", [])
    if not set(planned) <= {step["step_id"] for step in registry["SOP"]}:
        raise ValueError("manifest planned_step_ids reference unregistered SOP steps")
    classes = {row["object_class"] for row in registry["Object Classes"]}
    roles = defaultdict(list)
    for obj in objects:
        if obj.get("object_class") not in classes:
            raise ValueError(f"Unregistered object_class: {obj}")
        for key in ("reagent_id", "role_id"):
            if obj.get(key) and obj[key] not in entities:
                raise ValueError(f"Unregistered {key}: {obj[key]}")
        if obj.get("role_id"):
            roles[obj["role_id"]].append(obj["object_id"])
    sources = manifest.get("capture", {}).get("sources", [])
    if not sources:
        raise ValueError("manifest.capture.sources is required")
    source_ids = set()
    for source in sources:
        sid = source["source_id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", sid) or sid in source_ids:
            raise ValueError(f"Invalid/duplicate source_id: {sid}")
        source_ids.add(sid)
        if not source.get("file"):
            raise ValueError(f"Missing source.file: {sid}")
        if source.get("is_continuous_clip") is not None and not isinstance(source["is_continuous_clip"], bool):
            raise ValueError(f"{sid}.is_continuous_clip must be true, false or null")
        for key in ("run_offset_s", "time_scale"):
            if source.get(key) is not None and not isinstance(source[key], (int, float)):
                raise ValueError(f"Invalid {sid}.{key}")
        if source.get("time_scale") is not None and source["time_scale"] <= 0:
            raise ValueError("time_scale must be positive or null")
    for condition in manifest.get("initial_conditions", []):
        if condition.get("container_id") not in ids:
            raise ValueError("initial_conditions.container_id must reference object_instances")
        if not set(condition.get("reagent_ids", [])) <= entities:
            raise ValueError("Unregistered initial reagent_ids")
    return {"config": config, "registry": registry, "protocol": protocol, "manifest": manifest,
            "objects": {obj["object_id"]: obj for obj in objects}, "role_candidates": dict(roles),
            "sources": {s["source_id"]: s for s in sources},
            "source_paths": {s["source_id"]: (Path(config["repo_root"]) / s["file"]).resolve() for s in sources}}
