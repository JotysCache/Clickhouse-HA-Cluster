"""Per-operation counters with outage (time-to-recover) detection."""
import collections
import threading
import time

from . import events


class OpStats:
    """Tracks successes/failures of one operation type (write or read).

    An outage starts at the last success before a failure and ends at the next
    success; its length is what the user of the database experienced as
    downtime for this operation.
    """

    def __init__(self, cfg, name):
        self.cfg = cfg
        self.name = name
        self.lock = threading.Lock()
        self.ok = 0
        self.errors = 0
        self.rows = 0
        self.last_ok_ts = None
        self.last_endpoint = None
        self.last_latency_ms = None
        self.last_error = None
        self.last_error_ts = None
        self.outage_since = None
        self.outage_errors = 0
        self.outage_first_error = None
        self.outages = []
        self._window = collections.deque()

    def success(self, latency_s, endpoint, rows=0):
        now = time.time()
        with self.lock:
            if self.outage_since is not None:
                rec = events.append(
                    self.cfg.runs_dir / "outages.jsonl",
                    op=self.name, start=self.outage_since, end=now,
                    duration_s=round(now - self.outage_since, 3),
                    errors=self.outage_errors, first_error=self.outage_first_error,
                    recovered_on=endpoint,
                )
                self.outages.append(rec)
                events.event(self.cfg, "app",
                             f"{self.name} recovered after {rec['duration_s']:.1f}s "
                             f"({self.outage_errors} failed attempts) via {endpoint}")
                self.outage_since = None
            self.ok += 1
            self.rows += rows
            self.last_ok_ts = now
            self.last_endpoint = endpoint
            self.last_latency_ms = latency_s * 1000
            self._window.append((now, rows or 1))

    def failure(self, error):
        now = time.time()
        with self.lock:
            self.errors += 1
            self.last_error = error
            self.last_error_ts = now
            if self.outage_since is None:
                self.outage_since = self.last_ok_ts or now
                self.outage_errors = 0
                self.outage_first_error = error
                events.event(self.cfg, "app", f"{self.name} failing: {error}")
            self.outage_errors += 1

    def rate(self, window_s=5.0):
        now = time.time()
        with self.lock:
            while self._window and self._window[0][0] < now - window_s:
                self._window.popleft()
            return sum(n for _, n in self._window) / window_s

    def in_outage_for(self):
        with self.lock:
            return None if self.outage_since is None else time.time() - self.outage_since
