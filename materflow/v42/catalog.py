"""Strict, auditable label matching. A catalog is packaging knowledge, not contents."""
from pathlib import Path
from collections import defaultdict
import csv
import json
import re
import unicodedata
from ..schema import read_json,write_json,stable_id,file_hash
from .io import internal,records

FIELDS=("packaging_catalog_id brand product_name catalog_number specification packaging_size "
        "grade_or_concentration label_aliases required_match_fields distinguishing_fields "
        "reference_image_paths reference_group_id notes").split()

def canonical_text(value):
    value=" ".join(unicodedata.normalize("NFKC",str(value)).casefold().split())
    value=re.sub(r"(?<=\d)\s+(?=(?:ml|mg|kg|g|l)\b)","",value)
    return value

def values_list(value):
    if isinstance(value,list):
        return value
    if not value:
        return []
    if str(value).strip().startswith("["):
        return json.loads(value)
    return [x.strip() for x in str(value).split(";") if x.strip()]

def load_catalog(path):
    if path is None:
        return []
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    if Path(path).suffix.lower()==".csv":
        with Path(path).open(encoding="utf-8-sig",newline="") as stream:
            rows=list(csv.DictReader(stream))
    else:
        rows=read_json(path)
    ids=[]
    for row in rows:
        if not row.get("packaging_catalog_id"):
            raise ValueError("Catalog row requires packaging_catalog_id")
        required=values_list(row.get("required_match_fields"))
        if not required or any(k not in FIELDS or not row.get(k) for k in required):
            raise ValueError("Every packaging row needs populated required_match_fields")
        row["required_match_fields"]=required
        row["reference_image_paths"]=values_list(row.get("reference_image_paths"))
        ids.append(row["packaging_catalog_id"])
    if len(ids)!=len(set(ids)):
        raise ValueError("Duplicate packaging catalog ID")
    return rows

def contains(text,value):
    needle=canonical_text(value)
    if not needle:
        return False
    # Boundary checks prevent brand ABC matching ABCD. CJK labels may concatenate.
    if re.fullmatch(r"[a-z0-9\s./%+-]+",needle):
        return re.search(r"(?<![a-z0-9])"+re.escape(needle)+r"(?![a-z0-9])",text) is not None
    return needle in text

def match_label(entries,catalog,score_threshold=.85):
    if not catalog:
        return {"status":"not_configured","packaging_catalog_id":None,"reason":"No user packaging catalog",
                "candidates":[],"evidence_refs":[]}
    blocks=[(b["raw_text"],r["request_id"]) for r in entries for b in r.get("blocks",[])
            if b.get("model_score",0)>=score_threshold]
    # Exact duplicate reads are not independent votes and scores are not probabilities.
    text="\n".join(dict.fromkeys(canonical_text(t) for t,_ in blocks))
    candidates=[]
    for row in catalog:
        hits={key:contains(text,row[key]) for key in row["required_match_fields"]}
        # Explicit user aliases are permitted alternatives only for brand/product_name.
        alias_data=row.get("label_aliases") or {}
        if isinstance(alias_data,str) and alias_data.strip().startswith("{"):
            alias_data=json.loads(alias_data)
        if isinstance(alias_data,dict):
            for key,aliases in alias_data.items():
                if key in hits and key in ("brand","product_name"):
                    hits[key]=hits[key] or any(contains(text,v) for v in values_list(aliases))
        candidates.append({"packaging_catalog_id":row["packaging_catalog_id"],
                           "required_field_matches":hits,"complete":all(hits.values())})
    complete=[r for r in candidates if r["complete"]]
    if len(complete)==1:
        ident=complete[0]["packaging_catalog_id"]
        row=next(r for r in catalog if r["packaging_catalog_id"]==ident)
        return {"status":"matched","packaging_catalog_id":ident,
                "attributes":{k:row.get(k) for k in FIELDS if k not in
                              ("label_aliases","required_match_fields","reference_image_paths","notes")},
                "candidates":candidates,"evidence_refs":sorted({ref for _,ref in blocks}),
                "reason":"Unique complete required label-field match; packaging only, contents unresolved"}
    return {"status":"ambiguous" if len(complete)>1 else "insufficient" if text else "unreadable",
            "packaging_catalog_id":None,"candidates":candidates,
            "evidence_refs":sorted({ref for _,ref in blocks}),"reason":"No unique complete label match"}

def identify(context,progress):
    c=context["config"];folder=internal(context)
    catalog=load_catalog(c.get("catalog_path"))
    grouped=defaultdict(list)
    for row in records(folder/"ocr_results.jsonl"):
        if row["object_class"]=="bottle":
            grouped[row["track_id"]].append(row)
    matches={tid:match_label(rows,catalog,c["ocr_score_threshold"]) for tid,rows in grouped.items()}
    gallery_status="not_configured"
    if c.get("gallery_index_path"):
        from .gallery import match_queries
        visual=match_queries(context,grouped,catalog)
        for tid,ranking in visual.items():
            matches.setdefault(tid,match_label([],catalog,c["ocr_score_threshold"]))["visual_ranking"]=ranking
            # Image retrieval cannot promote an insufficient label into a match.
        gallery_status="queried"
    write_json(folder/"packaging_matches.json",matches)
    report={"catalog_status":"configured" if catalog else "not_configured",
            "catalog_entries":len(catalog),"gallery_status":gallery_status,"bottle_tracks":len(grouped),
            "match_counts":{status:sum(x["status"]==status for x in matches.values())
                            for status in ("matched","ambiguous","insufficient","unreadable","not_configured")},
            "visual_similarity_overrides_text":False,"physical_instances_merged_by_catalog":False}
    write_json(folder/"identity_metrics.json",report)
    progress.update("identify: "+report["catalog_status"]+"; label-gated bottle candidates retained internally")
    return [folder/"packaging_matches.json",folder/"identity_metrics.json"]
