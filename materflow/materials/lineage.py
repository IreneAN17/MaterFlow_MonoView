from .state import initialize_sample_state, apply_confirmed_event, check_event_state_consistency


def reconstruct_lineage(events, observations, context):
    check_event_state_consistency(events, context)
    known = {o["observation_id"] for o in observations}
    for event in events:
        if not set(event["evidence_refs"]) <= known:
            raise ValueError("Lineage event references missing observations")
    state = initialize_sample_state(context)
    for event in events:
        apply_confirmed_event(event, state, context, observations)
    return {"materials": state["materials"], "lineage": state["edges"]}
