from __future__ import annotations

import json
import os
import sys
import threading
import time

write_lock = threading.Lock()


def emit(message: dict) -> None:
    with write_lock:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def emit_raw(raw: str) -> None:
    with write_lock:
        sys.stdout.write(raw)
        sys.stdout.flush()


def respond(message: dict) -> None:
    params = message.get("params") or {}
    time.sleep(float(params.get("delay", 0)))
    if message["method"] == "fail":
        emit({"id": message["id"], "error": {"code": 99, "message": "failed"}})
    elif message["method"] == "large":
        emit({"id": message["id"], "result": {"value": "x" * int(params["size"])}})
    elif message["method"] == "stderr":
        sys.stderr.write("x" * int(params["size"]))
        sys.stderr.flush()
        emit({"id": message["id"], "result": {"value": params.get("value")}})
    elif message["method"] == "malformed":
        emit_raw("{not json}\n")
    else:
        emit({"id": message["id"], "result": {"value": params.get("value")}})


for raw in sys.stdin:
    message = json.loads(raw)
    if message.get("method") == "exit":
        os._exit(7)
    if "id" not in message:
        if message.get("method") == "emit":
            emit({"method": "event/test", "params": message.get("params", {})})
        elif message.get("method") == "malformed":
            emit_raw("{not json}\n")
        continue
    threading.Thread(target=respond, args=(message,), daemon=True).start()
