"""Deterministic evidence rules. This module never receives a model or pixels."""
from copy import deepcopy
from ..schema import ACTIONS, FIELDS, field_evidence, stable_id


def _time(observation):
    return observation.get("start_s")


def _track(observation):
    return observation.get("track_id") or observation.get("payload", {}).get("track_id")


def _near(a, b, context):
    if a["source_id"] != b["source_id"]:
        return False
    if a["observation_id"] == b["observation_id"]:
        return True
    if _time(a) is None or _time(b) is None:
        # Untimed stills must be the very same image, not merely ordered files.
        index = context.get("evidence_index", {})
        a_frames = {index[r].get("frame_id") for r in a["evidence_refs"] if r in index}
        b_frames = {index[r].get("frame_id") for r in b["evidence_refs"] if r in index}
        return bool(a_frames & b_frames)
    gap = context["config"].get("event_gap_s", 2 / context["config"]["sample_fps"])
    return abs(_time(a) - _time(b)) <= gap + 1e-8


def propose_lab_events(observations, context):
    states = [o for o in observations if o["signal_type"] == "visual_state" and o["payload"].get("kind") == "state"]
    flows = [o for o in observations if o["signal_type"] == "material_flow" and o["payload"].get("arrival_visible") is True]
    classes = {_track(o): o["payload"]["object_class"] for o in observations if o["payload"].get("kind") == "object"}
    candidates, consumed = [], set()

    def local_states(track, anchor, names):
        return [o for o in states if _track(o) == track and o["payload"]["state"] in names and _near(o, anchor, context)]

    def candidate(action, evidence, source_track=None, target_track=None, output_parts=None):
        unique = {o["observation_id"]: o for o in evidence}
        evidence = list(unique.values())
        sid = evidence[0]["source_id"]
        identities = context.get("identities", {})
        def bound(track):
            if track is None:
                return None
            oid = identities.get(track, {}).get("object_id")
            for o in evidence:
                if _track(o) == track and o.get("object_id"):
                    oid = oid or o["object_id"]
                for side in ("source", "target"):
                    if o["payload"].get(side + "_track_id") == track:
                        oid = oid or o["payload"].get(side + "_object_id")
            return oid
        anchors = [o for o in evidence if action != "wash" or o["payload"].get("state") != "solid_retained"]
        times = [o["start_s"] for o in anchors if o.get("start_s") is not None]
        candidates.append({"action": action, "source_id": sid, "source_container_id": bound(source_track),
                           "target_container_id": bound(target_track), "source_track_id": source_track, "target_track_id": target_track,
                           "start_s": None, "end_s": None, "sop_step_candidate": None, "interval_status": "unresolved",
                           "evidence_refs": sorted(unique), "notes": "Action evidence exists; boundaries or continuity may be unknown.",
                           "observed_start_s": min(times) if times else None, "observed_end_s": max(times) if times else None,
                           "output_parts": output_parts or [], "boundary_start_refs": [], "boundary_end_refs": []})
        return candidates[-1]

    # Washing requires contact + drainage of a previously retained sample.
    for contact in states:
        if contact["payload"]["state"] != "liquid_contacting_retained_solid":
            continue
        target = _track(contact)
        draining = local_states(target, contact, {"liquid_draining", "wash_liquid_removed"})
        retained = [o for o in states if _track(o) == target and o["payload"]["state"] == "solid_retained" and
                    o["source_id"] == contact["source_id"] and
                    (_near(o, contact, context) or (context["sources"][contact["source_id"]].get("is_continuous_clip") is True and
                     _time(o) is not None and _time(contact) is not None and _time(o) < _time(contact)))]
        if not draining or not retained:
            continue
        incoming = [o for o in flows if o["payload"].get("target_track_id") == target and _near(o, contact, context)]
        if any(o["payload"].get("material_form") == "suspension" for o in incoming):
            continue
        source_tracks = {o["payload"].get("source_track_id") for o in incoming if o["payload"].get("source_track_id")}
        source = next(iter(source_tracks)) if len(source_tracks) == 1 else None
        candidate("wash", [contact, *draining, retained[-1], *incoming], source, target, [{"part": "retentate", "track_id": target}])
        consumed.update(o["observation_id"] for o in incoming)

    # Initial loading is part of filtration, not a second independently counted transfer.
    for flow in flows:
        if flow["observation_id"] in consumed:
            continue
        payload = flow["payload"]
        source, target = payload.get("source_track_id"), payload.get("target_track_id")
        retention = local_states(target, flow, {"solid_retained", "solid_liquid_separation"})
        drainage = [o for o in flows if o["payload"].get("source_track_id") == target and o["payload"].get("target_track_id") and _near(o, flow, context)]
        separation = classes.get(target) == "filter funnel" and payload.get("material_form") == "suspension" and (retention or drainage)
        if not separation or not source or not target:
            continue
        parts = []
        if retention:
            parts.append({"part": "retentate", "track_id": target})
        for drain in drainage:
            parts.append({"part": "filtrate", "track_id": drain["payload"]["target_track_id"]})
        candidate("filter", [flow, *retention, *drainage], source, target, parts)
        consumed.update(o["observation_id"] for o in [flow, *drainage])

    for flow in flows:
        if flow["observation_id"] in consumed:
            continue
        p = flow["payload"]
        if p.get("source_track_id") and p.get("target_track_id") and p["source_track_id"] != p["target_track_id"]:
            candidate("transfer", [flow], p["source_track_id"], p["target_track_id"])

    for state in states:
        name, track = state["payload"]["state"], _track(state)
        bound = context.get("identities", {}).get(track, {}).get("object_id") or state.get("object_id")
        sample_carrier = classes.get(track) in {"beaker", "container", "filter funnel", "weighing cup", "bottle"}
        if name in {"stirring_motion", "stirring_started"} and (bound or sample_carrier) and classes.get(track) not in {"stirrer", "balance", "oven", "temperature probe", "human"}:
            candidate("stir", [state], target_track=track)
        elif name in {"sample_in_oven", "sample_placed_in_oven"} and (bound or sample_carrier):
            candidate("hold", [state], target_track=track)
        elif name == "aging_started" and (bound or sample_carrier) and state["payload"].get("temporal_input") == "ordered_images":
            ends = [o for o in states if _track(o) == track and o["source_id"] == state["source_id"] and
                    o["payload"]["state"] == "aging_stopped" and o["payload"].get("temporal_input") == "ordered_images" and
                    _time(o) is not None and _time(state) is not None and _time(o) > _time(state)]
            if ends:
                item = candidate("age", [state, ends[0]], target_track=track)
                item["start_s"], item["end_s"] = state["start_s"], ends[0]["start_s"]
                item["boundary_start_refs"], item["boundary_end_refs"] = [state["observation_id"]], [ends[0]["observation_id"]]
    boundary_names = {"stir": ("stirring_started", "stirring_stopped"),
                      "hold": ("sample_placed_in_oven", "sample_removed_from_oven")}
    for item in candidates:
        if item["action"] not in boundary_names:
            continue
        start_name, end_name = boundary_names[item["action"]]
        related = [s for s in states if _track(s) == item["target_track_id"] and s["source_id"] == item["source_id"] and
                   s["payload"].get("temporal_input") == "ordered_images" and _time(s) is not None]
        starts = sorted((s for s in related if s["payload"]["state"] == start_name and _time(s) <= item["observed_end_s"]), key=_time)
        ends = sorted((s for s in related if s["payload"]["state"] == end_name and _time(s) >= item["observed_start_s"]), key=_time)
        if starts:
            item["start_s"] = starts[-1]["start_s"]
            item["boundary_start_refs"] = [starts[-1]["observation_id"]]
        if ends:
            item["end_s"] = ends[0]["start_s"]
            item["boundary_end_refs"] = [ends[0]["observation_id"]]
        item["evidence_refs"] = sorted(set(item["evidence_refs"] + item["boundary_start_refs"] + item["boundary_end_refs"]))
        if starts and ends and item["end_s"] < item["start_s"]:
            raise ValueError("Conflicting process boundary evidence")
    return candidates


