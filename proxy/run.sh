#!/usr/bin/env bash
# Start the Evercode Flush Proxy.
#
# Easiest (proxy + Claude Code in one command):   ./evercode
#   That launcher starts this proxy in daemon mode, points Claude Code at it,
#   and sets EVERCODE_FLUSH_PROXY=1. Most users never call run.sh directly.
#
# Run the proxy alone:
#   Foreground (blocks; for debugging):          ./proxy/run.sh
#   Daemon (background; returns immediately):    EVERCODE_PROXY_DAEMON=1 ./proxy/run.sh
#   With an explicit upstream (e.g. cc-switch):  EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh
#   Restart (kill old on :5589 first):           EVERCODE_PROXY_RESTART=1 ./proxy/run.sh
#
# Then point Claude Code at it BEFORE launching it (the ./evercode launcher
# does this for you):
#     export ANTHROPIC_BASE_URL=http://127.0.0.1:5589
#     export EVERCODE_FLUSH_PROXY=1     # tells the skill to emit sentinels
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${EVERCODE_UPSTREAM:=}"
: "${EVERCODE_PROXY_PORT:=5589}"

if [ -z "$EVERCODE_UPSTREAM" ]; then
  # Fall back to whatever ANTHROPIC_BASE_URL currently is, so we sit in front of
  # the existing chain (e.g. cc-switch on :15721) instead of replacing it.
  CURRENT="${ANTHROPIC_BASE_URL:-}"
  if [ -n "$CURRENT" ] && ! [[ "$CURRENT" =~ :${EVERCODE_PROXY_PORT}$ ]]; then
    export EVERCODE_UPSTREAM="$CURRENT"
  else
    export EVERCODE_UPSTREAM="https://api.anthropic.com"
  fi
fi

# EVERCODE_PROXY_RESTART=1: stop any proxy already on :PORT before starting, so
# the new process loads fresh code (the ./evercode launcher exposes this as
# --restart-proxy). Useful after a plugin upgrade; idempotent.
if [ "${EVERCODE_PROXY_RESTART:-0}" = "1" ]; then
  echo "Evercode Flush Proxy: restart — stopping any proxy on :${EVERCODE_PROXY_PORT} ..."
  EVERCODE_PROXY_PORT="$EVERCODE_PROXY_PORT" "${SCRIPT_DIR}/stop.sh" >/dev/null 2>&1 || true
  sleep 0.3
fi

# Mirror server.py's default log path so stderr tracebacks (the 2>&1 below)
# land in the same file the server's FileHandler writes — one log, no dupes.
: "${EVERCODE_PROXY_LOG:=${HOME}/.claude/evercode-proxy.log}"
export EVERCODE_PROXY_LOG

echo "Starting Evercode Flush Proxy"
echo "  listen   : 127.0.0.1:${EVERCODE_PROXY_PORT}"
echo "  upstream : ${EVERCODE_UPSTREAM}"
echo "  log      : ${EVERCODE_PROXY_LOG}"
echo

if [ "${EVERCODE_PROXY_DAEMON:-0}" = "1" ]; then
  # Background the server so the caller (e.g. the ./evercode launcher) can
  # continue in the same shell. Writes a pid file for stop.sh / inspection.
  nohup python3 "${SCRIPT_DIR}/server.py" >>"${EVERCODE_PROXY_LOG}" 2>&1 &
  echo $! >"${SCRIPT_DIR}/proxy.pid"
  echo "Evercode Flush Proxy: started in background (pid $!)."
  echo "  Point Claude Code here: ANTHROPIC_BASE_URL=http://127.0.0.1:${EVERCODE_PROXY_PORT}"
  echo "  Stop with: ./proxy/stop.sh"
else
  echo "In a SEPARATE shell, before starting the evercode, set:"
  echo "  export ANTHROPIC_BASE_URL=http://127.0.0.1:${EVERCODE_PROXY_PORT}"
  echo "  export EVERCODE_FLUSH_PROXY=1"
  echo
  exec python3 "${SCRIPT_DIR}/server.py"
fi
