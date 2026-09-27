"""`hatest run`: writer + reader + monitor, with the live dashboard."""
import signal
import threading
import time

from rich.console import Console
from rich.live import Live

from . import dashboard, events
from .client import HAClient, endpoints_for
from .ledger import Ledger
from .monitor import Monitor
from .stats import OpStats
from .workload import Reader, Writer


class App:
    def __init__(self, cfg, mode):
        self.cfg, self.mode = cfg, mode
        self.stop = threading.Event()
        self.started = time.time()
        self.endpoints = endpoints_for(cfg, mode)
        self.ledger = Ledger(cfg.runs_dir / "ledger.sqlite")
        self.write_stats = OpStats(cfg, "write")
        self.read_stats = OpStats(cfg, "read")
        self.writer = Writer(cfg, HAClient(cfg, self.endpoints), self.ledger, self.write_stats, self.stop)
        self.reader = Reader(cfg, HAClient(cfg, self.endpoints, query_s=5), self.writer,
                             self.read_stats, self.stop)
        self.monitor = Monitor(cfg, self.stop)

    def run(self, headless=False):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.stop.set())
        events.event(self.cfg, "app", f"workload started (mode={self.mode})")
        self.monitor.start()
        self.writer.start()
        self.reader.start()
        if headless:
            while not self.stop.wait(5):
                print(dashboard.headless_line(self), flush=True)
        else:
            with Live(dashboard.render(self), console=Console(), screen=True, refresh_per_second=2) as live:
                while not self.stop.wait(0.5):
                    live.update(dashboard.render(self))
        for t in (self.writer, self.reader):
            t.join(timeout=30)
        events.event(self.cfg, "app",
                     f"workload stopped: {self.write_stats.rows:,} rows acked, "
                     f"{self.write_stats.errors} write errors, {self.read_stats.errors} read errors")
        print(f"Stopped. Acked seq up to {self.writer.acked_max}. Run `python -m hatest verify` next.")
