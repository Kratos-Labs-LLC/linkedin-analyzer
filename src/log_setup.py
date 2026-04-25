"""Logging configuration. Two modes selected by `JSON_LOGS` env var:

  - JSON_LOGS=1 → newline-delimited JSON, one record per line. Suitable for
    launchd / cron / log aggregators (Datadog, Loki, journald).
  - else      → human-readable single-line format. Default — what the user
    sees when they tail logs/YYYY-MM-DD.log.

Call setup_logging() exactly once per process, ideally near the top of every
script entrypoint. Subsequent calls are no-ops (idempotent).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_INSTALLED = False


class _JsonFormatter(logging.Formatter):
    """One JSON object per record. Stable keys: ts, level, logger, message.
    Includes any `extra=` fields too — useful for run_date, post_id etc."""

    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and not k.startswith("_"):
                # Best-effort JSON safety — fall back to repr if non-serializable.
                try:
                    json.dumps(v)
                    payload[k] = v
                except TypeError:
                    payload[k] = repr(v)
        return json.dumps(payload, default=str)


def setup_logging(
    *,
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    json_mode = os.environ.get("JSON_LOGS") == "1"
    formatter: logging.Formatter
    if json_mode:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    handlers: list[logging.Handler] = []
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        handlers.append(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    handlers.append(sh)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    _INSTALLED = True


def reset_for_testing() -> None:
    """Test-only: re-allow setup_logging on the next call."""
    global _INSTALLED
    _INSTALLED = False
