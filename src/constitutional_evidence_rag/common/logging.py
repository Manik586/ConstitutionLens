"""Logging utilities (SRS NFR-06: Observability).

Call `configure_logging()` once at process start (a script's entry
point), then `get_logger(__name__)` in each module. No per-stage
handlers are set up yet since no pipeline stage exists in the repo
yet — this just gives every future module a consistent, shared way to
log, so each phase doesn't reinvent its own logging setup.
"""
from __future__ import annotations

import logging
import sys

DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(
    level: str = "INFO", fmt: str = DEFAULT_FORMAT, force: bool = False
) -> None:
    """Configure the root logger.

    `force` is passed through to `logging.basicConfig`; leave it False
    in application code (basicConfig then no-ops if logging is already
    configured) and set it True only where a test needs to reset the
    root logger's state.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stdout,
        force=force,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
