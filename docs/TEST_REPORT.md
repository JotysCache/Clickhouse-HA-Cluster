# ClickHouse HA POC: test report

| | |
|---|---|
| Dates | 2026-09-27 and 2026-09-28 |
| Cluster | 3 × Ubuntu 24.04, 2 vCPU / 4 GB, ClickHouse 26.8 LTS (server + Keeper) |
| Client | `chpoc-app`, 2 vCPU / 2 GB, in the same VPC |
| Workload | 100 rows × 5 batches/s, `insert_quorum = 2`, a read every second |
| Failover modes tested | through the VIP (HAProxy + keepalived) and client-side |

## Pre-flight

- `playbooks/health.yml`: HEALTHY. A second `site.yml` run: `changed=0` on all hosts.
- `hatest status`: healthy. `hatest run`: 0 errors at steady state.

## Results, VIP mode (final run)

| # | Scenario | Probe read | Probe write (expected) | App write downtime | App read downtime | Heal time | Acked rows lost | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | ClickHouse stopped on one node | OK | OK (OK) | 0.6 s | 0 s | 28 s | 0 | PASS |
| 2 | kill -9 on the serving node | OK | OK (OK) | 0.9 s | 0 s | 3 s | 0 | PASS |
| 3 | Keeper leader stopped | OK | OK (OK) | 0 s | 0 s | 3 s | 0 | PASS |
| 4 | Keeper quorum lost | OK | refused (refused) | 83.0 s | 0 s | 9 s | 0 | PASS |
| 5 | HAProxy stopped on VIP owner | OK | OK (OK) | 2.8 s | 3.2 s | 6 s | 0 | PASS |
| 6 | Hard reset of a VM | OK | OK (OK) | 27.2 s | 7.8 s | 3 s | 0 | PASS |
| 7 | Network partition of one node | OK | OK (OK) | 31.6 s | 0 s | 21 s | 0 | PASS |
| 8 | ClickHouse down on 2 of 3 nodes | OK | refused (refused) | 69.4 s | 0 s | 3 s | 0 | PASS |

Scenario 4 needed a re-run: the first run's FAIL was the verifier checking before the replicas had converged (see finding 9). The raw report shows both rows.

## Results, client-side failover

| # | Scenario | App write downtime | App read downtime | Heal time | Acked rows lost | Result |
|---|---|---|---|---|---|---|
| 1 | ClickHouse stopped on one node | 0 s | 0 s | 3 s | 0 | PASS |
| 3 | Keeper leader stopped | 0 s | 0 s | 6 s | 0 | PASS |
| 7 | Network partition of one node | 0 s | 0 s | 6 s | 0 | PASS |

## Final verification

3,005,000 acknowledged rows on each of the three replicas after the final VIP-mode run, 0 missing, 0 duplicates, identical checksums (`a2981d228fd920e4`). After the client-mode run: 3,141,800 rows, same result.

## Findings and follow-ups

See [walkthrough/05-results.md](walkthrough/05-results.md#findings): twelve findings. Eight were fixed and re-tested (1 to 5 and 8 to 10), two describe the environment (6 and 7), and two are open follow-ups (11 and 12). Open follow-ups: remove the public IP from `chpoc-1`; ask the cloud provider about the network event at 05:34:20 to 05:35:15 UTC on 2026-09-28; the rsyslog AppArmor override for HAProxy logging; monitoring, backups and TLS before any production use.

Raw reports: [docs/results/](results/).
