"""Small example tools available to the booking agent."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, get_type_hints

_logger = logging.getLogger(__name__)


def find_available_slots(date: str, service: str) -> str:
    """Find example appointment slots for a service on a date."""
    _logger.info("Finding available slots for service=%s date=%s", service, date)
    return f"Example availability for {service} on {date}: 09:00, 11:30, 15:00."


def clinic_hours(day: str) -> str:
    """Return the clinic's example opening hours for a day."""
    _logger.info("Looking up clinic hours for day=%s", day)
    return f"The clinic is open on {day} from 09:00 to 17:00."


def load_tools(functions: list[Callable[..., Any]]) -> dict[str, dict[str, Any]]:
    """Build model definitions and executable functions from tool callables."""
    _logger.debug("Loading %d tools: %s", len(functions), [f.__name__ for f in functions])
    loaded: dict[str, dict[str, Any]] = {}
    for function in functions:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in signature.parameters.items():
            annotation = hints.get(name, str)
            json_type = "string" if annotation is str else "number" if annotation in (int, float) else "boolean" if annotation is bool else "object"
            properties[name] = {"type": json_type}
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        loaded[function.__name__] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": function.__name__,
                    "description": inspect.getdoc(function) or "",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            },
            "execute": function,
        }
        _logger.debug("Loaded tool %s with schema %s", function.__name__, properties)
    _logger.info("Loaded tools: %s", list(loaded))
    return loaded
