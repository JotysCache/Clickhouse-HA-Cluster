"""Fault injection over SSH, and self-checking failure scenarios.

Each scenario: check the cluster is healthy -> break something -> hold the
failure while probing reads/writes -> repair -> wait for full convergence ->
verify zero loss against the ack ledger -> append a row to runs/report.md.
The workload (`hatest run`) should be running in another terminal meanwhile;
its measured downtime is pulled into the report.
"""
import datetime as dt
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from rich.console import Console

from . import events
from .client import HAClient, endpoints_for, short_error
from .ledger import Ledger
from .monitor import Monitor, healthy, http_ping, keeper_role, vip_owner
from .verify import verify

SERVER, KEEPER, HAPROXY, KEEPALIVED = "clickhouse-server", "clickhouse-keeper", "haproxy", "keepalived"


class ChaosError(Exception):
    pass


class Chaos:
    def __init__(self, cfg, console=None):
        self.cfg = cfg
        self.console = console or Console()
        self.mon = Monitor(cfg)

    # ---- plumbing ------------------------------------------------------------
    def log(self, msg):
        events.event(self.cfg, "chaos", msg)
        self.console.print(f"[dim]{dt.datetime.now():%H:%M:%S}[/] [bold magenta]chaos[/] {msg}")

    def ssh(self, node, cmd, timeout=60, check=True):
        host = self.cfg.node(node).host
        argv = ["ssh", *self.cfg.ssh_options, f"{self.cfg.ssh_user}@{host}", cmd]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if check:
                raise ChaosError(f"{node}: ssh timed out: {cmd}")
            return None
        if check and r.returncode != 0:
            raise ChaosError(f"{node}: `{cmd}` failed ({r.returncode}): {(r.stderr or r.stdout).strip()}")
        return r.stdout.strip() if r.returncode == 0 else None

    def sudo(self, node, cmd, **kw):
        return self.ssh(node, f"sudo -n sh -c {shlex.quote(cmd)}", **kw)

    def others(self, node):
        return [n for n in self.cfg.nodes if n.name != node]

    # ---- discovery -------------------------------------------------------------
    def keeper_leader(self):
        for n in self.cfg.nodes:
            if keeper_role(n.host, self.cfg.keeper_port) == "leader":
                return n.name
        return None

    def vip_owner(self):
        owner = vip_owner(self.cfg)
        if owner in (None, "?", "no-backend"):
            for n in self.cfg.nodes:
                if self.ssh(n.name, f"ip -o addr show | grep -qw {self.cfg.vip}", timeout=10, check=False) is not None:
                    return n.name
            return None
        return owner

    def serving_node(self):
        """Replica currently answering app traffic (through the VIP if there is one)."""
        mode = "vip" if self.cfg.vip else "client"
        try:
            name, _ = HAClient(self.cfg, endpoints_for(self.cfg, mode), query_s=5).run(
                lambda c: c.query("SELECT getMacro('replica')").result_rows[0][0])
            return name
        except Exception:
            return self.cfg.nodes[0].name

    def resolve(self, target):
        """Node name, or an alias: leader (Keeper leader), vip (VIP owner), serving."""
        if target == "leader":
            name = self.keeper_leader()
        elif target == "vip":
            name = self.vip_owner()
        elif target == "serving":
            name = self.serving_node()
        else:
            return self.cfg.node(target).name
        if not name:
            raise ChaosError(f"could not resolve '{target}'")
        return name

    # ---- primitive actions -------------------------------------------------------
    def service(self, node, unit, action):
        if action == "kill":
            # SIGKILL the main process only, like `kill -9 <pid>` (systemd restarts it).
            cmd = f"kill -9 $(systemctl show -p MainPID --value {unit})"
        else:
            cmd = f"systemctl {action} {unit}"
        # A graceful ClickHouse stop waits for open client connections (keep-alive),
        # which can take ~60 s: allow well beyond that before calling it a failure.
        self.sudo(node, cmd, timeout=180)
        self.log(f"{action} {unit} on {node}")

    def repair_all(self):
        """Best-effort return to a fully running cluster after a tooling error."""
        for n in self.cfg.nodes:
            try:
                self.heal(n.name)
            except ChaosError:
                pass
            for unit in (KEEPER, SERVER, HAPROXY, KEEPALIVED):
                try:
                    self.sudo(n.name, f"systemctl start {unit}", timeout=180)
                except ChaosError as e:
                    self.log(f"repair: could not start {unit} on {n.name}: {e}")
        self.log("repair: all services started and partitions healed on every node")

    def partition(self, node, safety_s):
        """Drop all traffic between `node` and its peers (SSH from here keeps working).

        A systemd timer on the node heals the partition after `safety_s` in case
        we lose track of it.
        """
        rules = []
        for peer in self.others(node):
            rules += [f"INPUT -s {peer.host}", f"OUTPUT -d {peer.host}"]
        add = "; ".join(f"iptables -I {r} -m comment --comment chpoc -j DROP" for r in rules)
        heal = "; ".join(f"while iptables -D {r} -m comment --comment chpoc -j DROP 2>/dev/null; do :; done"
                         for r in rules)
        self.sudo(node, "systemctl stop chpoc-heal.timer chpoc-heal.service 2>/dev/null; "
                        "systemctl reset-failed chpoc-heal.timer chpoc-heal.service 2>/dev/null; "
                        f"systemd-run --quiet --unit chpoc-heal --on-active={int(safety_s)} /bin/sh -c \"{heal}\"; "
                        f"{add}")
        self.log(f"network partition: {node} isolated from {', '.join(p.name for p in self.others(node))} "
                 f"(auto-heal in {int(safety_s)}s)")

    def heal(self, node):
        rules = []
        for peer in self.others(node):
            rules += [f"INPUT -s {peer.host}", f"OUTPUT -d {peer.host}"]
        self.sudo(node, "systemctl stop chpoc-heal.timer 2>/dev/null; " + "; ".join(
            f"while iptables -D {r} -m comment --comment chpoc -j DROP 2>/dev/null; do :; done" for r in rules))
        self.log(f"network partition healed on {node}")

    def reboot(self, node):
        """Hard reset via sysrq: no shutdown, no fs sync. Simulates pulling the power cord."""
        self.sudo(node, "echo 1 > /proc/sys/kernel/sysrq; "
                        "(sleep 1; echo b > /proc/sysrq-trigger) >/dev/null 2>&1 &", timeout=15, check=False)
        self.log(f"HARD RESET of {node} (sysrq-b, like a power cut)")

    def wait_back(self, node, timeout):
        """Wait until the VM answers SSH and ClickHouse + Keeper services are active again."""
        deadline = time.time() + timeout
        time.sleep(10)
        while time.time() < deadline:
            out = self.ssh(node, f"systemctl is-active {SERVER} {KEEPER}", timeout=10, check=False)
            if out and all(s == "active" for s in out.split()):
                self.log(f"{node} is back (services active)")
                return
            time.sleep(5)
        raise ChaosError(f"{node} did not come back within {timeout}s")

    # ---- observation ----------------------------------------------------------------
    def probe(self, label):
        """Can the cluster serve a read and a quorum write right now (trying every node)?"""
        hc = HAClient(self.cfg, endpoints_for(self.cfg, "client"), query_s=12)
        res = {"read": False, "write": False, "vip": None, "notes": []}
        try:
            _, ep = hc.run(lambda c: c.query("SELECT count() FROM events").result_rows)
            res["read"] = True
            res["notes"].append(f"read via {ep.name}")
        except Exception as e:
            res["notes"].append(f"read: {short_error(e)}")
        try:
            row = [[dt.datetime.now(dt.timezone.utc), f"scenario {label}"]]
            _, ep = hc.run(lambda c: c.insert(
                "probe", row, column_names=["ts", "scenario"],
                settings={"insert_quorum": self.cfg.insert_quorum, "insert_quorum_timeout": 5000}))
            res["write"] = True
            res["notes"].append(f"write via {ep.name}")
        except Exception as e:
            res["notes"].append(f"write: {short_error(e)}")
        hc.close()
        if self.cfg.vip:
            owner = vip_owner(self.cfg)
            res["vip"] = owner not in (None, "no-backend") and http_ping(self.cfg.vip, self.cfg.http_port)
            res["notes"].append(f"VIP on {owner}")
        return res

    def converge(self, timeout):
        """Seconds until the cluster is fully healthy again, or None on timeout."""
        t0 = time.time()
        last_reason = None
        while time.time() - t0 < timeout:
            nodes, _ = self.mon.poll_once()
            ok, reason = healthy(nodes)
            if ok:
                return time.time() - t0
            if reason != last_reason:
                self.console.print(f"  waiting for convergence: {reason}")
                last_reason = reason
            time.sleep(3)
        return None

    # ---- scenarios -----------------------------------------------------------------
    def run_scenario(self, num, node=None, hold=None):
        sc = SCENARIOS[num]
        hold = hold or self.cfg.hold_s
        self.console.rule(f"[bold]Scenario {num}: {sc.title}")
        self.console.print(f"[dim]Expected: {sc.expected}[/]")

        pre = self.converge(self.cfg.converge_timeout_s)
        if pre is None:
            self.log(f"S{num} SKIPPED: cluster not healthy before start")
            return self._report(num, sc, "-", None, None, None, None, "SKIPPED (unhealthy at start)")

        t0 = time.time()
        ledger = Ledger(self.cfg.runs_dir / "ledger.sqlite")
        app_running = ledger.last_ack_time(self.cfg.writer_id) > t0 - 10
        if not app_running:
            self.console.print("[yellow]The workload is not running (`hatest run`): app downtime won't be measured.[/]")
        self.log(f"S{num} START: {sc.title}")
        try:
            target, recover = sc.breaker(self, node)
        except (ChaosError, StopIteration) as e:
            self.log(f"S{num} ERROR while injecting the failure: {e}")
            self.repair_all()
            self.converge(self.cfg.converge_timeout_s)
            return self._report(num, sc, f"error: {e}", None, None, None, None, "ERROR (tooling)")
        t_broken = time.time()
        self.log(f"S{num} failure in place: {target}")

        time.sleep(max(0.0, hold / 2 - (time.time() - t_broken)))
        probe = self.probe(num)
        self.log(f"S{num} probe during failure: read={'OK' if probe['read'] else 'FAIL'} "
                 f"write={'OK' if probe['write'] else 'FAIL'} ({'; '.join(probe['notes'])})")
        time.sleep(max(0.0, hold - (time.time() - t_broken)))

        self.log(f"S{num} repairing")
        try:
            recover()
        except ChaosError as e:
            self.log(f"S{num} repair step failed: {e}")
            self.repair_all()
        heal_s = self.converge(self.cfg.converge_timeout_s)
        self.log(f"S{num} cluster healthy again after {heal_s:.0f}s" if heal_s is not None
                 else f"S{num} cluster NOT healthy after {self.cfg.converge_timeout_s:.0f}s")

        if app_running:
            self._wait_app_recovered(ledger)
        ok_verify, summary = verify(self.cfg, console=self.console)
        passed = (probe["read"] and probe["write"] == sc.expect_write and heal_s is not None and ok_verify
                  and probe["vip"] is not False)
        result = "PASS" if passed else "FAIL"
        self.log(f"S{num} {result}")
        outages = events.read(self.cfg.runs_dir / "outages.jsonl", since=t0)
        return self._report(num, sc, target, probe, heal_s, summary, outages, result, app_running=app_running)

    def _wait_app_recovered(self, ledger):
        """Close the scenario only once the app's writes and reads flow again, so each
        outage is attributed to the scenario that caused it."""
        t_repair = time.time()
        while time.time() - t_repair < self.cfg.converge_timeout_s:
            if ledger.last_ack_time(self.cfg.writer_id) > t_repair:
                break
            time.sleep(1)
        else:
            self.log("app writes have NOT resumed")
            return
        time.sleep(2 * self.cfg.read_interval_s + 1)  # let the reader get a success in too

    def _report(self, num, sc, target, probe, heal_s, summary, outages, result, app_running=True):
        path = self.cfg.runs_dir / "report.md"
        if not path.exists():
            path.write_text(
                "| When | # | Scenario | What was broken | Probe read | Probe write (expected) | VIP | "
                "App write downtime | App read downtime | Heal time | Acked rows lost | Result |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|\n")

        def downtime(op):
            if probe is None:
                return "-"
            if not app_running:
                return "app not running"
            durations = [o["duration_s"] for o in (outages or []) if o.get("op") == op]
            return f"{max(durations):.1f}s ({len(durations)}x)" if durations else "0s (no errors)"

        def yn(v):
            return "-" if v is None else ("OK" if v else "FAIL")

        row = [dt.datetime.now().strftime("%Y-%m-%d %H:%M"), str(num), sc.title, target,
               yn(probe and probe["read"]) if probe else "-",
               f"{yn(probe['write'])} ({'OK' if sc.expect_write else 'FAIL'})" if probe else "-",
               yn(probe["vip"]) if probe else "-",
               downtime("write"), downtime("read"),
               f"{heal_s:.0f}s" if heal_s is not None else "-",
               str(summary.get("lost_rows", "-")) if summary else "-",
               f"**{result}**"]
        with open(path, "a") as f:
            f.write("| " + " | ".join(c.replace("|", "/") for c in row) + " |\n")
        self.console.print(f"[bold]{result}[/] - appended to {path}")
        return result == "PASS"


