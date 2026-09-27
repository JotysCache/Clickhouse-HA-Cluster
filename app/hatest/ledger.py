"""Local record of every INSERT the cluster acknowledged.

This is the ground truth for "zero data loss": every batch in here was
confirmed by ClickHouse (with insert_quorum), so it must exist on the cluster
afterwards, whatever we broke in between.
"""
import sqlite3
import time


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS acked (
                writer_id TEXT NOT NULL,
                batch_id  INTEGER NOT NULL,
                first_seq INTEGER NOT NULL,
                last_seq  INTEGER NOT NULL,
                rows      INTEGER NOT NULL,
                endpoint  TEXT,
                acked_at  REAL NOT NULL,
                PRIMARY KEY (writer_id, batch_id)
            )""")

    def record(self, writer_id, first_seq, last_seq, endpoint):
        self.db.execute(
            "INSERT OR IGNORE INTO acked VALUES (?, ?, ?, ?, ?, ?, ?)",
            (writer_id, first_seq, first_seq, last_seq, last_seq - first_seq + 1, endpoint, time.time()),
        )

    def max_seq(self, writer_id):
        row = self.db.execute("SELECT max(last_seq) FROM acked WHERE writer_id = ?", (writer_id,)).fetchone()
        return row[0] or 0

    def last_ack_time(self, writer_id):
        row = self.db.execute("SELECT max(acked_at) FROM acked WHERE writer_id = ?", (writer_id,)).fetchone()
        return row[0] or 0

    def batches(self, writer_id, upto_seq):
        """{batch_id: rows} for every acked batch whose rows are all <= upto_seq."""
        cur = self.db.execute(
            "SELECT batch_id, rows FROM acked WHERE writer_id = ? AND last_seq <= ?", (writer_id, upto_seq))
        return dict(cur.fetchall())
