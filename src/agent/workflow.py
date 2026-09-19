"""Tool availability for request-scoped call execution."""

from .call_context import CallContext

BASE_TOOLS = {
    "get_clinic_catalogue",
    "search_patients",
    "get_patient_appointments",
    "search_availability",
    "register_patient",
    "prepare_booking",
    "prepare_reschedule",
    "prepare_cancellation",
    "get_call_state",
    "revise_request",
    "submit_no_action",
    "escalate_to_human",
}


def available_tools(context: CallContext) -> set[str]:
    tools = set(BASE_TOOLS)
    if context.has_prepared_proposal():
        tools.add("confirm_action")
    return tools
