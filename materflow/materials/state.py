"""Sample states are distinct from physical carriers. Never infer source depletion."""
from ..schema import ACTIONS, FIELDS, stable_id, field_evidence


def check_event_state_consistency(events, context):
    for event in events:
        if event["action"] not in ACTIONS:
            raise ValueError(f"Unsupported event action: {event['action']}")
        for key in ("source_container_id", "target_container_id"):
            if event.get(key) and event[key] not in context["objects"]:
                raise ValueError(f"Unknown physical object_id: {event[key]}")
        if event.get("source_track_id") and event.get("source_track_id") == event.get("target_track_id") and event["action"] == "transfer":
            raise ValueError("Moving one carrier is not a transfer between carriers")
    return events


def initialize_sample_state(context):
    state = {"current": {}, "empty": set(), "materials": [], "edges": [], "last_events": {}, "context": context}
    for i, condition in enumerate(context["manifest"].get("initial_conditions", [])):
        if condition.get("field_status", context["manifest"].get("initial_conditions_status")) != "supported":
            continue
        container = condition["container_id"]
        eid = f"REG:initial_conditions:{i}"
        context.setdefault("evidence_index", {})[eid] = {"kind": "declared_initial_condition", "manifest_path": str(context["config"]["manifest_path"]),
                                                        "json_pointer": f"/initial_conditions/{i}", "source_id": None, "image_path": None, "box_xyxy": None}
        if condition.get("contents") == "empty":
            state["empty"].add(container)
            continue
        material = _material(context, "M", container, [eid], condition.get("reagent_ids", []),
                             condition.get("provenance_status", "unresolved"), seed=["initial", i])
        state["materials"].append(material)
        state["current"][container] = material
    return state


def _material(context, prefix, container, refs, reagents, provenance, event_id=None, seed=None, track=None):
    material = {"material_id": stable_id(prefix, context["config"]["run_id"], seed, container, track),
                "material_type": "source_material" if prefix == "M" else "sample_state", "container_id": container,
                "reagent_ids": sorted(set(reagents)), "created_by_event_id": event_id, "provenance_status": provenance,
                "notes": "Source identifiers describe evidenced origin, not current chemical composition.", "track_id": track}
    supported = ["material_id", "material_type"]
    if container:
        supported.append("container_id")
    if event_id:
        supported.append("created_by_event_id")
    field_evidence(material, FIELDS["Materials"], refs, supported=supported,
                   not_applicable=("created_by_event_id",) if event_id is None else ())
    return material


def _carrier(event, side):
    return event.get(side + "_container_id") or ("track:" + event[side + "_track_id"] if event.get(side + "_track_id") else None)


def _root(event, side, state, observations):
    context = state["context"]
    key = _carrier(event, side)
    if key is None:
        return None
    previous = _prior(state, key, event)
    if previous is not None:
        state["last_events"][key] = event
        return previous
    oid, track = event.get(side + "_container_id"), event.get(side + "_track_id")
    reagent = context["objects"].get(oid, {}).get("reagent_id")
    # A visible flowing/processed sample exists, but its prior preparation remains unknown.
    material = _material(context, "M", oid, event["evidence_refs"], [reagent] if reagent else [], "unresolved",
                         seed=["first_observed", key, event["source_id"], event["event_id"]], track=track)
    state["materials"].append(material)
    state["current"][key] = material
    state["last_events"][key] = event
    state["empty"].discard(key)
    return material


def _prior(state, key, event):
    last = state["last_events"].get(key)
    if last and last["event_id"] != event["event_id"] and (last["source_id"] != event["source_id"] or
                 state["context"]["sources"][event["source_id"]].get("is_continuous_clip") is not True):
        # The same carrier does not prove unchanged contents across an unknown cut/source.
        return None
    return state["current"].get(key)


def _edge(state, before, after, relation, event, part=None):
    if before is None:
        return
    record = {"edge_id": stable_id("L", before["material_id"], after["material_id"], relation, event["event_id"], part),
              "source_material_id": before["material_id"], "relation": relation, "target_material_id": after["material_id"],
              "event_id": event["event_id"], "output_part": part, "notes": None}
    field_evidence(record, FIELDS["Lineage"], [event["event_id"]], supported=[key for key in FIELDS["Lineage"] if record.get(key) is not None],
                   not_applicable=("output_part",) if part is None else ())
    state["edges"].append(record)


def apply_confirmed_event(event, sample_state, context, observations=None):
    """Historical name: apply evidenced action; no human-review assertion is implied."""
    state = sample_state
    action = event["action"]
    if action == "transfer":
        source = _root(event, "source", state, observations)
        target_key = _carrier(event, "target")
        if source is None or target_key is None:
            return state
        previous = _prior(state, target_key, event)
        target_known = previous is not None or target_key in state["empty"]
        complete = target_known and source["provenance_status"] == "supported" and (previous is None or previous["provenance_status"] == "supported")
        reagents = source["reagent_ids"] + (previous["reagent_ids"] if previous else [])
        after = _material(context, "S", event.get("target_container_id"), [event["event_id"]], reagents,
                          "supported" if complete else "unresolved", event["event_id"], seed=event["event_id"], track=event.get("target_track_id"))
        state["materials"].append(after)
        _edge(state, source, after, "transferred_to", event)
        if previous:
            _edge(state, previous, after, "contributed_to", event)
        state["current"][target_key] = after
        state["last_events"][target_key] = event
        state["empty"].discard(target_key)
    elif action in {"stir", "hold", "age", "filter", "wash"}:
        before = _root(event, "source" if action == "filter" else "target", state, observations)
        if before is None:
            return state
        parts = event.get("output_parts") if action == "filter" else [{"part": "retentate" if action == "wash" else None,
                                                                          "track_id": event.get("target_track_id")}]
        for part in parts or []:
            track = part["track_id"]
            oid = context.get("identities", {}).get(track, {}).get("object_id")
            if track == event.get("target_track_id"):
                oid = oid or event.get("target_container_id")
            key = oid or ("track:" + track if track else None)
            provenance = before["provenance_status"]
            if action == "filter" and key not in state["empty"] and _prior(state, key, event) is not before:
                provenance = "unresolved"
            after = _material(context, "S", oid, [event["event_id"]], before["reagent_ids"], provenance,
                              event["event_id"], seed=[event["event_id"], part["part"], key], track=track)
            state["materials"].append(after)
            _edge(state, before, after, "processed_into", event, part["part"])
            if key:
                state["current"][key] = after
                state["last_events"][key] = event
                state["empty"].discard(key)
    return state
