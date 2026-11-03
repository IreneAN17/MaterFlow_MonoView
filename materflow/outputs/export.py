"""The only three business workbooks. JSON retains structured payloads."""
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from ..schema import FIELDS, SCHEMA_VERSION, field_evidence, write_json, write_jsonl


def assemble_run_result(events, observations, lineage):
    return {"observations": observations, "events": events, **lineage}


def _field_rows(records, sheet):
    result = []
    for record in records:
        field_evidence(record, FIELDS[sheet])  # Default unresolved, never silently supported.
        rid = record[FIELDS[sheet][0]]
        for field, evidence in record["field_evidence"].items():
            if evidence["field_status"] not in {"supported", "unresolved", "not_applicable"}:
                raise ValueError(f"Invalid field status: {rid}.{field}")
            result.append({"record_id": rid, "field": field, **evidence})
    return result


def _cell(value):
    return ";".join(map(str, value)) if isinstance(value, list) else value


def _sheet(workbook, name, rows):
    sheet = workbook.create_sheet(name)
    columns = FIELDS[name]
    sheet.append(columns)
    alignment = Alignment(vertical="top", wrap_text=True)
    for row_number, record in enumerate(rows, 2):
        # Access explicit cells: worksheet row/column bounds scan all existing
        # cells, so computing them once per record makes large exports quadratic.
        for column_number, key in enumerate(columns, 1):
            cell = sheet.cell(row_number, column_number, _cell(record.get(key)))
            # Visual/model text is data, including strings starting '=' or '+'.
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = alignment
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24475A")
    for col, header in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(col)].width = min(54, max(15, len(header) + 3))
    return sheet


def validate_references(result, context):
    observations = {o["observation_id"] for o in result["observations"]}
    events = {e["event_id"] for e in result["events"]}
    materials = {m["material_id"] for m in result["materials"]}
    evidence = context.get("evidence_index", {})
    for obs in result["observations"]:
        if not obs["evidence_refs"] or not set(obs["evidence_refs"]) <= set(evidence):
            raise ValueError(f"Observation evidence is not traceable: {obs['observation_id']}")
    for event in result["events"]:
        if not event["evidence_refs"] or not set(event["evidence_refs"]) <= observations:
            raise ValueError(f"Event must reference observations: {event['event_id']}")
    for edge in result["lineage"]:
        if edge["event_id"] not in events or edge["source_material_id"] not in materials or edge["target_material_id"] not in materials:
            raise ValueError(f"Broken lineage reference: {edge['edge_id']}")
    all_ids = observations | events | materials | set(evidence) | {e["edge_id"] for e in result["lineage"]}
    for sheet, key in (("Observations", "observations"), ("Events", "events"), ("Materials", "materials"), ("Lineage", "lineage")):
        for row in _field_rows(result[key], sheet):
            if not set(row.get("evidence_refs", [])) <= all_ids:
                raise ValueError(f"Broken field evidence reference: {row['record_id']}.{row['field']}")


def export_run_result(result, context):
    validate_references(result, context)
    output = Path(context["config"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    explanation = [
        {"key": "run_id", "value": context["config"]["run_id"], "meaning": "One run per workbook"},
        {"key": "schema_version", "value": SCHEMA_VERSION, "meaning": "Selected docs/V4.0 contract"},
        {"key": "time_basis", "value": "source_relative_seconds", "meaning": "Unknown boundaries and mappings remain blank; no experimental duration exported"},
        {"key": "field_status_default", "value": "unresolved", "meaning": "Missing support is not evidence of absence"},
        {"key": "human_review_complete", "value": False, "meaning": "Automatic draft; model outputs are not human confirmation"},
        {"key": "reagent_ids", "value": "evidenced origin only", "meaning": "Does not assert present molecular form or retained quantity"},
        {"key": "initial_conditions_status", "value": context["manifest"].get("initial_conditions_status"), "meaning": "Empty unresolved registration never means empty containers"},
    ]
    names = (("observations_v4.0.xlsx", [("Observations", "observations")]),
             ("events_v4.0.xlsx", [("Events", "events")]),
             ("lineage_v4.0.xlsx", [("Materials", "materials"), ("Lineage", "lineage")]))
    for filename, main_sheets in names:
        counts = ", ".join(f"{sheet}={len(result[key])}" for sheet, key in main_sheets)
        print(f"Exporting {filename}: {counts}", flush=True)
        workbook = Workbook()
        workbook.remove(workbook.active)
        fields = []
        for sheet, key in main_sheets:
            _sheet(workbook, sheet, result[key])
            fields.extend(_field_rows(result[key], sheet))
        _sheet(workbook, "Field Evidence", fields)
        _sheet(workbook, "说明", explanation)
        workbook.save(output / filename)
        print(f"Completed export {filename}", flush=True)
    write_jsonl(output / "observations.jsonl", result["observations"])
    write_json(output / "evidence_index.json", context.get("evidence_index", {}))
    write_json(output / "derived.json", {key: result[key] for key in ("events", "materials", "lineage")})
    return [str(output / filename) for filename, _ in names]