# ---- scenario definitions -----------------------------------------------------------
@dataclass
class Scenario:
    title: str
    expected: str
    expect_write: bool
    breaker: Callable  # (chaos, node) -> (description, recover_fn)


def _stop_server(ch, node):
    node = ch.resolve(node or ch.cfg.nodes[0].name)
    ch.service(node, SERVER, "stop")
    return f"{SERVER} stopped on {node}", lambda: ch.service(node, SERVER, "start")


def _kill_serving(ch, node):
    node = ch.resolve(node or "serving")
    ch.service(node, SERVER, "kill")
    # systemd restarts it on its own (Restart=always); starting explicitly is a no-op then.
    return f"{SERVER} SIGKILLed on {node} (the node serving traffic)", lambda: ch.service(node, SERVER, "start")


def _stop_keeper_leader(ch, node):
    node = ch.resolve(node or "leader")
    ch.service(node, KEEPER, "stop")
    t0, leader = time.time(), None
    while time.time() - t0 < 30 and not leader:
        time.sleep(1)
        leader = ch.keeper_leader()
    ch.log(f"new Keeper leader: {leader} after {time.time() - t0:.0f}s" if leader else "no Keeper leader after 30s")
    return (f"Keeper leader {node} stopped; new leader: {leader or 'none within 30s'}",
            lambda: ch.service(node, KEEPER, "start"))


