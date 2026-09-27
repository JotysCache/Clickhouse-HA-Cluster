"""Proves "no acknowledged write was lost" and "all replicas are identical".

Works while the writer is still running: it snapshots the ledger's highest
acked seq and only checks rows up to that point.
"""
import time

from rich import box
from rich.console import Console
from rich.table import Table

from .client import Endpoint, connect, short_error
from .ledger import Ledger

PER_BATCH = """
SELECT batch_id, count() AS c, uniqExact(seq) AS u
FROM events WHERE writer_id = {w:String} AND seq <= {m:UInt64}
GROUP BY batch_id
"""
TOTALS = """
SELECT count(), uniqExact(seq),
       sum(cityHash64(seq, batch_id, ts, writer_id, ingest_replica, payload))
FROM events WHERE writer_id = {w:String} AND seq <= {m:UInt64}
"""


def verify(cfg, allow_down=False, sync_timeout=180, console=None):
    """Returns (ok, summary dict)."""
    console = console or Console()
    ledger = Ledger(cfg.runs_dir / "ledger.sqlite")
    snap = ledger.max_seq(cfg.writer_id)
    expected = ledger.batches(cfg.writer_id, snap)
    exp_rows = sum(expected.values())
    params = {"w": cfg.writer_id, "m": snap}
    console.print(f"[bold]Verify[/]: {len(expected):,} acked batches / {exp_rows:,} rows "
                  f"(writer {cfg.writer_id}, seq <= {snap:,})")
    if not expected:
        console.print("[yellow]Ledger is empty: nothing to verify yet.[/]")
        return True, {"acked_rows": 0}

    clients, down = {}, {}
    for n in cfg.nodes:
        try:
            clients[n.name] = connect(cfg, Endpoint(n.name, n.host, cfg.http_port), query_s=60)
        except Exception as e:
            down[n.name] = short_error(e)

    # Replication is asynchronous past the quorum: wait until each reachable
    # replica has at least every acked row before comparing.
    deadline = time.time() + sync_timeout
    pending = set(clients)
    while pending and time.time() < deadline:
        for name in list(pending):
            try:
                n = clients[name].query(
                    "SELECT count() FROM events WHERE writer_id = {w:String} AND seq <= {m:UInt64}",
                    parameters=params).result_rows[0][0]
                if n >= exp_rows:
                    pending.discard(name)
            except Exception as e:
                down[name] = short_error(e)
                pending.discard(name)
                clients.pop(name)
        if pending:
            time.sleep(2)

    # Replicas are eventually consistent: after a Keeper outage a replica can
    # briefly hold an orphaned copy of a batch (an insert whose commit status was
    # unknown) until it reconciles with Keeper. Re-check until converged or timed
    # out; only a state that persists is a failure.
    t_check = time.time()
    deadline = max(deadline, t_check + 60)
    while True:
        results, probe_down = _check_replicas(clients, expected, params)
        converging = any(r["missing"] or r["duplicates"] for r in results.values()) or \
            len({r["checksum"] for r in results.values()}) > 1
        if not converging or time.time() >= deadline:
            break
        time.sleep(3)
    down.update(probe_down)
    converge_s = time.time() - t_check

    table = Table(box=box.SIMPLE_HEAD, title="Per-replica check against the ack ledger")
    for col in ("replica", "rows", "unique seq", "missing acked batches", "duplicates",
                "unacked extra batches", "checksum"):
        table.add_column(col)

    for name, r in results.items():
        table.add_row(name, f"{r['count']:,}", f"{r['uniq']:,}", str(len(r["missing"])), str(r["duplicates"]),
                      str(len(r["extra"])), f"{r['checksum']:x}")
    for name, err in down.items():
        table.add_row(name, "[red]UNREACHABLE[/]", "", "", "", "", err[:40])
    console.print(table)

    # "Lost" = acked but on no replica at all. "Diverged" = replicas disagree.
    lost = set(expected)
    for r in results.values():
        lost &= r["missing"]
    lost_rows = sum(expected[b] for b in lost)
    checksums = {r["checksum"] for r in results.values()}
    problems = []
    if not results:
        problems.append("no replica reachable")
    if lost:
        problems.append(f"DATA LOSS: {lost_rows:,} acked rows in {len(lost)} batches are on no replica")
    if any(r["missing"] for r in results.values()):
        problems.append("some replica is missing acked rows (not converged)")
    if any(r["duplicates"] for r in results.values()):
        problems.append("duplicate seq values present")
    if len(checksums) > 1:
        problems.append("replica checksums differ")
    if down and not allow_down:
        problems.append(f"unreachable replicas: {', '.join(down)}")

    for r in results.values():
        if r["extra"]:
            console.print(f"[yellow]note[/]: {len(r['extra'])} batch(es) present but never acked "
                          "(in flight when a failure hit; the client could not confirm them). Not a loss.")
            break

    if converge_s > 1 and not problems:
        console.print(f"[yellow]note[/]: replicas needed {converge_s:.0f}s to converge after the failure.")

    ok = not problems
    if ok:
        console.print(f"[bold green]PASS[/]: all {exp_rows:,} acknowledged rows present on "
                      f"{len(results)} replica(s), 0 lost, checksums identical.")
    else:
        for p in problems:
            console.print(f"[bold red]FAIL[/]: {p}")
    return ok, {"acked_rows": exp_rows, "lost_rows": lost_rows, "replicas": len(results),
                "down": list(down), "problems": problems, "converge_s": round(converge_s, 1)}


def _check_replicas(clients, expected, params):
    """Per replica: counts, checksum, acked batches missing, duplicates, unacked extras."""
    results, down = {}, {}
    for name, client in clients.items():
        try:
            per_batch = {b: (c, u) for b, c, u in client.query(PER_BATCH, parameters=params).result_rows}
            count, uniq, checksum = client.query(TOTALS, parameters=params).result_rows[0]
        except Exception as e:
            down[name] = short_error(e)
            continue
        missing = {b for b, rows in expected.items() if per_batch.get(b, (0, 0))[1] < rows}
        results[name] = dict(count=count, uniq=uniq, checksum=checksum, missing=missing,
                             duplicates=count - uniq, extra=set(per_batch) - set(expected))
    return results, down
