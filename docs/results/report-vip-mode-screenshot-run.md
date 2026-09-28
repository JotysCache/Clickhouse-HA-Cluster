| When | # | Scenario | What was broken | Probe read | Probe write (expected) | VIP | App write downtime | App read downtime | Heal time | Acked rows lost | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-28 04:31 | 1 | Stop ClickHouse on one node | clickhouse-server stopped on chpoc-1 | OK | OK (OK) | OK | 0.6s (1x) | 0s (no errors) | 28s | 0 | **PASS** |
| 2026-09-28 04:32 | 2 | kill -9 ClickHouse on the serving node | clickhouse-server SIGKILLed on chpoc-2 (the node serving traffic) | OK | OK (OK) | OK | 0.9s (1x) | 0s (no errors) | 3s | 0 | **PASS** |
| 2026-09-28 04:33 | 3 | Stop the Keeper leader | Keeper leader chpoc-3 stopped; new leader: chpoc-1 | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 3s | 0 | **PASS** |
| 2026-09-28 04:35 | 4 | Keeper quorum loss (2 of 3 Keepers down) | Keeper stopped on chpoc-1 and chpoc-2 (1 of 3 left: no quorum) | OK | FAIL (FAIL) | OK | 83.1s (1x) | 0s (no errors) | 6s | 0 | **FAIL** |
| 2026-09-28 04:36 | 5 | Stop HAProxy on the VIP owner | HAProxy stopped on VIP owner chpoc-1; VIP moved to chpoc-2 | OK | OK (OK) | OK | 2.8s (1x) | 3.2s (1x) | 6s | 0 | **PASS** |
| 2026-09-28 04:38 | 6 | Hard reset of a whole VM | chpoc-2 hard reset (power-cut style) | OK | OK (OK) | OK | 27.2s (1x) | 7.8s (1x) | 3s | 0 | **PASS** |
| 2026-09-28 04:39 | 7 | Network partition of one node | chpoc-3 network-partitioned from its peers | OK | OK (OK) | OK | 31.6s (1x) | 0s (no errors) | 21s | 0 | **PASS** |
| 2026-09-28 04:42 | 8 | ClickHouse down on 2 of 3 nodes | clickhouse-server stopped on chpoc-1 and chpoc-2; only chpoc-3 left | OK | FAIL (FAIL) | OK | 69.4s (3x) | 0s (no errors) | 3s | 0 | **PASS** |
| 2026-09-28 04:46 | 4 | Keeper quorum loss (2 of 3 Keepers down) | Keeper stopped on chpoc-1 and chpoc-2 (1 of 3 left: no quorum) | OK | FAIL (FAIL) | OK | 83.0s (1x) | 0s (no errors) | 9s | 0 | **PASS** |
| 2026-09-28 05:34 | 1 | Stop ClickHouse on one node | clickhouse-server stopped on chpoc-1 | OK | OK (OK) | OK | 20.4s (1x) | 0s (no errors) | 19s | 0 | **PASS** |
| 2026-09-28 05:37 | 2 | kill -9 ClickHouse on the serving node | clickhouse-server SIGKILLed on chpoc-2 (the node serving traffic) | OK | OK (OK) | OK | 0.6s (1x) | 0s (no errors) | 0s | 0 | **PASS** |
| 2026-09-28 05:39 | 3 | Stop the Keeper leader | Keeper leader chpoc-2 stopped; new leader: chpoc-3 | OK | OK (OK) | OK | 0s (no errors) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-28 05:41 | 4 | Keeper quorum loss (2 of 3 Keepers down) | Keeper stopped on chpoc-3 and chpoc-1 (1 of 3 left: no quorum) | OK | FAIL (FAIL) | OK | 83.1s (1x) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-28 05:43 | 5 | Stop HAProxy on the VIP owner | HAProxy stopped on VIP owner chpoc-2; VIP moved to chpoc-1 | OK | OK (OK) | OK | 2.9s (1x) | 3.2s (1x) | 9s | 0 | **PASS** |
| 2026-09-28 05:45 | 6 | Hard reset of a whole VM | chpoc-2 hard reset (power-cut style) | OK | OK (OK) | OK | 27.3s (1x) | 0s (no errors) | 6s | 0 | **PASS** |
| 2026-09-28 05:46 | 7 | Network partition of one node | chpoc-2 network-partitioned from its peers | OK | OK (OK) | OK | 0s (no errors) | 7.2s (1x) | 9s | 0 | **PASS** |
| 2026-09-28 05:50 | 8 | ClickHouse down on 2 of 3 nodes | clickhouse-server stopped on chpoc-1 and chpoc-2; only chpoc-3 left | OK | FAIL (FAIL) | OK | 87.2s (3x) | 0s (no errors) | 0s | 0 | **PASS** |
