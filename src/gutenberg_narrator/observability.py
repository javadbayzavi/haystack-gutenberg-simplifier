"""Structured logging and request correlation.

Deliberately a smaller, separate copy of the simplifier's equivalent rather than
a shared import. The duplication is a log formatter and a context variable; the
alternative is a shared package that both services must be released together to
change, which is exactly the coupling that makes an optional add-on
non-optional. Small duplication is the price of independent deploy cycles, and
it is cheaper than the coupling.

There is no tracing here. The simplifier traces because it runs a multi-component
Haystack pipeline where the interesting question is which component was slow.
This service does one thing in a loop; its metrics already answer that.
"""

import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, with the request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "service": "gutenberg-narrator",
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def configure_logging(level: str | None = None) -> None:
    """Send structured JSON to stdout, replacing any inherited handlers."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO").upper())
