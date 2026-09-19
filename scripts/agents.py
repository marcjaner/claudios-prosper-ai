"""Run the booking agent with visible INFO and DEBUG logging."""

from __future__ import annotations

import logging

from agent.agent import run_agent
from agent.utils import configure_logging

if __name__ == "__main__":
    configure_logging(logging.DEBUG)
    prompt = "I would like to book a general check-up for next Tuesday."
    for event in run_agent(prompt):
        logging.getLogger(__name__).info("Agent event: %s", event)
