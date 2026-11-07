from pathlib import Path
from collections import Counter
from openpyxl import Workbook
from ..outputs.export import _cell
from ..schema import write_json,read_json
from .context import FIELDS,CORE,VERSION
from .clock import map_time
from .time_formats import normalized_anchors

def validate(result,context):
    groups={k:{r[idfield] for r in result[k]} for k,idfield in (("observations","observation_id"),("events","event_id"),("materials","material_id"),("lineage","edge_id"))}
    for k,ids in groups.items():
        if len(ids)!=len(result[k]):raise ValueError("Duplicate public ID: "+k)
    known=set().union(*groups.values())
    for o in result["observations"]:
        if o.get("start_s") is None or o["end_s"]<o["start_s"]:raise ValueError("Invalid observation interval")
        if not set(o["evidence_refs"])<=groups["observations"] or o["observation_id"] in o["evidence_refs"]:raise ValueError("Invalid observation dependency")
        if not o.get("visual_evidence_refs"):raise ValueError("Missing raw visual evidence")
        for v in o["visual_evidence_refs"]:
            if v not in context["evidence_index"]:raise ValueError("Unknown visual evidence")
    graph={o["observation_id"]:o["evidence_refs"] for o in result["observations"]}
    def visit(key,seen):
        if key in seen:raise ValueError("Circular derived observation")
        for target in graph[key]:visit(target,seen|{key})
    for key in graph:visit(key,set())
    objects={v["object_id"] for v in context["identities"].values() if v.get("object_id")}
    for e in result["events"]:
        if e["action"] not in context["actions"] or not e["evidence_refs"] or not set(e["evidence_refs"])<=groups["observations"]:raise ValueError("Invalid event/action evidence")
        for k in ("source_container_id","target_container_id"):
            if e.get(k) and e[k] not in objects:raise ValueError("Unknown event carrier")
    for m in result["materials"]:
        if m.get("container_id") and m["container_id"] not in objects:raise ValueError("Unknown material carrier")
        if m.get("created_by_event_id") and m["created_by_event_id"] not in groups["events"]:raise ValueError("Unknown creating event")
    for e in result["lineage"]:
        if e["source_material_id"] not in groups["materials"] or e["target_material_id"] not in groups["materials"] or e["event_id"] not in groups["events"]:raise ValueError("Invalid lineage endpoints")
    for sheet,key in (("Observations","observations"),("Events","events"),("Materials","materials")):
        for r in result[key]:
            if set(r["field_evidence"])!=set(CORE[sheet]):raise ValueError("Unexpected field-evidence expansion")
            for item in r["field_evidence"].values():
                if item["field_status"] not in {"supported","unresolved","not_applicable"} or not set(item["evidence_refs"])<=known:raise ValueError("Invalid field evidence")

def sheet(book,name,records):
    from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.utils import get_column_letter
    page=book.create_sheet(name);columns=FIELDS[name];page.append(columns);alignment=Alignment(vertical="top",wrap_text=True)
    for index,record in enumerate(records,2):
        for column,key in enumerate(columns,1):
            cell=page.cell(index,column,_cell(record.get(key)))
            if isinstance(cell.value,str):cell.data_type="s"
            cell.alignment=alignment
    page.freeze_panes="A2";page.auto_filter.ref=page.dimensions
    for c in page[1]:c.font=Font(bold=True,color="FFFFFF");c.fill=PatternFill("solid",fgColor="24475A")
    for i,key in enumerate(columns,1):page.column_dimensions[get_column_letter(i)].width=min(54,max(15,len(key)+3))

def export(result,context):
    validate(result,context);output=context["config"]["output_dir"];products=[]
    explanation=[{"key":"schema_version","value":VERSION,"meaning":"Category + runtime carrier identity"},{"key":"run_id","value":context["config"]["run_id"],"meaning":"IDs scoped to this run"},{"key":"human_review_complete","value":False,"meaning":"Automatic draft; not an accuracy certification"},{"key":"time_basis","value":"source_relative_seconds","meaning":"Clock mapping in time_mapping.json/time_report.md; evidence spans do not assert actual operation duration"}]
    for filename,parts in (("observations_v4.1.xlsx",[("Observations","observations")]),("events_v4.1.xlsx",[("Events","events")]),("lineage_v4.1.xlsx",[("Materials","materials"),("Lineage","lineage")])):
        print("Exporting "+filename,flush=True);book=Workbook();book.remove(book.active);fields=[]
        for name,key in parts:
            sheet(book,name,result[key])
            for r in result[key]:
                for column,item in r.get("field_evidence",{}).items():fields.append({"record_id":r[FIELDS[name][0]],"field":column,**item})
        sheet(book,"Field Evidence",fields);sheet(book,"说明",explanation);path=output/filename;book.save(path);products.append(path)
        print("Completed export "+filename,flush=True)
    write_json(output/"derived.json",{k:v for k,v in result.items() if k!="observations"});write_json(output/"evidence_index.json",context["evidence_index"])
    return products+[output/"derived.json",output/"evidence_index.json"]

def time_report(result,context):
    output=context["config"]["output_dir"];anchors=normalized_anchors(read_json(output/"clock_anchors.json"));shots=read_json(output/"shots.json");rows=[]
    obs={o["observation_id"]:o for o in result["observations"]};events={e["event_id"]:e for e in result["events"]}
    for o in result["observations"]:rows.append({"record_id":o["observation_id"],"source_id":o["source_id"],"start_s":o["start_s"],"end_s":o["end_s"],"meaning":"visible_evidence_span"})
    for e in result["events"]:rows.append({"record_id":e["event_id"],"source_id":e["source_id"],"start_s":e["start_s"],"end_s":e["end_s"],"meaning":"event_evidence_span_not_confirmed_duration"})
    for m in result["materials"]:
        e=events.get(m.get("created_by_event_id"));refs=m["field_evidence"]["container_id"]["evidence_refs"]
        o=next((obs[r] for r in refs if r in obs),None);e=e or o
        rows.append({"record_id":m["material_id"],"source_id":e["source_id"] if e else None,"start_s":e["start_s"] if e else None,"end_s":e["end_s"] if e else None,"meaning":"state_creation_evidence" if m.get("created_by_event_id") else "first_material_evidence_not_creation_time"})
    for edge in result["lineage"]:
        e=events[edge["event_id"]];rows.append({"record_id":edge["edge_id"],"source_id":e["source_id"],"start_s":e["start_s"],"end_s":e["end_s"],"meaning":"associated_event_evidence"})
    for row in rows:
        row["start_time"]=map_time(row["start_s"],row["source_id"],anchors,shots);row["end_time"]=map_time(row["end_s"],row["source_id"],anchors,shots)
    write_json(output/"time_mapping.json",{"timezone":context["config"].get("timezone","Asia/Shanghai"),"anchors":anchors,"records":rows,"policy":"No global extrapolation, unknown clock/continuity remains unresolved"})
    lines=["# Source clock evidence","","Times identify visible evidence, not complete process durations. M-root times are first evidence, not physical creation.","","| Record | Source seconds | Start clock | End clock | Meaning |","|---|---|---|---|---|"]
    for r in rows:lines.append(f"| {r['record_id']} | {r['start_s']}–{r['end_s']} | {r['start_time'].get('datetime') or 'unresolved'} | {r['end_time'].get('datetime') or 'unresolved'} | {r['meaning']} |")
    (output/"time_report.md").write_text("\n".join(lines),encoding="utf-8")
    return [output/"time_mapping.json",output/"time_report.md"]
