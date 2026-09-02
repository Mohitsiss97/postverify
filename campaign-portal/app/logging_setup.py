"""Logging — dev me padhne layak, production me machine ke layak.

JSON isliye ki production me logs ko koi tool (CloudWatch, Loki, Datadog) padhta
hai; wahan free-text grep karna kaam nahi aata.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .config import settings

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
        # logger.info("...", extra={"submission_id": 5}) jaisi cheezein bhi aayein
        for key, value in record.__dict__.items():
            if key not in _SKIP and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
            datefmt="%H:%M:%S"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Ye do bahut shor karte hain, aur unka shor humara nahi hai.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
