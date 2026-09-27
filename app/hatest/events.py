"""Append-only JSONL logs shared between the app and the chaos runner."""
import json
import threading
import time

_lock = threading.Lock()


def append(path, **record):
    record.setdefault("ts", time.time())
    line = json.dumps(record, default=str)
    with _lock, open(path, "a") as f:
        f.write(line + "\n")
    return record


def read(path, since=None, tail=None):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    if tail:
        lines = lines[-tail:]
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is None or rec.get("ts", 0) >= since:
            out.append(rec)
    return out


def event(cfg, source, msg, **extra):
    """Timeline entry shown on the dashboard and used in reports."""
    return append(cfg.runs_dir / "events.jsonl", source=source, msg=msg, **extra)
