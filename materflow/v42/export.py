"""Single-contract timestamped business outputs; no source-relative time in public tables."""
from pathlib import Path
from datetime import datetime
from collections import Counter
import json
import re
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
from ..schema import read_json,write_json,file_hash
from .io import internal,records,jsonl_writer
from .clock import display_time
from .timeline import Timeline

CONTRACT=read_json(Path(__file__).with_name("schema_v4_2.json"))
FIELDS=CONTRACT["fields"]
CORE=CONTRACT["field_evidence"]

def public_rows(context):
    folder=internal(context);timeline=Timeline(context)
    observations=list(records(folder/"observations.jsonl"));derived=read_json(folder/"derived.json")
    result={"observations":[],"events":[],"materials":[],"lineage":[],"objects":[]}
    byobs={o["observation_id"]:o for o in observations};events={e["event_id"]:e for e in derived["events"]}
    for o in observations:
        result["observations"].append({k:o.get(k) for k in FIELDS["Observations"] if k not in ("start_time","end_time")})
        result["observations"][-1].update(start_time=timeline.iso(o["source_id"],o["start_s"]),
                                        end_time=timeline.iso(o["source_id"],o["end_s"]))
    for e in derived["events"]:
        r={k:e.get(k) for k in FIELDS["Events"] if k not in ("start_time","end_time")}
        r.update(start_time=timeline.iso(e["source_id"],e["start_s"]),end_time=timeline.iso(e["source_id"],e["end_s"]))
        result["events"].append(r)
    for m in derived["materials"]:
        r={k:m.get(k) for k in FIELDS["Materials"]}
        event=events.get(m.get("created_by_event_id"))
        refs=m["field_evidence"]["container_id"]["evidence_refs"]
        obs=next((byobs[x] for x in refs if x in byobs),None)
        first=event or obs
        r["first_observed_time"]=timeline.iso(first["source_id"],first["start_s"]) if first else None
        r["created_time"]=timeline.iso(event["source_id"],event["start_s"]) if event and event["interval_status"]=="supported" else None
        if event and not r["created_time"]:
            r["notes"]=(r.get("notes") or "")+" State creation time unresolved; event evidence is not a confirmed creation instant."
        result["materials"].append(r)
    for edge in derived["lineage"]:
        event=events[edge["event_id"]]
        r={k:edge.get(k) for k in FIELDS["Lineage"]}
        r.update(event_start_time=timeline.iso(event["source_id"],event["start_s"]),
                 event_end_time=timeline.iso(event["source_id"],event["end_s"]))
        result["lineage"].append(r)
    identities=read_json(folder/"identities.json")
    for tid,identity in identities.items():
        if not identity.get("object_id"):
            continue
        package=identity["packaging_match"]
        attrs=package.get("attributes",{})
        refs=[o["observation_id"] for o in observations if o.get("track_id")==tid]
        r={k:None for k in FIELDS["Object Identities"]}
        r.update({k:attrs.get(k) for k in r if k in attrs})
        r.update(object_id=identity["object_id"],object_class=identity["object_class"],
            packaging_catalog_id=package.get("packaging_catalog_id"),packaging_match_status=package["status"],
            relevance_status=identity["relevance_status"],evidence_refs=refs,
            first_observed_time=timeline.iso(identity["first_frame"]["source_id"],identity["first_s"]),
            last_observed_time=timeline.iso(identity["last_frame"]["source_id"],identity["last_s"]),
            notes="Runtime physical instance; packaging attributes do not assert contents.")
        result["objects"].append(r)
    return result,{"observations":observations,**derived},timeline

