#!/usr/bin/env python3
"""
Evercode Flush Proxy

A minimal transparent proxy between Claude Code and the upstream API that trims
conversation history at task boundaries — WITHOUT an LLM summarizer.

Why this exists
---------------
The evercode skill is an autonomous agent that runs for up to 8 hours across
many tasks. Its conversation history grows unbounded. The skill is *designed* to
survive compaction: every task starts with Inner 0 (Per-Task Context Refresh),
which re-reads state.json / current-decomp.md / INVARIANTS.md from disk. So a
"compaction" for evercode does not need a high-quality LLM summary — it just
needs to drop old turns and leave a pointer telling the agent to re-read disk.

That is exactly what this proxy does. No summarizer model, no background threads,
no content hashing. Pure stdlib.

How it works
------------
After each task commit (Inner 5), the skill emits a UNIQUE sentinel:

    <<EC_FLUSH:1752220800>>          # the number is `date +%s`

The harness includes that assistant/tool text in subsequent requests, so this
proxy sees it. On any /v1/messages request:

  1. Scan all message text for  <<EC_FLUSH:<id>>>  tokens.
  2. Strip every sentinel token from the forwarded body (the upstream model never
     sees them).
  3. If any sentinel id is NEW (not yet consumed): keep the last KEEP_RECENT
     messages, drop leading orphaned tool_result blocks, strip stale
     cache_control breakpoints, prepend a static pointer message, and mark that
     sentinel id consumed — so it never re-triggers even though the harness
     keeps the original turns in its own store.

The uniqueness + consumed-set is essential: a plain fixed sentinel would live in
the harness history forever and re-trim on every subsequent request, which would
cap the agent's working context at KEEP_RECENT messages and break multi-step
tasks.

Wiring (see README.md for the full recipe):
    export EVERCODE_UPSTREAM=http://127.0.0.1:15721   # your real upstream
    python3 proxy/server.py                              # listens on :5589
    export ANTHROPIC_BASE_URL=http://127.0.0.1:5589      # point Claude Code here
    export EVERCODE_FLUSH_PROXY=1                       # skill emits sentinels
"""

import json
import os
import re
import sys
import logging
import ssl
import threading
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

LISTEN_PORT = int(os.environ.get("EVERCODE_PROXY_PORT") or "5589")
KEEP_RECENT = int(os.environ.get("EVERCODE_KEEP_RECENT") or "6")
MIN_TO_TRIM = int(os.environ.get("EVERCODE_MIN_TO_TRIM") or "10")

# A sentinel looks like  <<EC_FLUSH:1752220800>>  (id = digits / word chars).
SENTINEL_RE = re.compile(r"<<EC_FLUSH:(\w+)>>")

def _default_log_path() -> str:
    """Stable, version- and repo-independent log location.

    Same path regardless of which server.py copy is running (repo checkout vs
    plugin cache), so there's always ONE log to watch and a plugin update
    doesn't orphan the history into a stale file. EVERCODE_PROXY_LOG overrides.
    """
    return os.path.join(os.path.expanduser("~"), ".claude", "evercode-proxy.log")


LOG_PATH = os.environ.get("EVERCODE_PROXY_LOG") or _default_log_path()
# Ensure the log dir exists (e.g. a custom EVERCODE_PROXY_LOG pointing elsewhere).
os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)), exist_ok=True)


