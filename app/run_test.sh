#!/usr/bin/env bash
# Unattended end-to-end HA test:
#   starts the workload in the background, runs the failure scenarios one by
#   one, stops the workload, runs a final verification.
#
#   MODE=vip|client ./run_test.sh [scenario numbers...]    (default: all)
set -euo pipefail
cd "$(dirname "$0")"
: "${CH_APP_PASSWORD:?export CH_APP_PASSWORD first}"
PY=${PY:-python3}
MODE=${MODE:-vip}
[ $# -eq 0 ] && set -- all
mkdir -p runs
LOG="runs/app-$(date +%Y%m%d-%H%M%S).log"

"$PY" -m hatest status || { echo "Cluster is not healthy; fix it before testing."; exit 1; }

"$PY" -m hatest run --mode "$MODE" --headless >"$LOG" 2>&1 &
APP_PID=$!
trap 'kill -INT $APP_PID 2>/dev/null || true; wait $APP_PID 2>/dev/null || true' EXIT
echo "workload running (pid $APP_PID, mode $MODE), log: $LOG"
sleep 20

rc=0
"$PY" -m hatest scenario "$@" || rc=$?

kill -INT $APP_PID; wait $APP_PID || true
trap - EXIT
echo
"$PY" -m hatest verify || rc=$?
echo "Report: runs/report.md   App log: $LOG"
exit $rc
