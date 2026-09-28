"""`hatest web`: read-only live status page for browsers.

Polls the nodes itself (Monitor) and reads what the `hatest run` workload and
the chaos runner write to runs/ (ledger, outages, events), so it needs no
connection to the workload process. Bind it to localhost and put a TLS +
auth reverse proxy in front of it.
"""
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import events
from .monitor import Monitor, healthy

PAGE = Path(__file__).with_name("web.html")


def create_app(cfg):
    app = FastAPI(title="ClickHouse HA status", docs_url=None, redoc_url=None, openapi_url=None)
    mon = Monitor(cfg, interval=2.0)
    mon.start()
    ledger_path = cfg.runs_dir / "ledger.sqlite"

    def workload():
        now = time.time()
        try:
            db = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=2)
            try:
                total, last_ack = db.execute(
                    "SELECT coalesce(sum(rows), 0), max(acked_at) FROM acked WHERE writer_id = ?",
                    (cfg.writer_id,)).fetchone()
                recent = db.execute(
                    "SELECT coalesce(sum(rows), 0) FROM acked WHERE writer_id = ? AND acked_at > ?",
                    (cfg.writer_id, now - 10)).fetchone()[0]
            finally:
                db.close()
        except sqlite3.Error:
            return {"running": False, "acked_rows": 0, "rate": 0, "last_ack_age_s": None}
        age = None if last_ack is None else now - last_ack
        return {
            "running": age is not None and age < 15,
            "acked_rows": total,
            "rate": recent / 10,
            "last_ack_age_s": age,
        }

    @app.get("/api/status")
    def status():
        ok, reason = healthy(mon.nodes) if mon.updated else (False, "starting")
        return {
            "now": time.time(),
            "updated": mon.updated,
            "healthy": ok,
            "reason": reason,
            "vip": {"address": cfg.vip, "owner": mon.vip},
            "nodes": [{"name": name, **st} for name, st in mon.nodes.items()],
            "workload": workload(),
            "outages": events.read(cfg.runs_dir / "outages.jsonl", tail=8)[::-1],
            "events": events.read(cfg.runs_dir / "events.jsonl", tail=14)[::-1],
        }

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE.read_text()

    return app


def serve(cfg, host="127.0.0.1", port=8080):
    import uvicorn
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
