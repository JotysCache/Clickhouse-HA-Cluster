# 6. Operating the cluster

[5. Results](05-results.md) · [README](../../README.md)

All commands run from the laptop unless the prompt says otherwise. `ch` below means `sudo clickhouse-client --config-file /root/.clickhouse-client-admin.xml`, which logs in as `admin` on the node you are on.

## Is it healthy?

| Question | Command |
|---|---|
| Everything, with an assert | `cd ansible && ../.venv/bin/ansible-playbook playbooks/health.yml` |
| One line | `ssh chpoc-app hatest status` |
| Zero-loss check (needs the workload's ledger) | `ssh chpoc-app hatest verify` |
| In a browser | https://clickhouse-ha.tenbyte-dev.top |
| Keeper role of a node | `ssh chpoc-1 'echo mntr \| nc -w2 127.0.0.1 9181 \| grep zk_server_state'` |
| Who holds the VIP | `ssh chpoc-1 ip -o addr \| grep 10.107.40.100` (repeat per node) |
| HAProxy view | port 8404 on any node, e.g. `ssh -L 8404:10.107.40.5:8404 chpoc-app`, then http://localhost:8404/stats |
| Metrics | Prometheus format on :9363 of each node |

Useful SQL on a node:

```sql
-- replication health of this replica
SELECT table, is_readonly, absolute_delay, queue_size, active_replicas, total_replicas FROM system.replicas;
-- row counts on every replica at once
SELECT hostName(), count() FROM clusterAllReplicas('poc_cluster', poc.events) GROUP BY 1;
-- replication work that keeps failing
SELECT table, type, num_tries, last_exception FROM system.replication_queue WHERE last_exception != '';
```

## Changing configuration

Edit the role or `group_vars`, then roll it out one node at a time. Each run waits until its node is healthy:

```bash
cd ansible
for n in chpoc-1 chpoc-2 chpoc-3; do ../.venv/bin/ansible-playbook site.yml --limit $n || break; done
../.venv/bin/ansible-playbook playbooks/app_host.yml     # chpoc-app: test app, status page, proxy
```

To restart everything without a config change, for example after an upgrade:

```bash
../.venv/bin/ansible-playbook playbooks/rolling_restart.yml
```

It restarts Keeper and then ClickHouse on one node, waits for Keeper to rejoin and the replication queue to drain, then moves to the next node.

## Upgrading ClickHouse

The packages are held, so unattended upgrades never change them. To upgrade, set `clickhouse_version` in `group_vars/all/main.yml` to the exact version, then do the one-node-at-a-time rollout above. Read the release notes first. Upgrade Keeper and server together (they are the same binary here), and stay on an LTS release.

## Running the tests again

```bash
ssh chpoc-app
tmux new -s chpoc 'hatest run'                    # terminal 1: workload + dashboard (Ctrl-b d to detach)
hatest scenario all                               # terminal 2: all eight scenarios
MODE=client ./run_test.sh 1 3 7                   # or: unattended, with client-side failover
```

For a live demo, break things by hand:

```bash
hatest chaos stop-server chpoc-2      hatest chaos start-server chpoc-2
hatest chaos kill-server serving      hatest chaos stop-keeper leader
hatest chaos stop-haproxy vip         hatest chaos partition chpoc-3   /   hatest chaos heal chpoc-3
hatest chaos reboot chpoc-1
```

`leader`, `vip` and `serving` resolve to the current Keeper leader, the VIP owner and the node answering the app. Reports go to `/opt/chpoc/app/runs/report.md`.

## Recovery procedures

These are the procedures we needed during testing.

### A node does not come back after a crash or power cut

Check `sudo journalctl -u clickhouse-server` and `/var/log/clickhouse-server/clickhouse-server.err.log` on the node.

If the log says `Suspiciously many (N parts, X B in total) broken parts`, the node lost recently written parts in the crash. With this deployment's settings it repairs itself unless the broken parts total more than 1 GiB. A byte count that high means something worse than a crash, such as the wrong disk mounted or a corrupted disk: investigate before overriding anything.

To force the repair anyway, on that node only:

```bash
echo "<clickhouse><merge_tree><max_suspicious_broken_parts>100000</max_suspicious_broken_parts><max_suspicious_broken_parts_bytes>1000000000000</max_suspicious_broken_parts_bytes></merge_tree></clickhouse>" \
  | sudo tee /etc/clickhouse-server/config.d/99-recovery-broken-parts.xml
sudo systemctl restart clickhouse-server
ch -q "SELECT queue_size FROM system.replicas WHERE table='events'"   # wait until 0
sudo rm /etc/clickhouse-server/config.d/99-recovery-broken-parts.xml && sudo systemctl restart clickhouse-server
```

The broken parts are moved to the table's `detached/` directory, not deleted, and the replica fetches clean copies from its peers.

### A replica is read-only although Keeper is healthy

```sql
SYSTEM RESTART REPLICA poc.events;
```

If Keeper lost the table's metadata, run `SYSTEM RESTORE REPLICA poc.events` instead.

### Keeper lost its quorum for good (two VMs destroyed)

Start the surviving Keeper with `clickhouse keeper --force-recovery` using its normal config. Then rebuild the other two VMs with the same names and the same `keeper_id`s, and run `site.yml`.

### A partition was left in place

`hatest chaos heal chpoc-N`. The partition also removes itself after a safety delay (the `chpoc-heal` systemd timer on the node).

### A test left services stopped

Run `ssh chpoc-app hatest status` to see what is down, then start it with `hatest chaos start-server <node>` (or `start-keeper`, `start-haproxy`). The scenario runner does this by itself when it hits an error.

## Secrets

The secrets live in `ansible/group_vars/all/vault.yml`, encrypted. The vault key is in `~/.chpoc-vault-pass` on the laptop.

```bash
cd ansible
../.venv/bin/ansible-vault edit group_vars/all/vault.yml     # change a value
for n in chpoc-1 chpoc-2 chpoc-3; do ../.venv/bin/ansible-playbook site.yml --limit $n || break; done
../.venv/bin/ansible-playbook playbooks/app_host.yml
```

The Cloudflare origin certificate is valid until 2041. To replace it, create a new one in Cloudflare (SSL/TLS, Origin Server), put the certificate and key into `vault_origin_cert` and `vault_origin_key`, and run `playbooks/app_host.yml`.

## Tearing down

Delete the four VMs in the cloud console, the `clickhouse-ha` DNS record and the configuration rule in Cloudflare, and revoke the origin certificate there. On the laptop, remove the `chpoc` blocks from `~/.ssh/config` and delete `~/.chpoc-vault-pass`, `~/chpoc-origin.pem` and `~/chpoc-origin.key`.
