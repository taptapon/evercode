#!/usr/bin/env bash
# Start the Evercode Flush Proxy.
#
# Quick start (defaults):   ./proxy/run.sh
# With options:             EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh
#
# Then point Claude Code at it BEFORE launching a evercode:
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
echo "In a SEPARATE shell, before starting the evercode, set:"
echo "  export ANTHROPIC_BASE_URL=http://127.0.0.1:${EVERCODE_PROXY_PORT}"
echo "  export EVERCODE_FLUSH_PROXY=1"
echo

exec python3 "${SCRIPT_DIR}/server.py"
