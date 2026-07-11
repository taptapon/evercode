# CLAUDE.md

Contributor notes for working in the `evercode` repo. (User-facing docs live in
[README.md](README.md); the execution spec the agent follows is
[SKILL.md](SKILL.md).)

## What this is

A Claude Code **plugin/skill** that turns a session into an autonomous, around-the-clock
coding agent. The agent follows `SKILL.md` as a literal execution spec — it is
not a library or an app you run directly. There is no build step.

## Repo layout

| Path | Role |
|---|---|
| `SKILL.md` | The execution spec the agent runs verbatim (~1600 lines). Outer loop = key results; Inner loop = tasks (steps 0–6). |
| `INVARIANTS.md` | Non-negotiable rules; re-read by **Inner 0** before every task. |
| `skills/evercode/` | Install target. `SKILL.md` and `INVARIANTS.md` are **symlinked** here → `../../`. **Edit the root files**, not the symlinks. |
| `.claude-plugin/plugin.json` | Marketplace manifest (name, version, author). |
| `proxy/` | **Optional** companion: flush proxy that trims context at task boundaries. See `proxy/README.md`. |

## Mental model (read before touching SKILL.md)

- **Disk is the source of truth, never memory.** Every verdict — Codex review,
  decomposition approval, run state — is a file under
  `.evercode/runs/<RUN_ID>/`. After hours of work and multiple compactions
  the agent's memory is wrong; the files are right.
- **The skill is built to *survive* compaction.** Inner 0 re-reads
  `INVARIANTS.md` + `state.json` + `current-decomp.md` before each task. Any new
  state MUST be persisted to `state.json`, not held in the conversation.
- **Inner loop:** `0 refresh → 1 plan → 2 execute → 3 Codex review (file-gated)
  → 4 validate → 5 commit → 5.5 flush sentinel (optional) → 6 next task`.
- **Gates exist because a compacted, forgetful agent will try to skip them.**
  The Inner 5 pre-commit file gate (verifies `code-review.txt` exists, is
  non-empty, and matches the verdict in state) is the load-bearing defense
  against skipping Codex. Don't weaken it.

## Editing SKILL.md / INVARIANTS.md

- Be precise and literal — an autonomous agent under compaction follows this
  text. Ambiguity becomes a silent skip.
- The ASCII inner-loop diagram (~line 600) lists the **core** steps 0–6.
  Optional add-ons (like 5.5) live as their own subsections between the
  numbered steps, **not** inside the aligned box.
- Markdown inside bash fences uses placeholders like `<G>`/`<S>` — these are
  template tokens the agent substitutes, not literal shell. Preserve them.

## The flush proxy (`proxy/`)

Optional companion. Pre-flight §11 detects it (`ANTHROPIC_BASE_URL` + `/health`),
asks the user, and records the opt-in in `state.json.flush_proxy`. Inner 5.5
reads that field — not the `EVERCODE_FLUSH_PROXY` env var — to emit the unique
sentinel `<<EC_FLUSH:<timestamp>>>`; the proxy trims history at that boundary
and prepends a pointer to re-read disk (no LLM summarizer — it leans on Inner 0).
The proxy must be on the API path *before* Claude Code launches (`ANTHROPIC_BASE_URL`
is fixed at launch); the skill detects+records but cannot hot-plug it mid-session.
The repo-root `./evercode` launcher handles the ordering for users: it starts the
proxy daemonized (reusing one already up), sets the env, and `exec claude`. For
contributor/debug use, `run.sh` also runs foreground by default and daemonized
under `EVERCODE_PROXY_DAEMON=1`.

```bash
# quick smoke check the module loads + core logic is intact
python3 -c "import importlib.util as u; s=u.spec_from_file_location('n','proxy/server.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('loaded; KEEP_RECENT=',m.KEEP_RECENT)"

# one-command launch (starts proxy + Claude Code):  ./evercode
# run the proxy alone — foreground:    ./proxy/run.sh
#                        daemon:        EVERCODE_PROXY_DAEMON=1 ./proxy/run.sh
# both then need:  export ANTHROPIC_BASE_URL=http://127.0.0.1:5589 EVERCODE_FLUSH_PROXY=1
curl http://127.0.0.1:5589/health

# stop it (shared service — NOT auto-stopped at shift end)
./proxy/stop.sh
```

**Chaining with cc-switch:** no conflict — the proxy sits in front
(proxy `:5589` → cc-switch `:15721` → real API). It only rewrites the body and
forwards auth verbatim, so cc-switch's routing/account-switching still works.
Gotcha: the proxy auto-detects upstream from `$EVERCODE_UPSTREAM` →
`$ANTHROPIC_BASE_URL` → `~/.claude/settings.json` → `api.anthropic.com`; if it
can't see cc-switch's URL it **silently bypasses cc-switch**. When chaining,
set it explicitly: `EVERCODE_UPSTREAM=http://127.0.0.1:15721 ./proxy/run.sh`.

If you change the sentinel format in `proxy/server.py`, update the `SENTINEL_RE`
**and** the emit line in SKILL.md Inner 5.5 together — they must match. The
sentinel must stay **unique per task** (the timestamp); a fixed sentinel would
re-trigger on every later request because the harness keeps old turns.

## Versioning

Version lives in **`.claude-plugin/plugin.json`** only (`"version"`). SKILL.md
§1 reads it dynamically for the auto-update check — there is no hardcoded
version in SKILL.md. Bump the one field, nothing else.

## Git

The skill never runs `git push` and never opens PRs during a shift — but as a
contributor you commit normally. Keep commits small and reversible (the whole
project is about small reversible units).
