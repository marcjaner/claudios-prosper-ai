"""Shared agent utilities."""

from __future__ import annotations

import logging
from functools import lru_cache

from dotenv import load_dotenv


def configure_logging(level: int = logging.INFO) -> None:
    """Configure useful console logging for the agent process."""
    logging.getLogger(__name__).debug("Configuring logging at level %s", level)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

@lru_cache(maxsize=1)
def load_environment() -> bool:
    """Load the dotenv file once per process."""
    loaded = load_dotenv()
    logging.getLogger(__name__).info("Environment loaded from dotenv: %s", loaded)
    return loaded