def _resolve_upstream() -> str:
    """Where to forward traffic. Never route back at ourselves."""
    up = os.environ.get("EVERCODE_UPSTREAM")
    if up:
        return up.rstrip("/")
    # Fall back to ANTHROPIC_BASE_URL recorded in settings.json — but only if it
    # isn't our own port (which would loop once the user repoints it at us).
    try:
        settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
        with open(settings_path, encoding="utf-8") as f:
            env_vars = (json.load(f) or {}).get("env", {}) or {}
        base = (env_vars.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        if base and (urlparse(base).port or 0) != LISTEN_PORT:
            return base
    except Exception:
        pass
    return "https://api.anthropic.com"


UPSTREAM_URL = _resolve_upstream()
_parsed = urlparse(UPSTREAM_URL)
UPSTREAM_PATH = _parsed.path or ""
ssl_ctx = ssl.create_default_context()


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


class _FlushHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


# One formatter for every handler so the file and terminal lines match.
_log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_fh = _FlushHandler(LOG_PATH, mode="a")
_fh.setFormatter(_log_fmt)
_handlers = [_fh]
if sys.stdout.isatty():
    # Foreground/interactive: mirror to the terminal too. In daemon mode stdout
    # is redirected to the same log file by run.sh, so attaching the stream
    # handler here would write every line twice — skip it and let the FileHandler
    # be the single writer (run.sh's 2>&1 still captures stderr tracebacks).
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_log_fmt)
    _handlers.append(_sh)
logging.basicConfig(level=logging.INFO, handlers=_handlers)
log = logging.getLogger("evercode-flush")


# --------------------------------------------------------------------------- #
# Upstream connection + path joining
# --------------------------------------------------------------------------- #


def _upstream_conn():
    if _parsed.scheme == "https":
        return http.client.HTTPSConnection(
            _parsed.hostname, _parsed.port or 443, context=ssl_ctx, timeout=600
        )
    return http.client.HTTPConnection(_parsed.hostname, _parsed.port or 80, timeout=600)


def _join_path(upstream_path: str, request_path: str) -> str:
    if not upstream_path:
        return request_path
    if not request_path or request_path == "/":
        return upstream_path
    if upstream_path.endswith("/") and request_path.startswith("/"):
        return upstream_path[:-1] + request_path
    if not upstream_path.endswith("/") and not request_path.startswith("/"):
        return upstream_path + "/" + request_path
    return upstream_path + request_path


# --------------------------------------------------------------------------- #
# Header helpers
# --------------------------------------------------------------------------- #

_DROP_REQ_HEADERS = {"host", "transfer-encoding", "connection", "content-length"}


def _forward_headers(req_headers: dict, body: bytes = None, strip_encoding: bool = False) -> dict:
    headers = {}
    for k, v in req_headers.items():
        low = k.lower()
        if low in _DROP_REQ_HEADERS:
            continue
        if strip_encoding and low == "accept-encoding":
            continue
        headers[k] = v
    if body is not None:
        headers["content-length"] = str(len(body))
    return headers


# --------------------------------------------------------------------------- #
# Message helpers
# --------------------------------------------------------------------------- #


def _message_text(content) -> str:
    """Flatten all text in a message content (str or list of blocks) for scanning."""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                c = b.get("content", "")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    for sub in c:
                        if isinstance(sub, dict):
                            parts.append(sub.get("text", ""))
    return "\n".join(parts)


def _clean_message(msg: dict) -> dict:
    """Deep-copy a message with sentinel tokens removed and cache_control stripped."""
    msg = json.loads(json.dumps(msg))  # deep copy (payload is JSON-serializable)
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = SENTINEL_RE.sub("", content)
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            b.pop("cache_control", None)
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                b["text"] = SENTINEL_RE.sub("", b["text"])
            elif b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, str):
                    b["content"] = SENTINEL_RE.sub("", c)
                elif isinstance(c, list):
                    for sub in c:
                        if isinstance(sub, dict):
                            sub.pop("cache_control", None)
                            if isinstance(sub.get("text"), str):
                                sub["text"] = SENTINEL_RE.sub("", sub["text"])
    return msg


