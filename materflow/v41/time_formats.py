"""Normalize only separators already implied by a complete numeric clock reading."""
import re,unicodedata
from copy import deepcopy
from datetime import datetime
from .clock import parse_clock

def complete_clock(text):
    value=unicodedata.normalize("NFKC",text).strip()
    if not re.fullmatch(r"[0-9\s/.:：-]+",value):return None,"unresolved"
    standard=parse_clock(value)
    if standard:return standard,"explicit_separators"
    if re.fullmatch(r"[0-9\s/.:：-]+",value):
        digits="".join(c for c in value if c.isdigit())
        if len(digits)==14 and digits.startswith("20"):
            try:return datetime.strptime(digits,"%Y%m%d%H%M%S").isoformat(sep=" "),"complete_digits_separator_normalization"
            except ValueError:pass
    return None,"unresolved"

def normalized_anchors(anchors):
    result=deepcopy(anchors)
    for row in result:
        candidate,method=complete_clock(row["raw_text"])
        row.update(parsed_candidate=candidate,parse_method=method)
        row["datetime"]=candidate if row.get("model_score",0)>=.85 else None
    for i,row in enumerate(result):
        if not row["datetime"] or i==0 or i==len(result)-1:continue
        a,b=result[i-1],result[i+1]
        if a["source_id"]!=row["source_id"] or b["source_id"]!=row["source_id"] or not a["datetime"] or not b["datetime"]:continue
        expected=b["source_time_s"]-a["source_time_s"]
        neighbor_delta=(datetime.fromisoformat(b["datetime"])-datetime.fromisoformat(a["datetime"])).total_seconds()
        left=(datetime.fromisoformat(row["datetime"])-datetime.fromisoformat(a["datetime"])).total_seconds()
        if abs(neighbor_delta-expected)<=1.1 and abs(left-(row["source_time_s"]-a["source_time_s"]))>1.1:
            row["datetime"]=None;row["parse_method"]="unresolved_isolated_clock_inconsistency"
    return result