def _keeper_quorum_loss(ch, node):
    leader = ch.resolve("leader")
    second = next(n.name for n in ch.others(leader) if n.name != node) if node else ch.others(leader)[0].name
    for n in (leader, second):
        ch.service(n, KEEPER, "stop")

    def recover():
        for n in (leader, second):
            ch.service(n, KEEPER, "start")
    return f"Keeper stopped on {leader} and {second} (1 of 3 left: no quorum)", recover


def _vip_failover(ch, node):
    if not ch.cfg.vip:
        raise ChaosError("scenario 5 needs a VIP (cluster.vip)")
    owner = ch.resolve(node or "vip")
    ch.service(owner, HAPROXY, "stop")
    new = None
    for _ in range(20):
        time.sleep(1)
        new = vip_owner(ch.cfg)
        if new not in (None, owner, "?", "no-backend"):
            break
    ch.log(f"VIP moved {owner} -> {new}")
    return f"HAProxy stopped on VIP owner {owner}; VIP moved to {new}", lambda: ch.service(owner, HAPROXY, "start")


def _hard_reboot(ch, node):
    node = ch.resolve(node or ch.cfg.nodes[1].name)
    ch.reboot(node)
    return f"{node} hard reset (power-cut style)", lambda: ch.wait_back(node, ch.cfg.converge_timeout_s)


