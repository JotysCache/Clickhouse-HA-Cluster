| When | # | Scenario | What was broken | Probe read | Probe write (expected) | VIP | App write downtime | App read downtime | Heal time | Acked rows lost | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-28 05:54 | 1 | Stop ClickHouse on one node | clickhouse-server stopped on chpoc-1 | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 3s | 0 | **PASS** |
| 2026-09-28 05:55 | 3 | Stop the Keeper leader | Keeper leader chpoc-3 stopped; new leader: chpoc-1 | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-28 05:56 | 7 | Network partition of one node | chpoc-3 network-partitioned from its peers | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 6s | 0 | **PASS** |
