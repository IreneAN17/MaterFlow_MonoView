"""Exact labels + actual tracks bind registered candidates; no closed-set guessing."""
from collections import defaultdict
from ..schema import normalize


def resolve_object_identities(tracks, observations, context):
    labels = defaultdict(list)
    classes = {}
    for observation in observations:
        payload, track = observation["payload"], observation.get("track_id")
        if payload.get("kind") == "object" and payload.get("object_class"):
            classes[track] = payload["object_class"]
        if observation["signal_type"] == "ocr_text" and payload.get("kind") == "label" and payload["raw_text"].strip():
            labels[track].append(observation)
    identities = {}
    for track in set(classes) | set(labels):
        matched = []
        refs = []
        raw_labels = []
        for observation in labels[track]:
            text = normalize(observation["payload"]["raw_text"])
            raw_labels.append(observation["payload"]["raw_text"])
            reagent_candidates = context["aliases"]["reagent"].get(text, [])
            candidates = {oid for oid, obj in context["objects"].items()
                          if (normalize(obj.get("tag_text") or "") == text or obj.get("reagent_id") in reagent_candidates)
                          and (not classes.get(track) or obj["object_class"] == classes[track])}
            # Unknown or conflicting legible labels veto a forced identity.
            matched.append(candidates)
            refs.extend(observation["evidence_refs"])
        candidates = set.intersection(*matched) if matched else set()
        oid = next(iter(candidates)) if len(candidates) == 1 else None
        identities[track] = {"track_id": track, "object_id": oid, "candidates": sorted(candidates), "raw_labels": raw_labels,
                             "field_status": "supported" if oid else "unresolved", "evidence_refs": sorted(set(refs))}
    # The same registered instance cannot explain two distinct detections in one frame.
    presence = defaultdict(set)
    for obs in observations:
        if obs["payload"].get("kind") == "object":
            presence[(obs["source_id"], obs["start_s"], obs["payload"].get("frame_id", obs["observation_id"]))].add(obs.get("track_id"))
    conflicts = set()
    if tracks:
        for graph in tracks.get("frame_graphs", []):
            bound = defaultdict(set)
            for local in graph["assignments"]:
                tid = tracks["local_to_track"].get(local)
                oid = identities.get(tid, {}).get("object_id")
                if oid:
                    bound[oid].add(tid)
            for tids in bound.values():
                if len(tids) > 1:
                    conflicts.update(tids)
    for track in conflicts:
        identities[track].update({"object_id": None, "field_status": "unresolved", "conflict": "simultaneous distinct tracks"})

    def binding(track):
        return identities.get(track, {}).get("object_id")

    for obs in observations:
        payload = obs["payload"]
        obs["object_id"] = binding(obs.get("track_id"))
        identity = identities.get(obs.get("track_id"), {})
        if obs["object_id"]:
            obs["field_evidence"]["object_id"] = {"field_status": "supported", "evidence_refs": identity["evidence_refs"],
                                                     "reviewer": None, "notes": "Exact visible label matched a registered candidate; contents not chemically verified."}
        for key in ("source", "target", "subject", "object"):
            if key + "_track_id" in payload:
                payload[key + "_object_id"] = binding(payload[key + "_track_id"])
                identity_evidence = identities.get(payload[key + "_track_id"], {})
                obs["field_evidence"]["payload." + key + "_object_id"] = {
                    "field_status": "supported" if payload[key + "_object_id"] else "unresolved",
                    "evidence_refs": identity_evidence.get("evidence_refs", []), "reviewer": None, "notes": None}
        if payload.get("kind") == "relation":
            payload["object_id"] = binding(payload.get("object_track_id"))
        if payload.get("kind") == "state":
            payload["object_id"] = obs["object_id"]
    context["identities"] = identities
    return identities
