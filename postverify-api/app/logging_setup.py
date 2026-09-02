"""Logging: readable during development, machine-readable in production.

JSON is the production default because logs there are read by a tool
(CloudWatch, Loki, Datadog) rather than by a person with grep.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from . import config

_SKIP = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Carry through structured fields, e.g. logger.info(..., extra={"request_id": x})
        for key, value in record.__dict__.items():
            if key not in _SKIP and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if config.json_logs():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
            datefmt="%H:%M:%S"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(config.log_level())

    # These two are noisy, and the noise is not ours.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
