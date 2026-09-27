"""python -m hatest <command>

  run       [--mode vip|client] [--headless]   continuous writes + reads + live dashboard
  status                                         one-shot cluster status
  verify    [--allow-down]                       zero-loss + replica consistency check
  chaos     ACTION NODE                          manual fault injection (see ACTIONS)
  scenario  N [N ...] | all  [--node X] [--hold S]  automated, self-checking failure tests
  web       [--host H] [--port P]                read-only live status page (put TLS + auth in front)
"""
import argparse
import logging
import sys

from rich.console import Console
from rich.panel import Panel

from .config import Config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hatest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run the workload with the live dashboard")
    p.add_argument("--mode", choices=["vip", "client"], default="vip",
                   help="vip: through HAProxy on the VIP; client: app-side failover across nodes")
    p.add_argument("--headless", action="store_true", help="plain log lines instead of the dashboard")

    sub.add_parser("status", help="one-shot cluster status")

    p = sub.add_parser("verify", help="check acked rows vs every replica")
    p.add_argument("--allow-down", action="store_true", help="don't fail just because a replica is unreachable")

    from .chaos import ACTIONS, SCENARIOS
    p = sub.add_parser("chaos", help="manual fault injection")
    p.add_argument("action", choices=sorted(ACTIONS))
    p.add_argument("node", help="node name, or: leader | vip | serving")

    p = sub.add_parser("scenario", help="automated failure scenarios")
    p.add_argument("numbers", nargs="+", help=f"scenario numbers ({min(SCENARIOS)}-{max(SCENARIOS)}) or 'all'")
    p.add_argument("--node", help="override the target node")
    p.add_argument("--hold", type=float, help="seconds to keep each failure in place")

    p = sub.add_parser("web", help="read-only live status page")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)

    args = ap.parse_args(argv)
    # The driver logs every failed request; we report failures ourselves.
    logging.getLogger("clickhouse_connect").setLevel(logging.CRITICAL)
    cfg = Config(args.config)
    console = Console()

    if args.cmd == "run":
        from .app import App
        App(cfg, args.mode).run(headless=args.headless)

    elif args.cmd == "status":
        from .dashboard import _nodes_table
        from .monitor import Monitor, healthy
        mon = Monitor(cfg)
        mon.poll_once()
        ok, reason = healthy(mon.nodes)
        console.print(Panel(_nodes_table(mon), title=f"VIP owner: {mon.vip or '-'}"))
        console.print(f"[bold {'green' if ok else 'red'}]{reason}[/]")
        return 0 if ok else 1

    elif args.cmd == "verify":
        from .verify import verify
        ok, _ = verify(cfg, allow_down=args.allow_down, console=console)
        return 0 if ok else 1

    elif args.cmd == "chaos":
        from .chaos import Chaos
        ACTIONS[args.action](Chaos(cfg, console), args.node)

    elif args.cmd == "web":
        from .web import serve
        serve(cfg, host=args.host, port=args.port)

    elif args.cmd == "scenario":
        from .chaos import Chaos
        nums = sorted(SCENARIOS) if args.numbers == ["all"] else [int(n) for n in args.numbers]
        ch = Chaos(cfg, console)
        results = {n: ch.run_scenario(n, node=args.node, hold=args.hold) for n in nums}
        console.rule("[bold]Summary")
        for n, ok in results.items():
            console.print(f"  S{n} {SCENARIOS[n].title:<45} {'[green]PASS[/]' if ok else '[red]FAIL[/]'}")
        console.print(f"Report: {cfg.runs_dir / 'report.md'}")
        return 0 if all(results.values()) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