def _validate_tool_pairs(messages: list) -> list:
    """Drop leading messages until no orphaned tool_result remains.

    A tool_result is orphaned when its matching tool_use was trimmed away, so it
    now appears in the kept tail before its parent. The API rejects such pairs.
    """
    tool_use_ids = set()
    valid_from = 0
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tool_use_ids.add(b.get("id", ""))
            elif b.get("type") == "tool_result":
                if b.get("tool_use_id", "") not in tool_use_ids:
                    valid_from = i + 1
    if valid_from > 0:
        log.info(f"[TRIM] dropping {valid_from} leading message(s) with orphaned tool_result")
    return messages[valid_from:]


POINTER_TEXT = (
    "Context was trimmed at a task boundary by the evercode flush proxy. Your "
    "earlier in-conversation history was dropped — it is fully persisted on disk. "
    "Before doing anything else, run Inner 0 (Per-Task Context Refresh): re-read "
    "$RUN_DIR/state.json, $RUN_DIR/current-decomp.md, and $SKILL_DIR/INVARIANTS.md, "
    "then continue with the next pending task. Do not rely on memory of trimmed turns."
)

ACK_TEXT = (
    "Understood — context was trimmed at a task boundary. I will re-read state from "
    "disk (Inner 0) before continuing with the next task."
)


def _trim_for_flush(messages: list):
    """Return (trimmed_messages, did_trim). Caller already stripped sentinels."""
    if len(messages) <= max(KEEP_RECENT, MIN_TO_TRIM):
        return messages, False
    kept = messages[-KEEP_RECENT:]
    kept = _validate_tool_pairs(kept)
    kept = [_clean_message(m) for m in kept]
    pointer = {"role": "user", "content": POINTER_TEXT}
    # Guarantee valid alternation starting with user.
    if kept and kept[0].get("role") == "assistant":
        merged = [pointer] + kept           # user -> assistant
    else:
        ack = {"role": "assistant", "content": ACK_TEXT}
        merged = [pointer, ack] + kept      # user -> assistant -> ...
    return merged, True


# --------------------------------------------------------------------------- #
# Proxy state
# --------------------------------------------------------------------------- #

