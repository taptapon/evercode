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

```bash
# 1. Start the proxy (defaults to :5589, forwards to your current ANTHROPIC_BASE_URL).
./proxy/run.sh

# 2. In the shell you'll launch the evercode from, point Claude Code at the
#    proxy and enable sentinel emission:
export ANTHROPIC_BASE_URL=http://127.0.0.1:5589
export EVERCODE_FLUSH_PROXY=1
```

`EVERCODE_FLUSH_PROXY=1` is the gate: the skill only emits sentinels when it's
set, so users who don't run this proxy pay no cost (no stray tokens to their
model). If the proxy isn't in the path, the sentinels are inert text.

If your setup already has an upstream (e.g. `cc-switch` on `:15721`), set it
explicitly so the proxy chains correctly:

```bash
EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh
```

### Health check

```bash
curl http://127.0.0.1:5589/health
# {"status":"ok","upstream":"...","trims":3,"consumed_sentinels":3,...}
```

### Logs

`proxy/proxy.log` — one line per request; `[FLUSH]` lines mark each trim.

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
