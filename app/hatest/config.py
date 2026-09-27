import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Node:
    name: str
    host: str


class Config:
    def __init__(self, path):
        path = Path(path).resolve()
        raw = yaml.safe_load(path.read_text())
        c = raw["cluster"]
        self.nodes = [Node(n["name"], n["host"]) for n in c["nodes"]]
        self.vip = c.get("vip")
        self.http_port = c.get("http_port", 8123)
        self.keeper_port = c.get("keeper_port", 9181)
        self.database = c.get("database", "poc")

        auth = raw["auth"]
        self.user = auth["user"]
        pw_env = auth.get("password_env", "CH_APP_PASSWORD")
        self.password = os.environ.get(pw_env, auth.get("password"))
        if not self.password:
            raise SystemExit(f"Set the {pw_env} environment variable to the app user's password.")

        ssh = raw.get("ssh", {})
        self.ssh_user = ssh.get("user", "ubuntu")
        self.ssh_options = ssh.get("options", ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"])

        w = raw["workload"]
        self.writer_id = w.get("writer_id", "poc-writer")
        self.batch_size = int(w.get("batch_size", 100))
        self.batches_per_sec = float(w.get("batches_per_sec", 5))
        self.payload_bytes = int(w.get("payload_bytes", 64))
        self.insert_quorum = int(w.get("insert_quorum", 2))
        self.insert_quorum_timeout_ms = int(w.get("insert_quorum_timeout_ms", 15000))
        self.read_interval_s = float(w.get("read_interval_s", 1.0))

        t = raw.get("timeouts", {})
        self.connect_s = float(t.get("connect_s", 2))
        self.query_s = float(t.get("query_s", 20))

        ch = raw.get("chaos", {})
        self.hold_s = float(ch.get("hold_s", 40))
        self.converge_timeout_s = float(ch.get("converge_timeout_s", 300))

        self.runs_dir = path.parent / raw.get("runs_dir", "runs")
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def node(self, name):
        for n in self.nodes:
            if n.name == name:
                return n
        raise SystemExit(f"Unknown node {name!r}; known: {', '.join(n.name for n in self.nodes)}")
