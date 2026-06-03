import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class JSONFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": self.service,
            "msg": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        return json.dumps(payload)


def setup(service: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service))
    log = logging.getLogger(service)
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log
