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

echo "Starting Evercode Flush Proxy"
echo "  listen   : 127.0.0.1:${EVERCODE_PROXY_PORT}"
echo "  upstream : ${EVERCODE_UPSTREAM}"
echo "  log      : ${SCRIPT_DIR}/proxy.log"
echo

if [ "${EVERCODE_PROXY_DAEMON:-0}" = "1" ]; then
  # Background the server so the caller (e.g. the ./evercode launcher) can
  # continue in the same shell. Writes a pid file for stop.sh / inspection.
  nohup python3 "${SCRIPT_DIR}/server.py" >>"${SCRIPT_DIR}/proxy.log" 2>&1 &
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
