| When | # | Scenario | What was broken | Probe read | Probe write (expected) | VIP | App write downtime | App read downtime | Heal time | Acked rows lost | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-27 11:52 | 1 | Stop ClickHouse on one node | clickhouse-server stopped on chpoc-1 | OK | OK (OK) | OK | 0.5s (1x) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-27 11:56 | 2 | kill -9 ClickHouse on the serving node | clickhouse-server SIGKILLed on chpoc-1 (the node serving traffic) | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 0s | 0 | **PASS** |
| 2026-09-27 11:57 | 3 | Stop the Keeper leader | Keeper leader chpoc-1 stopped; new leader: chpoc-2 | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-27 11:59 | 4 | Keeper quorum loss (2 of 3 Keepers down) | Keeper stopped on chpoc-2 and chpoc-1 (1 of 3 left: no quorum) | OK | FAIL (FAIL) | OK | 68.2s (1x) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-27 12:00 | 5 | Stop HAProxy on the VIP owner | HAProxy stopped on VIP owner chpoc-2; VIP moved to chpoc-1 | OK | OK (OK) | OK | 6.6s (1x) | 7.2s (1x) | 0s | 0 | **PASS** |
| 2026-09-27 12:06 | 6 | Hard reset of a whole VM | chpoc-2 hard reset (power-cut style) | OK | OK (OK) | OK | 27.4s (141x) | 7.1s (47x) | - | 0 | **FAIL** |
| 2026-09-27 12:23 | 6 | Hard reset of a whole VM | chpoc-2 hard reset (power-cut style) | OK | OK (OK) | OK | 13.9s (151x) | 4.3s (54x) | - | 0 | **FAIL** |
| 2026-09-27 12:28 | 7 | Network partition of one node | chpoc-2 network-partitioned from its peers | OK | OK (OK) | FAIL | 53.8s (1x) | 45.2s (1x) | 7s | 0 | **FAIL** |
| 2026-09-27 12:30 | 8 | ClickHouse down on 2 of 3 nodes | clickhouse-server stopped on chpoc-1 and chpoc-2; only chpoc-3 left | OK | FAIL (FAIL) | OK | 49.7s (2x) | 0s (no errors) | 3s | 0 | **PASS** |
