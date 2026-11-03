"""Optional labels after evidence derivation, never an event generator."""


def align_events_to_sop(events, context):
    steps = context["registry"]["SOP"]
    for event in events:
        source = context["objects"].get(event.get("source_container_id"), {})
        target = context["objects"].get(event.get("target_container_id"), {})
        candidates = []
        for step in steps:
            if step.get("target_role") != target.get("role_id") or not target.get("role_id"):
                continue
            if step.get("reagent_role") and step["reagent_role"] == source.get("reagent_id") and event["action"] == "transfer":
                candidates.append(step["step_id"])
            elif event["action"] in {"filter", "wash"} and step["planned_step"] == "过滤并洗涤":
                candidates.append(step["step_id"])
            elif event["action"] == "stir" and step["planned_step"] == "机械搅拌乳液":
                candidates.append(step["step_id"])
        event["sop_step_candidate"] = candidates or None
    return events