def _partition(ch, node):
    if not node:
        leader, owner = ch.keeper_leader(), ch.vip_owner() if ch.cfg.vip else None
        # Prefer a plain follower so the test isolates one effect at a time.
        candidates = [n.name for n in ch.cfg.nodes if n.name not in (leader, owner)] or \
                     [n.name for n in ch.cfg.nodes if n.name != owner]
        node = candidates[-1]
    node = ch.resolve(node)
    ch.partition(node, safety_s=ch.cfg.hold_s + ch.cfg.converge_timeout_s)
    return f"{node} network-partitioned from its peers", lambda: ch.heal(node)


def _two_servers_down(ch, node):
    keep = ch.resolve(node or ch.cfg.nodes[-1].name)
    victims = [n.name for n in ch.others(keep)]
    for n in victims:
        ch.service(n, SERVER, "stop")

    def recover():
        for n in victims:
            ch.service(n, SERVER, "start")
    return f"{SERVER} stopped on {' and '.join(victims)}; only {keep} left", recover


SCENARIOS = {
    1: Scenario("Stop ClickHouse on one node",
                "reads and writes continue on the other 2; the node catches up on return", True, _stop_server),
    2: Scenario("kill -9 ClickHouse on the serving node",
                "in-flight requests fail and are retried elsewhere; systemd restarts the process", True,
                _kill_serving),
    3: Scenario("Stop the Keeper leader",
                "a new leader is elected within seconds; writes pause briefly then resume", True,
                _stop_keeper_leader),
    4: Scenario("Keeper quorum loss (2 of 3 Keepers down)",
                "tables turn read-only: reads OK, writes REJECTED (by design); writes resume on repair", False,
                _keeper_quorum_loss),
    5: Scenario("Stop HAProxy on the VIP owner",
                "keepalived moves the VIP to another node within seconds", True, _vip_failover),
    6: Scenario("Hard reset of a whole VM",
                "server + Keeper lost at once; cluster keeps serving; node rejoins after boot", True,
                _hard_reboot),
    7: Scenario("Network partition of one node",
                "isolated node goes read-only; majority keeps serving; it converges after healing", True,
                _partition),
    8: Scenario("ClickHouse down on 2 of 3 nodes",
                "reads OK from the last node; writes REJECTED because insert_quorum=2 cannot be met", False,
                _two_servers_down),
}

