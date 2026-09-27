"""Live terminal dashboard (rich) and a plain-text headless mode."""
import datetime as dt
import time

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import events

KEEPER_STYLE = {"leader": "bold green", "follower": "green", "down": "bold red", "no-quorum": "bold red"}


def _t(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _nodes_table(mon):
    t = Table(box=box.SIMPLE_HEAD, expand=True)
    for col in ("node", "server", "keeper", "replica", "delay s", "queue", "active", "rows", "note"):
        t.add_column(col)
    for name, s in mon.nodes.items():
        server = Text(s.get("server", "?"), style="green" if s.get("server") == "up" else "bold red")
        keeper = Text(s.get("keeper", "?"), style=KEEPER_STYLE.get(s.get("keeper"), "yellow"))
        if "readonly" in s:
            replica = Text("READ-ONLY", style="bold red") if s["readonly"] else Text("writable", style="green")
        else:
            replica = Text("-")
        t.add_row(name + (" [VIP]" if mon.vip == name else ""), server, keeper, replica,
                  str(s.get("delay", "-")), str(s.get("queue", "-")),
                  f"{s['active']}/{s['total']}" if "active" in s else "-",
                  f"{s['rows']:,}" if "rows" in s else "-", s.get("error", ""))
    return t


def _op_row(st, extra=""):
    outage = st.in_outage_for()
    status = (Text(f"OUTAGE {outage:.0f}s", style="bold red") if outage is not None
              else Text("OK", style="bold green"))
    lat = f"{st.last_latency_ms:.0f} ms" if st.last_latency_ms is not None else "-"
    last_err = f"{_t(st.last_error_ts)} {st.last_error}" if st.last_error else ""
    return [st.name, status, f"{st.rate():,.0f}/s", f"{st.ok:,}", f"{st.errors:,}",
            st.last_endpoint or "-", lat, extra, last_err[:70]]


def _workload_table(app):
    t = Table(box=box.SIMPLE_HEAD, expand=True)
    for col in ("op", "status", "rate", "ok", "errors", "served by", "latency", "detail", "last error"):
        t.add_column(col)
    t.add_row(*_op_row(app.write_stats, f"acked seq {app.writer.acked_max:,}"))
    r = app.reader.last
    detail = f"replica {r['replica']}, lag {r['lag_rows']} rows" if r else ""
    t.add_row(*_op_row(app.read_stats, detail))
    return t


def _outages_table(app):
    t = Table(box=box.SIMPLE_HEAD, expand=True)
    for col in ("op", "from", "to", "downtime", "failed attempts", "first error"):
        t.add_column(col)
    outs = sorted(app.write_stats.outages + app.read_stats.outages, key=lambda o: o["start"])[-6:]
    for o in outs:
        t.add_row(o["op"], _t(o["start"]), _t(o["end"]), f"{o['duration_s']:.1f}s",
                  str(o["errors"]), (o["first_error"] or "")[:60])
    return t


def _events_table(cfg):
    t = Table(box=None, expand=True, show_header=False)
    t.add_column(width=8)
    t.add_column(width=6)
    t.add_column()
    for e in events.read(cfg.runs_dir / "events.jsonl", tail=10):
        style = "bold magenta" if e.get("source") == "chaos" else ""
        t.add_row(_t(e["ts"]), e.get("source", ""), Text(e.get("msg", ""), style=style))
    return t


def render(app):
    mon = app.monitor
    head = Text.assemble(
        ("ClickHouse HA test  ", "bold"),
        f"mode={app.mode}  endpoints={', '.join(e.name for e in app.endpoints)}  "
        f"VIP owner={mon.vip or '-'}  up {time.time() - app.started:.0f}s",
    )
    return Group(
        Panel(head),
        Panel(_nodes_table(mon), title="Cluster (polled directly per node)"),
        Panel(_workload_table(app), title="Workload"),
        Panel(_outages_table(app), title="Recovered outages (time-to-recover)"),
        Panel(_events_table(app.cfg), title="Timeline"),
    )


def headless_line(app):
    r = app.reader.last
    nodes = " ".join(f"{n}:{s.get('server', '?')[0]}/{s.get('keeper', '?')[:4]}"
                     f"{'/RO' if s.get('readonly') else ''}" for n, s in app.monitor.nodes.items())
    return (f"{_t(time.time())} write ok={app.write_stats.ok} err={app.write_stats.errors} "
            f"acked={app.writer.acked_max} | read ok={app.read_stats.ok} err={app.read_stats.errors} "
            f"replica={r.get('replica', '-')} lag={r.get('lag_rows', '-')} | {nodes} vip={app.monitor.vip}")
