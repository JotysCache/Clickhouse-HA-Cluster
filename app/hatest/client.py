"""Connection layer with two failover strategies.

vip    : one endpoint (the HAProxy VIP). Failover happens in HAProxy/keepalived;
         the client only reconnects.
client : the app knows every node. It sticks to one node and, on any error,
         moves on to the next one (like a smart driver would).
"""
import re
from typing import NamedTuple

import clickhouse_connect


class Endpoint(NamedTuple):
    name: str
    host: str
    port: int


def endpoints_for(cfg, mode):
    if mode == "vip":
        if not cfg.vip:
            raise SystemExit("--mode vip needs cluster.vip in config.yaml")
        return [Endpoint("vip", cfg.vip, cfg.http_port)]
    if mode == "client":
        return [Endpoint(n.name, n.host, cfg.http_port) for n in cfg.nodes]
    raise SystemExit(f"unknown mode {mode!r}")


def connect(cfg, ep, query_s=None):
    return clickhouse_connect.get_client(
        host=ep.host,
        port=ep.port,
        username=cfg.user,
        password=cfg.password,
        database=cfg.database,
        connect_timeout=cfg.connect_s,
        send_receive_timeout=query_s or cfg.query_s,
        query_retries=0,  # retries/failover are our job, not the driver's
        autogenerate_session_id=False,
    )


class HAClient:
    def __init__(self, cfg, endpoints, query_s=None):
        self.cfg = cfg
        self.endpoints = list(endpoints)
        self.query_s = query_s
        self._idx = 0
        self._clients = {}

    def _client(self, ep):
        client = self._clients.get(ep.name)
        if client is None:
            client = connect(self.cfg, ep, self.query_s)
            self._clients[ep.name] = client
        return client

    def _drop(self, ep):
        client = self._clients.pop(ep.name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def run(self, fn):
        """Call fn(client) on the current endpoint, failing over through the others.

        Returns (result, endpoint). Raises the last error if every endpoint failed.
        """
        error = None
        n = len(self.endpoints)
        for i in range(n):
            idx = (self._idx + i) % n
            ep = self.endpoints[idx]
            try:
                result = fn(self._client(ep))
            except Exception as e:
                self._drop(ep)
                # Report why a live server refused over "connection refused" from a dead one.
                if error is None or ("DB::Exception" in str(e) and "DB::Exception" not in str(error)):
                    error = e
                continue
            self._idx = idx
            return result, ep
        self._idx = (self._idx + 1) % n
        raise error

    def close(self):
        for ep in self.endpoints:
            self._drop(ep)


_CH_ERROR = re.compile(r"Code: (\d+)\. DB::Exception: (.*?)\.? (?:\(([A-Z][A-Z_]+)\) )?\(version")
_NET_ERRORS = ("Connection refused", "timed out", "Read timed out", "Connection reset",
               "RemoteDisconnected", "No route to host", "Name or service not known",
               "Connection aborted", "Max retries exceeded")


def short_error(e):
    """One-line, human readable error for the dashboard and logs."""
    msg = " ".join(str(e).split())
    m = _CH_ERROR.search(msg)
    if not m and "DB::Exception" in msg:
        code = re.search(r"Code: (\d+)", msg)
        return f"CH{code.group(1) if code else '?'} {msg.split('DB::Exception: ', 1)[-1][:90]}"
    if m:
        code, text, name = m.groups()
        return f"CH{code} {name + ': ' if name else ''}{text[:80]}"
    for key in _NET_ERRORS:
        if key in msg:
            return f"network: {key}"
    return f"{type(e).__name__}: {msg[:100]}"
