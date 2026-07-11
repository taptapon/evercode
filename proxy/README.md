# Evercode Flush Proxy

An optional companion to the `evercode` skill. It sits on the Claude Code API
path and **trims conversation history at task boundaries** — so a multi-hour
autonomous run doesn't accumulate an unbounded transcript.

Unlike a general "rolling context" proxy, this one needs **no LLM summarizer**.
Evercode is built to recover from compaction: every task starts with
**Inner 0 (Per-Task Context Refresh)**, which re-reads `state.json`,
`current-decomp.md`, and `INVARIANTS.md` from disk. So trimming here just drops
old turns and leaves a pointer telling the agent to re-read disk. Cheaper,
deterministic, no second model in the loop.

---

## How a trim happens

1. After each task commit (Inner 5), the skill emits a **unique sentinel**:
   `<<EC_FLUSH:1752220800>>` (the number is `date +%s`).
2. The harness carries that text in later requests, so the proxy sees it.
3. The proxy: strips the sentinel, keeps the last `KEEP_RECENT` messages, drops
   orphaned `tool_result` blocks, strips stale cache breakpoints, prepends a
   pointer, and **marks that sentinel id consumed**.
4. Because the sentinel is unique and consumed once, it never re-triggers — even
   though the harness keeps the original turns in its own store.

A fixed (non-unique) sentinel would live in the harness history forever and
re-trim on every subsequent request, capping the agent's working context at
`KEEP_RECENT` messages and breaking multi-step tasks. Don't do that.

---

## Run it

**Launch order matters.** The proxy must be on the API path *before* Claude
Code starts — `ANTHROPIC_BASE_URL` is read at process launch and cannot be
changed for a running session. Starting the proxy after launch (or `export`-ing
`ANTHROPIC_BASE_URL` from inside the session) does nothing. So: start the
proxy, *then* launch Claude Code.

```bash
# 1. Start the proxy (defaults to :5589, forwards to your current ANTHROPIC_BASE_URL).
./proxy/run.sh

# 2. In the shell you'll launch Claude Code from, point it at the proxy and
#    preset the opt-in:
export ANTHROPIC_BASE_URL=http://127.0.0.1:5589
export EVERCODE_FLUSH_PROXY=1

# 3. Launch Claude Code and trigger evercode as usual.
```

### How evercode turns it on

evercode's pre-flight (§11) detects the proxy by checking `ANTHROPIC_BASE_URL`
+ the `/health` endpoint, asks whether to enable flushing, and records the
answer in `state.json.flush_proxy`. **Inner 5.5 reads that field — not the
env var — to decide whether to emit the sentinel**, so the opt-in survives
context compaction. `EVERCODE_FLUSH_PROXY=1` still works as a launch-time
preset: when pre-flight sees it set, it skips the question and records
`flush_proxy: true` directly. Users who don't run the proxy are unaffected —
sentinels are inert text when the proxy isn't on the path.

If the proxy is not on the path but you ask for it, pre-flight prints the
launch steps above and aborts the shift (it won't pretend to enable flushing
mid-session).

If your setup already has an upstream (e.g. `cc-switch` on `:15721`), set it
explicitly so the proxy chains correctly:

```bash
EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh
```

### Chaining with cc-switch (or another upstream)

No conflict by design — the proxy sits **in front of** cc-switch; they chain:

```
Claude Code → flush proxy (:5589) → cc-switch (:15721) → real API
```

- **Different ports** (5589 vs 15721) — no listener clash.
- **Orthogonal roles.** The proxy only rewrites the request *body* (strip the
  sentinel, trim history) and forwards every header — including auth — verbatim.
  cc-switch keeps doing its own routing / account-switching behind the proxy.
  Switching accounts in cc-switch mid-shift needs nothing from the proxy.
- **Verified:** a mock upstream behind the proxy receives the request with
  `x-api-key` intact and the sentinel stripped; trim works through the chain.

**Watch the upstream detection (the one real gotcha).** The proxy picks its
upstream in this order: `$EVERCODE_UPSTREAM` → `$ANTHROPIC_BASE_URL` (the shell
`run.sh` runs in) → `ANTHROPIC_BASE_URL` in `~/.claude/settings.json` →
`https://api.anthropic.com`. cc-switch does not always put its base URL in one
of those places (it may use a project-level settings file, inject env when it
launches Claude Code, etc.). If the proxy can't see cc-switch's URL at startup,
it **silently falls back to `api.anthropic.com` and bypasses cc-switch** — your
request goes straight to Anthropic, skipping whatever cc-switch was doing.

So when chaining, **always set `EVERCODE_UPSTREAM` explicitly**:

```bash
EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh
```

**Launch order:** cc-switch first, then the proxy, then Claude Code with
`ANTHROPIC_BASE_URL=http://127.0.0.1:5589`. A self-loop guard prevents the
proxy from ever using its own listen port as upstream. To run without the
proxy, just don't put it on the path — cc-switch works alone exactly as before.

### Health check

```bash
curl http://127.0.0.1:5589/health
# {"status":"ok","upstream":"...","trims":3,"consumed_sentinels":3,...}
```

### Logs

`proxy/proxy.log` — one line per request; `[FLUSH]` lines mark each trim.

### Stop it

```bash
./proxy/stop.sh
# EVERCODE_PROXY_PORT=5590 ./proxy/stop.sh   # non-default port
```

Stops by port (`lsof`), with a `pgrep -f server.py` fallback, SIGTERM first
then SIGKILL. Idempotent: prints "not running" and exits 0 if nothing matches.

The proxy is a shared, optional service and is **not** auto-stopped when an
evercode shift ends — stop it manually once you're done with all evercode work.

---

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `EVERCODE_PROXY_PORT` | `5589` | Port to listen on. |
| `EVERCODE_UPSTREAM` | current `ANTHROPIC_BASE_URL` | Where to forward traffic. Never the proxy's own port. |
| `EVERCODE_KEEP_RECENT` | `6` | Messages kept verbatim after a trim. |
| `EVERCODE_MIN_TO_TRIM` | `10` | Don't trim conversations this small or smaller. |
| `EVERCODE_PROXY_LOG` | `proxy/proxy.log` | Log file path. |
| `EVERCODE_FLUSH_PROXY` | — | Set to `1` so the **skill** emits sentinels (proxy reads nothing from this). |

---

## Tuning `KEEP_RECENT`

`6` keeps just enough for continuity (the commit that just landed, the move to
the next task) before Inner 0 re-reads full state from disk. Raise it if tasks
need more live working context; lower it to trim more aggressively. It must stay
below typical per-task message count or trims become no-ops.

---

## What it is NOT

- **Not a summarizer.** Dropped turns are not summarized — evercode's on-disk
  state is the source of truth, recovered via Inner 0. If you want LLM
  summaries, use a general rolling-context proxy instead.
- **Not a replacement for harness auto-compact.** It complements it: this proxy
  trims at clean task boundaries; the harness still auto-compacts by size if a
  single task grows large.
- **Stateless across restarts.** The consumed-sentinel set lives in memory. A
  proxy restart means the next request with any (old) sentinel trims once more —
  harmless, then normal.
