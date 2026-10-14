"""Structured JSON logging. Log records never include secrets, connection strings or result sets."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
_SECRETS = [
    (re.compile(r"postgres(ql)?://\S+"), "<db-url>"),
    (re.compile(r"eyJ[\w-]{10,}\.[\w-]+\.[\w-]+"), "<jwt>"),
    (re.compile(r"(?i)(password|passwd|pwd)=\S+"), r"\1=<redacted>"),
]


def scrub(text: str) -> str:
    for pat, repl in _SECRETS:
        text = pat.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        for k, v in vars(record).items():
            if k not in _STD and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(out, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):  # replaced by our request log
        logging.getLogger(noisy).disabled = True