def resolve_event_candidates(candidates, observations, context):
    by_id = {o["observation_id"]: o for o in observations}
    if len(by_id) != len(observations):
        raise ValueError("Duplicate observation_id")
    merged = []
    key = lambda c: (c["source_id"], c["observed_start_s"] is None, c["observed_start_s"] or 0, c["action"], str(c.get("target_track_id")))
    for item in sorted(deepcopy(candidates), key=key):
        if item["action"] not in ACTIONS or not item["evidence_refs"] or not set(item["evidence_refs"]) <= set(by_id):
            raise ValueError("Event action or observation evidence is invalid")
        matched = None
        for old in reversed(merged):
            if any(old.get(k) != item.get(k) for k in ("action", "source_id", "source_track_id", "target_track_id")):
                continue
            # Shared historical retention evidence alone cannot merge distinct wash rounds.
            anchors = lambda c: {r for r in c["evidence_refs"] if by_id[r]["payload"].get("state") != "solid_retained"}
            overlap = anchors(old) & anchors(item)
            continuity = context["sources"][item["source_id"]].get("is_continuous_clip") is True
            gap = context["config"].get("event_gap_s", 2/context["config"]["sample_fps"])
            touching = (continuity and old["observed_end_s"] is not None and item["observed_start_s"] is not None and
                        0 <= item["observed_start_s"] - old["observed_end_s"] <= gap + 1e-8)
            if overlap or touching:
                matched = old
                break
        if matched:
            matched["evidence_refs"] = sorted(set(matched["evidence_refs"] + item["evidence_refs"]))
            times = [t for t in (matched["observed_end_s"], item["observed_end_s"]) if t is not None]
            matched["observed_end_s"] = max(times) if times else None
            for part in item["output_parts"]:
                if part not in matched["output_parts"]:
                    matched["output_parts"].append(part)
        else:
            merged.append(item)
    for event in merged:
        event["event_id"] = stable_id("E", context["config"]["run_id"], event["action"], event["source_id"], event["evidence_refs"])
        source = context["sources"][event["source_id"]]
        if event["start_s"] is not None and event["end_s"] is not None and source.get("is_continuous_clip") is True:
            event["interval_status"] = "supported"
        supported = ["event_id", "action", "source_id", "evidence_refs"]
        for field in ("source_container_id", "target_container_id", "start_s", "end_s"):
            if event.get(field) is not None:
                supported.append(field)
        field_evidence(event, FIELDS["Events"], supported=supported,
                       not_applicable=("source_container_id",) if event["action"] in {"stir", "hold", "age"} else ())
    return merged
