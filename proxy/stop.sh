#!/usr/bin/env bash
# Stop the Evercode Flush Proxy if it is running.
#
#   ./proxy/stop.sh
#   EVERCODE_PROXY_PORT=5590 ./proxy/stop.sh
#
# Graceful by port (lsof), with a pgrep fallback on server.py. SIGTERM first,
# then SIGKILL if the port is still held a second later. Idempotent: prints
# "not running" and exits 0 if nothing matches.
set -euo pipefail

: "${EVERCODE_PROXY_PORT:=5589}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PIDS=""
if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -ti tcp:"${EVERCODE_PROXY_PORT}" 2>/dev/null || true)
fi
if [ -z "$PIDS" ] && command -v pgrep >/dev/null 2>&1; then
  PIDS=$(pgrep -f "${SCRIPT_DIR}/server.py" 2>/dev/null || true)
fi

if [ -z "$PIDS" ]; then
  echo "Evercode Flush Proxy: not running on :${EVERCODE_PROXY_PORT}."
  exit 0
fi

# shellcheck disable=SC2086  # PIDS is a newline-separated list of pids
kill $PIDS 2>/dev/null || true
sleep 1

REMAIN=""
if command -v lsof >/dev/null 2>&1; then
  REMAIN=$(lsof -ti tcp:"${EVERCODE_PROXY_PORT}" 2>/dev/null || true)
fi
if [ -n "$REMAIN" ]; then
  # shellcheck disable=SC2086
  kill -9 $REMAIN 2>/dev/null || true
  echo "Evercode Flush Proxy: force-stopped (SIGKILL) on :${EVERCODE_PROXY_PORT}."
else
  echo "Evercode Flush Proxy: stopped (SIGTERM) on :${EVERCODE_PROXY_PORT}."
fi