def validate(result,raw,context):
    groups={}
    for name,idcol in (("observations","observation_id"),("events","event_id"),("materials","material_id"),("lineage","edge_id")):
        values=[r[idcol] for r in result[name]]
        if len(values)!=len(set(values)):
            raise ValueError("Duplicate public ID "+name)
        groups[name]=set(values)
        pattern={"observations":"O","events":"E","materials":"[MS]","lineage":"L"}[name]+r"\d{3,}"
        if any(not re.fullmatch(pattern,v) for v in values):
            raise ValueError("Malformed public IDs")
    known=set.union(*groups.values())
    objects={r["object_id"] for r in result["objects"]}
    for name in ("observations","events"):
        for row in result[name]:
            a,b=row["start_time"],row["end_time"]
            if a and b and datetime.fromisoformat(b)<datetime.fromisoformat(a):
                raise ValueError("Reversed source-clock interval")
            if not set(row["evidence_refs"])<=groups["observations"]:
                raise ValueError("Unknown observation reference")
            if name=="observations" and row["observation_id"] in row["evidence_refs"]:
                raise ValueError("Self-referencing observation")
    graph={r["observation_id"]:r["evidence_refs"] for r in result["observations"]}
    def visit(key,stack):
        if key in stack:
            raise ValueError("Cyclic observation derivation")
        for child in graph[key]:
            visit(child,stack|{key})
    for key in graph:
        visit(key,set())
    for row in result["events"]:
        if row["action"] not in context["actions"]:
            raise ValueError("Unsupported action")
        for field in ("source_container_id","target_container_id"):
            if row[field] and row[field] not in objects:
                raise ValueError("Unknown event container")
    for m in result["materials"]:
        if m["container_id"] not in objects or (m["created_by_event_id"] and m["created_by_event_id"] not in groups["events"]):
            raise ValueError("Material reference invalid")
        if m["material_type"]=="source_material" and m["created_time"] is not None:
            raise ValueError("An M root must not invent its creation time")
    dag={m["material_id"]:[] for m in result["materials"]}
    for edge in result["lineage"]:
        if edge["source_material_id"] not in dag or edge["target_material_id"] not in dag or edge["event_id"] not in groups["events"]:
            raise ValueError("Invalid lineage edge")
        dag[edge["source_material_id"]].append(edge["target_material_id"])
    def material_visit(node,stack):
        if node in stack:
            raise ValueError("Cyclic material lineage")
        for child in dag[node]:
            material_visit(child,stack|{node})
    for key in dag:
        material_visit(key,set())
    for sheet,key in (("Observations","observations"),("Events","events"),("Materials","materials")):
        for row in raw[key]:
            if set(row["field_evidence"])!=set(CORE[sheet]):
                raise ValueError("Unexpected Field Evidence expansion")
            for field in row["field_evidence"].values():
                if not set(field["evidence_refs"])<=known or field["field_status"] not in ("supported","unresolved","not_applicable"):
                    raise ValueError("Invalid field evidence")

TIME_COLUMNS={"start_time","end_time","first_observed_time","last_observed_time","created_time","event_start_time","event_end_time"}

def excel_value(key,value):
    if value is None:
        return None
    if key in TIME_COLUMNS:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    if isinstance(value,list):
        return ";".join(map(str,value))
    return value

def sheet(book,name,rows):
    from openpyxl.utils import get_column_letter
    page=book.create_sheet(name);columns=FIELDS[name];page.append(columns)
    for number,row in enumerate(rows,2):
        for column,key in enumerate(columns,1):
            cell=page.cell(number,column,excel_value(key,row.get(key)))
            if key in TIME_COLUMNS and cell.value is not None:
                cell.number_format='yyyy/mm/dd"-"hh:mm:ss'
            elif isinstance(cell.value,str):
                cell.data_type="s"
            cell.alignment=Alignment(wrap_text=True,vertical="top")
    page.freeze_panes="A2";page.auto_filter.ref=page.dimensions
    for cell in page[1]:
        cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="24475A")
    for index,key in enumerate(columns,1):
        page.column_dimensions[get_column_letter(index)].width=24 if key in TIME_COLUMNS else min(55,max(16,len(key)+3))

def export_intervals(raw_intervals,timeline):
    rows=[]
    for item in raw_intervals:
        sid=item["source_id"]
        supported_durations=[timeline.elapsed(sid,a,b) for a,b in item["supported_segments"]]
        convert=lambda spans:[{"start_time":timeline.iso(sid,a),"end_time":timeline.iso(sid,b)}
                               for a,b in spans]
        rows.append({"record_id":item["record_id"],"source_id":sid,
            "start_time":timeline.iso(sid,item["start_s"]),"end_time":timeline.iso(sid,item["end_s"]),
            "temporal_kind":item["temporal_kind"],"time_semantics":item["time_semantics"],
            "supported_ranges":convert(item["supported_segments"]),"unknown_ranges":convert(item["unknown_segments"]),
            "wall_clock_elapsed_s":timeline.elapsed(sid,item["start_s"],item["end_s"]),
            "observed_supported_duration_s":sum(supported_durations) if all(v is not None for v in supported_durations) else None,
            "confirmed_processing_duration_s":item.get("confirmed_processing_duration"),
            "clock_precision_s":1,"statistics":item.get("statistics"),
            "display_resolution":item.get("display_resolution"),"resolution_method":item.get("resolution_method")})
    return rows

