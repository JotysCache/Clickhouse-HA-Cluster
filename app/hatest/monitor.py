"""Polls every node directly (never through the VIP) for an independent view of the cluster."""
import concurrent.futures as cf
import socket
import threading
import time
import urllib.error
import urllib.request

from .client import Endpoint, connect, short_error

REPLICA_QUERY = """
SELECT is_readonly, is_session_expired, absolute_delay, queue_size, active_replicas, total_replicas,
       (SELECT count() FROM events) AS row_count
FROM system.replicas
WHERE database = currentDatabase() AND table = 'events'
"""


def keeper_role(host, port, timeout=2.0):
    """leader | follower | standalone | no-quorum | down"""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"mntr")
            chunks = []
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
    except OSError:
        return "down"
    text = b"".join(chunks).decode(errors="replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "zk_server_state":
            return parts[1]
    return "no-quorum"


def http_ping(host, port, timeout=2.0):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ping", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def vip_owner(cfg, timeout=2.0):
    """Name of the node whose HAProxy currently answers on the VIP, or None."""
    if not cfg.vip:
        return None
    try:
        with urllib.request.urlopen(f"http://{cfg.vip}:{cfg.http_port}/ping", timeout=timeout) as r:
            return r.headers.get("X-HAProxy-Node") or "?"
    except urllib.error.HTTPError as e:  # HAProxy answered but has no healthy backend
        return e.headers.get("X-HAProxy-Node") or "no-backend"
    except OSError:
        return None


class Monitor(threading.Thread):
    def __init__(self, cfg, stop=None, interval=2.0):
        super().__init__(name="monitor", daemon=True)
        self.cfg = cfg
        self.stop = stop or threading.Event()
        self.interval = interval
        self.nodes = {n.name: {"server": "?", "keeper": "?"} for n in cfg.nodes}
        self.vip = None
        self.updated = None
        self._clients = {}
        self._pool = cf.ThreadPoolExecutor(max_workers=len(cfg.nodes) + 1)

    def _query(self, node):
        client = self._clients.get(node.name)
        try:
            if client is None:
                client = connect(self.cfg, Endpoint(node.name, node.host, self.cfg.http_port), query_s=4)
                self._clients[node.name] = client
            return client.query(REPLICA_QUERY).result_rows
        except Exception:
            self._clients.pop(node.name, None)
            raise

    def poll_node(self, node):
        st = {"server": "down", "keeper": keeper_role(node.host, self.cfg.keeper_port)}
        if http_ping(node.host, self.cfg.http_port):
            st["server"] = "up"
            try:
                rows = self._query(node)
            except Exception as e:
                st["error"] = short_error(e)
            else:
                if rows:
                    ro, expired, delay, queue, active, total, count = rows[0]
                    st.update(readonly=bool(ro or expired), delay=delay, queue=queue,
                              active=active, total=total, rows=count)
                else:
                    st["error"] = "table poc.events missing"
        return node.name, st

    def poll_once(self):
        futures = [self._pool.submit(self.poll_node, n) for n in self.cfg.nodes]
        vip = self._pool.submit(vip_owner, self.cfg)
        for f in futures:
            name, st = f.result()
            self.nodes[name] = st
        self.vip = vip.result()
        self.updated = time.time()
        return self.nodes, self.vip

    def run(self):
        while not self.stop.is_set():
            self.poll_once()
            self.stop.wait(self.interval)


def healthy(nodes):
    """(ok, reason) - True when the cluster is fully converged."""
    leaders = [n for n, s in nodes.items() if s.get("keeper") == "leader"]
    if len(leaders) != 1:
        return False, f"keeper leaders={leaders}"
    for name, s in nodes.items():
        if s.get("server") != "up":
            return False, f"{name} server {s.get('server')}"
        if s.get("keeper") not in ("leader", "follower"):
            return False, f"{name} keeper {s.get('keeper')}"
        if "error" in s:
            return False, f"{name}: {s['error']}"
        if s.get("readonly"):
            return False, f"{name} replica read-only"
        if s.get("queue", 1) or s.get("active") != s.get("total"):
            return False, f"{name} replication queue={s.get('queue')} active={s.get('active')}/{s.get('total')}"
    return True, "healthy"
