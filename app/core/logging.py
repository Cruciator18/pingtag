import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else arrived via `extra=`.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_HANDLER_FLAG = "_pingtag_json"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(debug: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_FLAG, True)

    root = logging.getLogger()
    # Idempotent: drop only handlers we installed earlier (keeps pytest's handlers intact).
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_FLAG, False)]
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Send uvicorn's own logs through our formatter; requests are logged by our middleware.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
