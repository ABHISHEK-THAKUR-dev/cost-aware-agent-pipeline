"""
Structured JSON logging with correlation IDs and secret redaction.
This is the primary debugging tool referenced in docs/design.md Part 2 patterns.
"""
import json
import logging
import re
import sys
import time
from typing import Any

_REDACT_KEY_PATTERN = re.compile(r"(key|token|secret|password|authorization)", re.IGNORECASE)
_REDACTED = "***REDACTED***"


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _REDACT_KEY_PATTERN.search(k) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(_redact(extra))
        if record.exc_info:
            # Exception detail stays in the log, never in the client response (see app/errors.py)
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log_step(
    logger: logging.Logger,
    trace_id: str,
    step: str,
    status: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
) -> None:
    """One structured line per pipeline step — the core debugging artifact."""
    logger.info(
        "pipeline_step",
        extra={
            "extra_fields": {
                "trace_id": trace_id,
                "step": step,
                "status": status,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "error": error,
            }
        },
    )
