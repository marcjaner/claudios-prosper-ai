"""Stage gating for the clinic call execution graph."""

from typing import Any

STAGE_TOOLS = {
    "identify": {"search_patients", "register_patient"},
    "configure": {"get_clinic_catalogue"},
    "availability": {"search_availability"},
    "retrieve": {"get_patient_appointments"},
    "execute": {"book_appointment", "reschedule_appointment", "cancel_appointment"},
    "terminate": {"submit_no_action", "escalate_to_human"},
}


def available_tools(state: dict[str, Any]) -> set[str]:
    names = STAGE_TOOLS["identify"] | STAGE_TOOLS["configure"] | STAGE_TOOLS["terminate"]
    if state.get("patient_id"):
        names |= STAGE_TOOLS["availability"] | STAGE_TOOLS["retrieve"]
    if state.get("patient_id") and state.get("catalogue_data"):
        names |= STAGE_TOOLS["execute"]
    return names


def update_state(state: dict[str, Any], tool_name: str, output: Any) -> dict[str, Any]:
    if not isinstance(output, dict):
        return state
    state = dict(state)
    values = _flatten(output)
    if tool_name in {"search_patients", "register_patient"}:
        _copy_first(state, values, "patient_id", ("patient_id", "id"))
    elif tool_name == "get_clinic_catalogue":
        state["catalogue_data"] = output
    elif tool_name == "search_availability":
        _copy_first(state, values, "slot", ("slot", "start_time", "datetime"))
    elif tool_name == "get_patient_appointments":
        _copy_first(state, values, "appointment_id", ("appointment_id",))
    state["stage"] = tool_name
    return state


def _flatten(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result[key] = item
            result.update(_flatten(item))
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        result.update(_flatten(value[0]))
    return result


def _copy_first(state: dict[str, Any], values: dict[str, Any], target: str, keys: tuple[str, ...]) -> None:
    for key in keys:
        if values.get(key):
            state[target] = values[key]
            return
