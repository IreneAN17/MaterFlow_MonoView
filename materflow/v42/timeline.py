"""Indexed source wall clocks; O(log N) lookup, seconds-only precision retained."""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from .clock import time_at
from .io import internal
from ..schema import read_json

class Timeline:
    def __init__(self,context,include_refined=True):
        folder=internal(context);self.c=context["config"]
        self.shots=read_json(folder/"shots.json")
        self.anchors=read_json(folder/"clock_anchors.json")
        extra=folder/"extra_clock_anchors.json"
        manifest=folder/"extra_clock_manifest.json"
        if include_refined and extra.exists() and manifest.exists():
            from ..schema import file_hash
            metadata=read_json(manifest)
            if metadata.get("timezone")==self.c["timezone"] and metadata.get("anchors_sha256")==file_hash(extra):
                valid_sources=all(file_hash(context["source_paths"][sid])==digest for sid,digest in metadata["inputs"].items())
                if valid_sources:
                    existing={(a["source_id"],a["frame_id"]) for a in self.anchors if a.get("datetime")}
                    self.anchors += [a for a in read_json(extra) if (a["source_id"],a["frame_id"]) not in existing]
        grouped=defaultdict(list)
        for a in self.anchors:
            if a.get("datetime"):
                grouped[a["source_id"]].append(a)
        self.groups={sid:sorted(rows,key=lambda a:a["source_time_s"]) for sid,rows in grouped.items()}
        self.times={sid:[r["source_time_s"] for r in rows] for sid,rows in self.groups.items()}

    def lookup(self,sid,t):
        if t is None:
            return {"datetime":None,"status":"unresolved","reason":"no_time_evidence"}
        rows=self.groups.get(sid,[])
        index=bisect_left(self.times.get(sid,[]),t)
        candidates=rows[max(0,index-1):index+1]
        return time_at(t,sid,candidates,self.shots,self.c["max_clock_anchor_gap_s"])

    def iso(self,sid,t):
        return self.lookup(sid,t)["datetime"]

    def elapsed(self,sid,a,b):
        x,y=self.iso(sid,a),self.iso(sid,b)
        if not x or not y:
            return None
        return (datetime.fromisoformat(y)-datetime.fromisoformat(x)).total_seconds()