_consumed_ids = set()   # sentinel ids already acted on (one trim each)
_trim_counter = [0]     # mutable counter for /health


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # silence default stderr access log; we log explicitly

    # -- low-level helpers -------------------------------------------------- #

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _headers_dict(self) -> dict:
        return {k: v for k, v in self.headers.items()}

    def _stream_response(self, resp) -> int:
        """Forward upstream response headers + body to the client, streaming."""
        self.send_response(resp.status)
        has_content_length = False
        for k, v in resp.getheaders():
            low = k.lower()
            if low in ("connection", "transfer-encoding"):
                continue  # http.client already de-chunked on read; re-frame below
            if low == "content-length":
                has_content_length = True
            self.send_header(k, v)
        if not has_content_length:
            self.send_header("Connection", "close")
        self.end_headers()
        total = 0
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
            total += len(chunk)
        return total

    def _send_error(self, status: int, msg: str):
        body = json.dumps({"error": msg}).encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    # -- raw passthrough ---------------------------------------------------- #

    def _proxy_raw(self, method: str):
        body = self._read_body()
        headers = _forward_headers(self._headers_dict(), body if body else None)
        log.info(f"[RAW] {method} {self.path} -> {UPSTREAM_URL} ({len(body)} bytes)")
        try:
            conn = _upstream_conn()
            conn.request(method, _join_path(UPSTREAM_PATH, self.path),
                         body=body if body else None, headers=headers)
            resp = conn.getresponse()
            log.info(f"[RAW] {resp.status} {resp.reason}")
            total = self._stream_response(resp)
            log.info(f"[RAW] streamed {total:,} bytes")
            conn.close()
        except Exception as e:
            log.error(f"[RAW] upstream error: {e}", exc_info=True)
            self._send_error(502, str(e))

    # -- HTTP verbs --------------------------------------------------------- #

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self._health()
        else:
            self._proxy_raw("GET")

    def do_POST(self):
        if self.path.startswith("/v1/messages"):
            self._handle_messages()
        else:
            self._proxy_raw("POST")

    def do_PUT(self):
        self._proxy_raw("PUT")

    def do_DELETE(self):
        self._proxy_raw("DELETE")

    def do_PATCH(self):
        self._proxy_raw("PATCH")

    def do_OPTIONS(self):
        self._proxy_raw("OPTIONS")

    # -- endpoints ---------------------------------------------------------- #

    def _health(self):
        data = {
            "status": "ok",
            "listen_port": LISTEN_PORT,
            "upstream": UPSTREAM_URL,
            "keep_recent": KEEP_RECENT,
            "min_to_trim": MIN_TO_TRIM,
            "sentinel_pattern": str(SENTINEL_RE.pattern),
            "trims": _trim_counter[0],
            "consumed_sentinels": len(_consumed_ids),
        }
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_messages(self):
        raw = self._read_body()
        req_headers = self._headers_dict()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.error("[MSG] invalid JSON")
            self._send_error(400, "Invalid JSON")
            return

        messages = payload.get("messages", [])
        model = payload.get("model", "unknown")

        # Scan for sentinel ids across all message text.
        found_ids = []
        for m in messages:
            found_ids.extend(SENTINEL_RE.findall(_message_text(m.get("content", ""))))

        did_trim = False
        if found_ids:
            # Strip every sentinel token so the upstream model never sees them.
            messages = [_clean_message(m) for m in messages]
            new_ids = [i for i in found_ids if i not in _consumed_ids]
            # Consume on sight (even if we don't trim), so a sentinel that fired
            # while the conversation was small doesn't re-trigger later.
            _consumed_ids.update(found_ids)

            if new_ids:
                trimmed, did_trim = _trim_for_flush(messages)
                if did_trim:
                    messages = trimmed
                    _trim_counter[0] += 1
                    log.info(
                        f"[FLUSH] new sentinel(s) {new_ids}: trimmed "
                        f"{len(payload['messages'])} -> {len(trimmed)} messages (model={model})"
                    )
                else:
                    log.info(
                        f"[FLUSH] new sentinel(s) {new_ids} but only "
                        f"{len(messages)} messages — stripped sentinel, no trim"
                    )
            else:
                log.info(f"[MSG] {len(found_ids)} sentinel(s) already consumed — stripped, no trim")

            payload["messages"] = messages

        body = json.dumps(payload).encode()
        headers = _forward_headers(req_headers, body, strip_encoding=True)
        log.info(
            f"[MSG] POST {self.path} -> {UPSTREAM_URL} "
            f"({len(body):,} bytes, {len(payload.get('messages', []))} msgs, trim={did_trim})"
        )
        try:
            conn = _upstream_conn()
            conn.request("POST", _join_path(UPSTREAM_PATH, self.path),
                         body=body, headers=headers)
            resp = conn.getresponse()
            log.info(f"[MSG] {resp.status} {resp.reason}")
            total = self._stream_response(resp)
            log.info(f"[MSG] streamed {total:,} bytes")
            conn.close()
        except Exception as e:
            log.error(f"[MSG] upstream error: {e}", exc_info=True)
            self._send_error(502, str(e))


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #


class ThreadedHTTPServer(HTTPServer):
    """Handle each request in its own thread."""

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    log.info(f"Evercode Flush Proxy starting on 127.0.0.1:{LISTEN_PORT}")
    log.info(f"  Upstream       : {UPSTREAM_URL}")
    log.info(f"  Keep recent    : {KEEP_RECENT} messages")
    log.info(f"  Min to trim    : {MIN_TO_TRIM} messages")
    log.info(f"  Sentinel       : {SENTINEL_RE.pattern}")
    log.info(f"  Log            : {LOG_PATH}")
    if UPSTREAM_URL.rstrip("/").endswith(f":{LISTEN_PORT}"):
        log.error("  !! UPSTREAM resolves to our own port — would loop. Set EVERCODE_UPSTREAM.")

    server = ThreadedHTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
