"""Logging: one format for everything the container prints. The default is readable text with an
ISO-8601 time; LOG_FORMAT=json gives one JSON object per line, for Loki, Graylog and the like."""

import json
import logging
import os
import sys
import time


def _when(record):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + time.strftime("%z")


class TextFormatter(logging.Formatter):
    def format(self, record):
        server = f" [{record.server}]" if getattr(record, "server", None) else ""
        line = f"{_when(record)} {record.levelname:<7}{server} {record.getMessage()}"
        return line + ("\n" + self.formatException(record.exc_info) if record.exc_info else "")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"time": _when(record), "level": record.levelname.lower(), "logger": record.name,
               "msg": record.getMessage()}
        if getattr(record, "server", None):
            out["server"] = record.server
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if os.environ.get("LOG_FORMAT", "").lower() == "json" else TextFormatter())
    root = logging.getLogger("fanctl")
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
