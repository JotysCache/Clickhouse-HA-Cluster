"""Continuous writer and reader threads."""
import datetime as dt
import secrets
import threading
import time

from .client import short_error

COLUMNS = ["seq", "batch_id", "ts", "writer_id", "payload"]


class Writer(threading.Thread):
    def __init__(self, cfg, client, ledger, stats, stop):
        super().__init__(name="writer", daemon=True)
        self.cfg, self.client, self.ledger, self.stats, self.stop = cfg, client, ledger, stats, stop
        self.acked_max = ledger.max_seq(cfg.writer_id)

    def _start_seq(self):
        # Rows can exist in ClickHouse that the ledger never saw (the app died after the
        # server committed but before the ack arrived). Never reuse those seq numbers.
        q = "SELECT max(seq) FROM events WHERE writer_id = {w:String}"
        while not self.stop.is_set():
            try:
                db_max, _ = self.client.run(
                    lambda c: c.query(q, parameters={"w": self.cfg.writer_id}).result_rows[0][0])
                return max(self.acked_max, db_max) + 1
            except Exception as e:
                self.stats.failure(short_error(e))
                self.stop.wait(1)
        return None

    def run(self):
        cfg = self.cfg
        seq = self._start_seq()
        interval = 1.0 / cfg.batches_per_sec
        next_t = time.monotonic()
        while seq is not None and not self.stop.is_set():
            first, last = seq, seq + cfg.batch_size - 1
            now = dt.datetime.now(dt.timezone.utc)
            rows = [[s, first, now, cfg.writer_id, secrets.token_hex(cfg.payload_bytes // 2)]
                    for s in range(first, last + 1)]
            settings = {
                "insert_quorum": cfg.insert_quorum,
                "insert_quorum_timeout": cfg.insert_quorum_timeout_ms,
                # Same token on every retry of this batch: if an earlier attempt actually
                # landed (e.g. the ack was lost), ClickHouse drops the retry as a duplicate.
                "insert_deduplication_token": f"{cfg.writer_id}:{first}",
            }
            attempt = 0
            while not self.stop.is_set():
                t0 = time.monotonic()
                try:
                    _, ep = self.client.run(
                        lambda c: c.insert("events", rows, column_names=COLUMNS, settings=settings))
                except Exception as e:
                    attempt += 1
                    self.stats.failure(short_error(e))
                    self.stop.wait(min(0.25 * attempt, 2.0))
                    continue
                self.ledger.record(cfg.writer_id, first, last, ep.name)
                self.acked_max = last
                self.stats.success(time.monotonic() - t0, ep.name, rows=len(rows))
                seq = last + 1
                break

            next_t += interval
            delay = next_t - time.monotonic()
            if delay > 0:
                self.stop.wait(delay)
            else:
                next_t = time.monotonic()  # fell behind during an outage: don't burst to catch up


class Reader(threading.Thread):
    QUERY = ("SELECT count(), max(seq), getMacro('replica') "
             "FROM events WHERE writer_id = {w:String}")

    def __init__(self, cfg, client, writer, stats, stop):
        super().__init__(name="reader", daemon=True)
        self.cfg, self.client, self.writer, self.stats, self.stop = cfg, client, writer, stats, stop
        self.last = {}

    def run(self):
        while not self.stop.is_set():
            acked_before = self.writer.acked_max
            t0 = time.monotonic()
            try:
                (count, max_seq, replica), ep = self.client.run(
                    lambda c: c.query(self.QUERY, parameters={"w": self.cfg.writer_id}).result_rows[0])
            except Exception as e:
                self.stats.failure(short_error(e))
            else:
                self.stats.success(time.monotonic() - t0, ep.name)
                self.last = {
                    "count": count,
                    "max_seq": max_seq,
                    "replica": replica,
                    # rows acked before this read started but not visible on the replica that answered
                    "lag_rows": max(0, acked_before - max_seq),
                }
            self.stop.wait(self.cfg.read_interval_s)