def export_run(context,progress):
    c=context["config"];out=c["output_dir"];folder=internal(context)
    from .time_refine import refine_missing_times
    clock_artifacts=refine_missing_times(context)
    result,raw,timeline=public_rows(context)
    validate(result,raw,context)
    explanation=[{"key":"schema_version","value":"ChemFlow-v4.2","meaning":"Public business time is source wall clock"},
        {"key":"timezone","value":c["timezone"],"meaning":"Excel stores local naive datetime with this explicit timezone"},
        {"key":"human_review_complete","value":False,"meaning":"Automatic results; no accuracy certification"},
        {"key":"time_semantics","value":"Evidence span unless interval_status supports complete boundaries","meaning":"Unknown gaps and processing duration are separate"},
        {"key":"internal_coordinates","value":"internal/","meaning":"Source seconds/PTS used only for video seek and provenance"}]
    artifacts=list(clock_artifacts)
    for file,parts in (("observations_v4.2.xlsx",[("Observations","observations")]),
                        ("events_v4.2.xlsx",[("Events","events")]),
                        ("lineage_v4.2.xlsx",[("Materials","materials"),("Lineage","lineage")])):
        book=Workbook();book.remove(book.active);fields=[]
        for name,key in parts:
            sheet(book,name,result[key])
            for row in raw[key]:
                for field,detail in row.get("field_evidence",{}).items():
                    fields.append({"record_id":row[FIELDS[name][0]],"field":field,**detail})
        if file.startswith("observations"):
            sheet(book,"Object Identities",result["objects"])
        sheet(book,"Field Evidence",fields);sheet(book,"说明",explanation)
        target=out/file;book.save(target);artifacts.append(target)
        progress.update("exported "+file)
    for key in result:
        target=out/(key+".jsonl")
        with jsonl_writer(target) as append:
            for row in result[key]:
                append(row)
        artifacts.append(target)
    raw_intervals=read_json(folder/"interval_evidence.json")+read_json(folder/"event_interval_evidence.json")
    public_intervals=export_intervals(raw_intervals,timeline)
    write_json(out/"interval_evidence.json",public_intervals);artifacts.append(out/"interval_evidence.json")
    lines=["# Source recording timestamps","","Clock fields reflect visible watermark evidence; source seconds remain internal.",
           "","| Record | Start time | End time | Meaning |","|---|---|---|---|"]
    for r in public_intervals:
        lines.append(f"| {r['record_id']} | {display_time(r['start_time']) or 'unknown'} | {display_time(r['end_time']) or 'unknown'} | {r['time_semantics']} |")
    (out/"time_report.md").write_text("\n".join(lines),encoding="utf-8")
    artifacts.append(out/"time_report.md")
    write_json(out/"record_counts.json",{k:len(v) for k,v in result.items()})
    artifacts.append(out/"record_counts.json")
    identity=read_json(folder/"identity_metrics.json");scan=read_json(folder/"scan_metrics.json")
    capabilities={"schema_version":"ChemFlow-v4.2","actual_class_count":len(context["classes"]),
        "class_source":"existing user-confirmed registry; no invented 20-class list",
        "packaging_catalog":identity["catalog_status"],"packaging_reference_gallery":identity["gallery_status"],
        "custom_detector":"awaiting_user_dataset_and_weights" if c["detector_backend"]!="custom_yolo" else "configured",
        "actual_detector":scan["actual_detector"],"actual_tracker":scan["tracker"],
        "general_label_ocr":"real_model_executed","specialized_training":"not_run",
        "three_hour_video_validation":"not_run","independent_accuracy_evaluation":"not_available",
        "materials_or_lineage_positive_case":"real_output_present" if result["lineage"] else "not_established_in_this_excerpt"}
    capabilities["component_validation"]={
        "date_tables_and_silent_video":"real_model_output_integrity_verified_separately",
        "klt_tracking":"real_excerpt_and_synthetic_contract_tested",
        "label_matcher":"unit_tested_awaiting_user_packaging_catalog",
        "reference_retrieval":"implemented_empty_index_tested_awaiting_reference_images",
        "custom_detector_training":"implemented_data_checker_tested_training_not_run",
        "bytetrack_botsort":"optional_adapters_implemented_not_installed_or_real_tested",
        "sam2_on_demand":"implemented_not_used_in_this_excerpt",
        "long_video_resume":"synthetic_partition_resume_tested_no_real_three_hour_run"}
    write_json(out/"capability_report.json",capabilities);artifacts.append(out/"capability_report.json")
    if c.get("annotated_video",True):
        from .video import render
        artifacts+=render(context,result,timeline,progress)
    return artifacts