ACTIONS = {
    "stop-server": lambda ch, n: ch.service(ch.resolve(n), SERVER, "stop"),
    "start-server": lambda ch, n: ch.service(ch.resolve(n), SERVER, "start"),
    "kill-server": lambda ch, n: ch.service(ch.resolve(n), SERVER, "kill"),
    "stop-keeper": lambda ch, n: ch.service(ch.resolve(n), KEEPER, "stop"),
    "start-keeper": lambda ch, n: ch.service(ch.resolve(n), KEEPER, "start"),
    "kill-keeper": lambda ch, n: ch.service(ch.resolve(n), KEEPER, "kill"),
    "stop-haproxy": lambda ch, n: ch.service(ch.resolve(n), HAPROXY, "stop"),
    "start-haproxy": lambda ch, n: ch.service(ch.resolve(n), HAPROXY, "start"),
    "stop-keepalived": lambda ch, n: ch.service(ch.resolve(n), KEEPALIVED, "stop"),
    "start-keepalived": lambda ch, n: ch.service(ch.resolve(n), KEEPALIVED, "start"),
    "partition": lambda ch, n: ch.partition(ch.resolve(n), safety_s=600),
    "heal": lambda ch, n: ch.heal(ch.resolve(n)),
    "reboot": lambda ch, n: ch.reboot(ch.resolve(n)),
}
