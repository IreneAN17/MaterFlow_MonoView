"""Shared records and IO helpers. Unknown values stay None."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import unicodedata

SCHEMA_VERSION = "ChemFlow-v4.0"
ACTIONS = {"transfer", "stir", "hold", "age", "filter", "wash"}
RELATIONS = {"hold", "on", "in", "flow into"}
FIELDS = {
    "Observations": "observation_id source_id start_s end_s signal_type observed_value unit object_id instrument_id value_basis evidence_refs notes".split(),
    "Events": "event_id action source_id start_s end_s source_container_id target_container_id sop_step_candidate interval_status evidence_refs notes".split(),
    "Materials": "material_id material_type container_id reagent_ids created_by_event_id provenance_status notes".split(),
    "Lineage": "edge_id source_material_id relation target_material_id event_id output_part notes".split(),
    "Field Evidence": "record_id field field_status evidence_refs reviewer notes".split(),
    "说明": ["key", "value", "meaning"],
}


@dataclass(frozen=True)
class FrameRecord:
    source_id: str
    frame_id: int
    source_frame_index: int
    source_time_s: float | None
    image_path: str
    pts: int | None = None
    time_base: str | None = None
    width: int | None = None
    height: int | None = None


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def stable_id(prefix, *parts):
    return prefix + sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import os, threading
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, default=json_default, allow_nan=False) + "\n")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def validate_box(box):
    import math
    if not isinstance(box, (list, tuple)) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
        raise ValueError(f"Expected normalized xyxy: {box!r}")
    if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError(f"Invalid normalized xyxy: {box!r}")
    return list(box)


def field_evidence(record, columns, evidence_refs=None, supported=(), not_applicable=()):
    refs = list(evidence_refs if evidence_refs is not None else record.get("evidence_refs", []))
    states = record.setdefault("field_evidence", {})
    for name in columns:
        status = "supported" if name in supported else "unresolved"
        if name in not_applicable:
            status = "not_applicable"
        states.setdefault(name, {"field_status": status, "evidence_refs": refs, "reviewer": None, "notes": None})
    return record
