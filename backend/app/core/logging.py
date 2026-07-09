"""Structured-ish logging setup.

A single :func:`configure_logging` call installs a consistent formatter on the root
logger. In production this is where a JSON formatter or a Sentry/OTel handler would be
wired in; for now it keeps a readable, timestamped console format.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotently configure the root logger with a console handler."""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers to WARNING.
    for noisy in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger; ensures logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
