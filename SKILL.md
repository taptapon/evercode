---
name: evercode
description: >
  Autonomous agent that runs while the human is away. Trigger with "/evercode"
  or phrases like "start evercode", "keep coding", "take over",
  "keep working while I'm away". The skill routes based on context: if no shift
  is active, it starts one; if a shift is already active, it prompts Stop / Resume
  / Abandon. Stop phrases like "stop evercode" or "end evercode" run the
  end procedure directly. The user approves ONE thing — an objective. Key Results
  and their tasks are decided iteratively during execution, each gated by a
  Codex review. The shift ends either when Codex and the agent agree further
  work would be over-engineering, or at an 8-hour hard cap. Each shift is an
  independent run with its own folder under `.evercode/runs/<RUN_ID>/`;
  previous runs are kept as history. Use this any time the user wants unattended
  autonomous work or long-running sessions.
---
# Evercode

You are entering autonomous mode. The human is stepping away — into a meeting, taking a
break, or otherwise unavailable. Your job is to make meaningful progress on their
codebase without asking any questions after the initial goal confirmation.

This skill has a **single unified entrypoint**. On invocation, route based on
(a) whether an evercode run is active and (b) the user's phrasing.

**Vocabulary:** **objective** (direction the user sets), **key result** (a concrete shippable step advancing it, gated by Codex), **task** (an independently committable unit under a KR). KRs are deliverable-shaped, not metric-shaped.

## Routing

First, detect whether any active shift exists:

```bash
# Active = any .evercode/runs/*/state.json with status == "running".
# No shell variables named `status` — zsh makes `$status` read-only.
for f in .evercode/runs/*/state.json; do
  [ -e "$f" ] || continue
  [ "$(jq -r .status "$f" 2>/dev/null)" = "running" ] && echo "$f"
done
```

If there are multiple active runs (shouldn't happen normally), pick the most
recent by `started_at` and warn the user.

> **Shell note:** the `Bash` tool may run under zsh, where `$status` is a
> read-only built-in. Never use `status` as a shell variable name in any
> snippet — use `run_status`, `st`, or run the logic inside `sh -c '...'`
> (as above) so the assignment executes in sh, not zsh.

Then route:


| Phrasing                                                                      | Active shift? | Action                                                    |
| ----------------------------------------------------------------------------- | ------------- | --------------------------------------------------------- |
| start/go trigger (e.g. "start evercode", "/evercode", "keep coding") | No            | Begin fresh shift → §Pre-flight                           |
| start/go trigger                                                              | Yes           | Prompt **Stop / Resume / Abandon** → §Stop-Resume-Abandon |
| stop trigger (e.g. "stop evercode", "end evercode", "wrap up")          | Yes           | Run end procedure → §Ending a Shift                       |
| stop trigger                                                                  | No            | Reply: "No active evercode run to stop." Stop.             |


## Pre-flight (new shift)

Run these checks in order. Failures stop the shift before any work begins.

**Ask pre-flight questions ONE AT A TIME.** Do not batch confirmations into a
single message. For each check that requires user input (flush proxy opt-in,
bypass-permissions confirmation, non-git mode confirmation, branch creation on
main/master, uncommitted changes, objective, goal approval), send a
single question, wait for the reply, then proceed to the next check. This keeps
the pre-flight conversational and avoids overwhelming the user with a wall of
decisions.

### 1. Flush proxy (optional)

The flush proxy trims conversation history at task boundaries so a long run
doesn't accumulate an unbounded transcript. It is **optional** and must sit on
the Claude Code API path — which is fixed at process launch, so this step only
**detects + asks + records**; it cannot hot-plug the proxy into a running
session.

This is the **first** pre-flight check because it is the only one that can
abort and relaunch Claude Code (to put the proxy on the API path). If the user
opts in, every later check — version update, branch setup, objective — is
redone after relaunch, so ask before investing in them. It also resolves
`$SKILL_DIR` up front (needed for the relaunch command below, and reused by
§2's version check, `state.json.skill_dir` in §10, and `INVARIANTS.md` in §10).

```bash
# Resolve SKILL_DIR. Plugin install first (authoritative), then user-level clone.
SKILL_DIR=""
INSTALLED_JSON="$HOME/.claude/plugins/installed_plugins.json"
PLUGIN_KEY=""
if [ -f "$INSTALLED_JSON" ] && command -v jq >/dev/null 2>&1; then
  PLUGIN_KEY=$(jq -r '
    .plugins // {} | to_entries[]
    | select(.key | startswith("evercode@"))
    | .key
  ' "$INSTALLED_JSON" 2>/dev/null | head -1)
  if [ -n "$PLUGIN_KEY" ]; then
    SKILL_DIR=$(jq -r --arg k "$PLUGIN_KEY" '
      .plugins[$k][].installPath // empty
    ' "$INSTALLED_JSON" 2>/dev/null | sort -V | tail -1)
    # /plugin update appends new versions without pruning; installPath ends in its version dir, so sort -V | tail -1 picks the highest.
  fi
fi
if [ -z "$SKILL_DIR" ] || [ ! -f "$SKILL_DIR/.claude-plugin/plugin.json" ]; then
  if [ -f "$HOME/.claude/skills/evercode/.claude-plugin/plugin.json" ]; then
    SKILL_DIR="$HOME/.claude/skills/evercode"
    PLUGIN_KEY=""   # clone install — no marketplace key
  else
    SKILL_DIR=""    # could not resolve — relaunch cmd falls back; auto-update skipped
  fi
fi
echo "SKILL_DIR=$SKILL_DIR"

# Detect whether the proxy is on the path and healthy.
PORT="${EVERCODE_PROXY_PORT:-5589}"
PROXY_ON_PATH=0
case "${ANTHROPIC_BASE_URL:-}" in *":${PORT}") PROXY_ON_PATH=1 ;; esac
HEALTH_OK=0
[ "$PROXY_ON_PATH" = "1" ] && \
  curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && HEALTH_OK=1

# Absolute-path relaunch cmd — cwd is the user's project dir, so ./evercode won't resolve; use $SKILL_DIR (resolved above).
LAUNCHER="$SKILL_DIR/evercode"
if [ -x "$LAUNCHER" ]; then
  RELAUNCH_CMD="cd \"$PWD\" && \"$LAUNCHER\" --dangerously-skip-permissions"
else
  RELAUNCH_CMD="cd \"$PWD\" && ANTHROPIC_BASE_URL=http://127.0.0.1:${PORT} EVERCODE_FLUSH_PROXY=1 claude --dangerously-skip-permissions   # start \"$SKILL_DIR/proxy/run.sh\" first"
fi
```

If the probe failed (neither install location matched), continue — the relaunch
command falls back to the manual form, and the shift can still run as long as
`INVARIANTS.md` is reachable via the fallback in §10.

Then branch — **ask at most one question**, per the one-at-a-time pre-flight rule:

- **`EVERCODE_FLUSH_PROXY=1` already set** (user opted in at launch) → skip the
  question. Record `flush_proxy: true` and print one line: "flush proxy:
  opt-in detected via env, sentinels enabled."
- **`HEALTH_OK=1`** (proxy on path and healthy) → ask:
  ```
  Flush proxy detected on your API path (:PORT, healthy). It trims conversation
  history at each task boundary to keep long runs lean. Emit per-task flush
  sentinels? (recommended for long runs) (yes / no)
  ```
  yes → `flush_proxy: true`; no → `flush_proxy: false`.
- **Not detected** → echo `$RELAUNCH_CMD` (a ready-to-run command resolved to
  absolute paths and your current project dir), then ask:
  ```
  Flush proxy not detected on your API path. It trims conversation history at
  task boundaries to keep long runs lean, but it must sit on the API path —
  which is fixed at Claude Code launch, so it can't be hot-plugged into this
  session. Exit Claude Code, paste the command above into your shell, then
  re-run /evercode. Enable now? (yes / no)
  ```
  yes → echo `$RELAUNCH_CMD` once more and **abort this shift** (do not
  silently proceed without flushing). no → `flush_proxy: false`, continue.

Hold the decision; it is written to `state.json.flush_proxy` (boolean) when
§10 creates the run's `state.json`. That field — not the env var — is what
Inner 5.5 reads to decide whether to emit sentinels, so the opt-in survives
compaction and works regardless of Bash-env propagation.

### 2. Skill version check + auto-update

With `$SKILL_DIR` resolved in §1, check whether a newer version of the skill
exists upstream and pull it in if so. The network check is **throttled to at
most once per 2 hours** (a timestamp file in `~/.claude/`), so a repeat start
within that window skips the curl entirely — delete
`~/.claude/evercode_version_check.txt` to force a check. (Each Bash call is a
fresh subprocess, so set `SKILL_DIR` to the value printed in §1 before running
the block below — pre-flight carries it in context the same way §10 fills in
`skill_dir`.)

Updates apply to the **next** `/evercode` invocation — Claude has already
loaded the current SKILL.md into context, so hot-swapping the running shift
is not possible. That is fine: the shift in progress completes on the loaded
version, the next one picks up the update.

```bash
# Throttle: hit the network at most once per 2h (timestamp file survives shifts),
# so a repeat start within that window skips the 5s curl. Delete the file to force.
CHECK_FILE="$HOME/.claude/evercode_version_check.txt"
NOW=$(date +%s)
LAST=$(cat "$CHECK_FILE" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
RUN_CHECK=1
[ $((NOW - LAST)) -lt 7200 ] && RUN_CHECK=0

if [ "$RUN_CHECK" = "1" ] && [ -n "$SKILL_DIR" ]; then
  # Compare local vs upstream (sed parses version; works before §4 installs jq).
  LOCAL_PJ="$SKILL_DIR/.claude-plugin/plugin.json"
  LOCAL_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOCAL_PJ" | head -1)
  REMOTE_VER=$(curl -fsS --max-time 5 \
    "https://raw.githubusercontent.com/taptapon/evercode/main/.claude-plugin/plugin.json" \
    2>/dev/null | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

  if [ -n "$LOCAL_VER" ] && [ -n "$REMOTE_VER" ] && [ "$LOCAL_VER" != "$REMOTE_VER" ] \
     && [ "$(printf '%s\n%s\n' "$LOCAL_VER" "$REMOTE_VER" | sort -V | tail -1)" = "$REMOTE_VER" ]; then
    if git -C "$SKILL_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      git -C "$SKILL_DIR" pull --ff-only --quiet 2>/dev/null \
        && echo "evercode: updated $LOCAL_VER → $REMOTE_VER (git clone). Active on next /evercode run."
    else
      TARGET="${PLUGIN_KEY:-evercode}"
      claude plugin update "$TARGET" >/dev/null 2>&1 \
        && echo "evercode: updated $LOCAL_VER → $REMOTE_VER (plugin). Active on next /evercode run."
    fi
  fi
  echo "$NOW" > "$CHECK_FILE"   # mark checked even on failure/parity — don't retry every start
fi
```

Network failures, HTTP errors, and missing `claude` CLI are all swallowed
silently. This step must never block a shift from starting.

**Codex dual-review is opt-in** (default: Claude self-reviews). Resolve the flag once here; §10 writes it to `state.json.codex_on`, and every Codex gate keys off it:

```bash
CODEX_ON=$([ "${EVERCODE_CODEX:-0}" = "1" ] && echo true || echo false)
[ "$CODEX_ON" = "true" ] && echo "evercode: Codex dual-review ENABLED (EVERCODE_CODEX=1)."
```

### 3. Bypass-permissions confirmation

Evercode is fully autonomous — it runs many tool calls and edits without human
input. If Claude Code is NOT launched with `--dangerously-skip-permissions`, the
loop will stall on permission prompts while the user is away.

There is **no reliable way** for the agent to introspect the current permission
mode from inside a session. Two paths:

- **`EVERCODE_BYPASS=1`** (set by the `./evercode` launcher when it forwards
  `--dangerously-skip-permissions`) → skip the question. Print one line:
  "bypass-permissions: opted in via launcher (EVERCODE_BYPASS=1)." and continue.
- **Otherwise** → ask, as the first pre-flight question:
  ```
  Before I start: is this session running in bypass-permissions mode
  (i.e., you launched Claude Code with `--dangerously-skip-permissions`)?

  Without it, the autonomous loop will stall on permission prompts while you're away.

  Reply "yes" to proceed, or "no" to abort — in which case, relaunch Claude Code
  with the flag and trigger evercode again.
  ```
  If the user says no → abort with the above instructions. If yes → continue.

### 4. `jq` dependency

Every subsequent step reads/writes `state.json` with `jq`. If it is not on
PATH, install it via the platform's package manager before proceeding:

```bash
if ! command -v jq >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install jq
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y jq
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y jq
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y jq
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm jq
  else
    echo "Could not auto-install jq. Please install it and retry." >&2
    exit 1
  fi
fi
```

On Linux this may prompt for a sudo password. Pre-flight is interactive (the
user is still present), so a password prompt is acceptable here — it would
not be acceptable once the autonomous loop starts. After installing, re-check
`command -v jq` and abort with a clear message if it still isn't available.

### 5. Git repo detection

One probe collects every git fact §6–§8 need, so they don't each spawn their
own Bash call (this is what keeps the pre-flight preamble short):

```bash
IS_GIT=0; git rev-parse --is-inside-work-tree >/dev/null 2>&1 && IS_GIT=1
BRANCH=""; BASE_COMMIT=""; GITIGNORE_HAS=0
if [ "$IS_GIT" = "1" ]; then
  BRANCH=$(git branch --show-current 2>/dev/null)
  BASE_COMMIT=$(git rev-parse HEAD 2>/dev/null)
  grep -qxF '.evercode/' .gitignore 2>/dev/null && GITIGNORE_HAS=1
fi
echo "IS_GIT=$IS_GIT BRANCH=$BRANCH BASE_COMMIT=$BASE_COMMIT GITIGNORE_HAS=$GITIGNORE_HAS"
```

- **`IS_GIT=0` → graceful-degrade mode.** See §Non-Git Degrade Mode for the
  limitations. Warn the user once and ask for confirmation before proceeding:
  ```
  This directory is not a git repo. Evercode will run in degrade mode:
  no commits, no rollback, no drift protection. Failed goals may leave partial
  changes in your working tree. Proceed?
  ```
  If yes → continue in degrade mode (skip §6–§8). If no → abort.
- **`IS_GIT=1` → full mode.** All commit-per-goal, drift, rollback,
  handoff-commit machinery runs. `BRANCH` and `BASE_COMMIT` are carried
  forward to §10's `state.json`. Continue to §6.

### 6. Gitignore entry (git mode only)

Ensure `.evercode/` is ignored. If the §5 probe found `GITIGNORE_HAS=0`,
append `.evercode/` on its own line (creating `.gitignore` if missing). This
is a one-line diff the user will see alongside their other changes — expected
and explicit. If `GITIGNORE_HAS=1`, skip — nothing to do.

```bash
if [ "$GITIGNORE_HAS" = "0" ]; then
  printf '\n.evercode/\n' >> .gitignore
fi
```

Do NOT use `.git/info/exclude` — we prefer the tracked `.gitignore` for
collaborator consistency and clarity.

### 7. Branch check (git mode only)

`BRANCH` came from the §5 probe. Must not be `main` or `master`.

- If on `main` or `master`: **propose creating a new branch** rather than
waiting for the user. Suggest a descriptive name based on session context
(e.g., `evercode/YYYY-MM-DD` or `feat/<topic>` inferred from the
conversation). Ask for confirmation or a different name. On confirmation,
run `git checkout -b <name>` and re-record `BRANCH` and `BASE_COMMIT`.
- On any other branch: proceed on it.

### 8. Clean working tree (git mode only)

Re-check the working tree now — after §6 may have added the `.gitignore`
line, which is the only change evercode makes here and the only dirty file
that's expected.

```bash
DIRTY=$(git status --short 2>/dev/null)
OTHER=$(printf '%s\n' "$DIRTY" | grep -vE '\.gitignore$')   # drop the expected .gitignore change
if [ -n "$OTHER" ]; then
  echo "CLEAN_TREE=no     # pre-existing uncommitted changes — ask the user"
else
  echo "CLEAN_TREE=yes"
  [ "$GITIGNORE_HAS" = "0" ] && git add .gitignore && git commit -q -m "chore: ignore .evercode/" >/dev/null 2>&1
fi
```

- **`CLEAN_TREE=no`** → there are pre-existing uncommitted changes. Ask the
user to commit or stash them. This is the only other permitted question
besides goal confirmation.
- **`CLEAN_TREE=yes`** → the tree was clean (the `.gitignore` line aside,
which the block auto-committed). Proceed.

### 9. Active-shift guard

This should have been handled in §Routing, but guard against races: re-scan
`.evercode/runs/*/state.json` and verify no `status: "running"` entry exists.
If one appeared between routing and here, loop back to §Stop-Resume-Abandon.

### 10. Initialize this run

**All timestamps in this skill are LOCAL time, not UTC.** Run folders and
handoffs are read by the human on their wall clock; UTC makes "when did this
run start" mental math harder. Use `date` (no `-u`) everywhere. For
machine-readable timestamps in state.json, include the local offset — never
append `Z`.

```bash
RUN_ID=$(date +%Y-%m-%d-%H%M)                              # local time
RUN_DIR=".evercode/runs/${RUN_ID}"
mkdir -p "$RUN_DIR/key results"
```

Write initial state to `$RUN_DIR/state.json`. Note `started_at` is **null** at
this point — pre-flight (bypass-permissions Q, branch Q, objective Q, etc.)
is interactive, not autonomous work. The shift clock starts at the handoff
banner (see §Objective Confirmation), so elapsed-time and the 8h hard cap
measure only the autonomous portion.

The `skill_dir` field below holds the `$SKILL_DIR` resolved in §1; substitute
the real path when writing the file.

```json
{
  "run_id": "2026-04-19-0847",
  "status": "running",
  "started_at": null,
  "mode": "git",
  "cwd": "/abs/path/to/project",
  "skill_dir": "<$SKILL_DIR resolved in §1>",
  "branch": "feat/xyz",
  "base_commit": "abc1234",
  "expected_head": "abc1234",
  "objective": null,
  "hard_cap_hours": 8,
  "average_task_duration_minutes": null,
  "end_consensus_file": null,
  "key_results": [],
  "test_results": null,
  "flush_proxy": false,
  "codex_on": "<$CODEX_ON from §2>"
}
```

(In degrade mode: `"mode": "no-git"`, omit `branch`, `base_commit`, `expected_head`.)

Finally, verify `INVARIANTS.md` is reachable at `$SKILL_DIR/INVARIANTS.md`. The
non-negotiable rules live there — a sibling of this SKILL.md in the skill's
install dir — and the agent reads it directly (never copied into the run
folder) on every per-task refresh (§Inner 0), so the run cannot proceed
without it. This same check covers the `skill_dir` fallback if §1's probe
failed.

```bash
test -f "$SKILL_DIR/INVARIANTS.md" || {
  # Last-ditch fallback: §1's probe failed AND state.json's skill_dir is bogus.
  if [ -f "$HOME/.claude/skills/evercode/INVARIANTS.md" ]; then
    SKILL_DIR="$HOME/.claude/skills/evercode"
    jq --arg sd "$SKILL_DIR" '.skill_dir = $sd' "$RUN_DIR/state.json" \
      > "$RUN_DIR/state.json.tmp" && mv "$RUN_DIR/state.json.tmp" "$RUN_DIR/state.json"
  else
    echo "FATAL: cannot locate INVARIANTS.md — aborting shift." >&2
    exit 1
  fi
}
```

## Objective Question

Before scanning anything, ask the user one question:

```
Do you have a high-level objective — something like
"improve the checkout flow" or "harden error handling in the API layer" —
or should I propose goals based on session context?

Reply with a goal, or "propose" to let me choose.
```

- **If the user gives an objective:** record it in `state.json.objective`.
Reconnaissance is scoped toward that goal — session history, specs, and
code related to the objective area are the priority.
- **If "propose":** leave `objective: null` and run full reconnaissance
across all sources.

This question is cheap and dramatically improves goal quality — it prevents
the skill from proposing low-value micro-goals when the user has a clear
larger direction in mind.

## Reconnaissance (2–3 minutes)

Scan these sources in priority order. Conversation history is the most
important signal — it tells you what the user actually cares about right now.

1. **Chat/session history (PRIMARY)** — read the full conversation; extract unfinished tasks, stated intentions, open threads (scoped to the objective if given).
2. **Unfinished changes** — git state: recent commits, uncommitted work (skip in degrade mode).
3. **Unimplemented specs** — `docs/` specs discussed but not implemented.
4. **Codebase TODOs** — `grep -r "TODO\|FIXME\|HACK\|XXX"`.
5. **CLAUDE.md / project docs** — conventions and architecture.

## Objective Confirmation (the only approval gate)

The user approves only ONE thing: the objective. Key Results and tasks are
decided iteratively during execution, each with its own Codex review.
Reviewing a long goal list at the start is a tax the user should not have to
pay.

**If the user gave an objective in the objective question:** echo it back
verbatim and ask for confirmation (single yes/no). Example:

```
Objective: "Harden error handling in the API layer"

I'll decide key results iteratively as I go, with Codex reviewing each one.
The shift ends when Codex and I agree further work would be over-engineering,
or after 8 hours — whichever comes first.

Confirm? (yes / edit)
```

**If the user asked the skill to "propose":** use reconnaissance to propose a
single objective and ask for confirmation. Example:

```
Based on session context, I propose this objective:

  "Finish the paper-trading dashboard and make it user-friendly."

I'll decide key results iteratively as I go. Shift ends on Codex+me consensus
or 8 hours, whichever comes first.

Confirm, or give me a different objective?
```

Once confirmed, the objective is locked for the run. Record it in
`state.objective`. No further user approval is needed until the handoff.

Once confirmed, emit a **visible handover banner** as its own message. This is
the marker between "interactive setup" and "autonomous execution". The user
should see it, understand they can walk away, and close the laptop if they
want. Use emojis and dividers so it stands out from ordinary output:

```
═══════════════════════════════════════════════════════════════
  ⚙️  EVERCODE ENGAGED  ⚙️
═══════════════════════════════════════════════════════════════

  You can step away now. I'll take it from here.

  Run ID:      RUN_ID
  Branch:      BRANCH (from BASE_COMMIT)          [git mode only]
  Objective:  <one-line summary>
  Max runtime: 8 hours (or until Codex and I agree we're done)
  Handoff:     .evercode/runs/RUN_ID/handoff.md

  Say "end evercode" any time to stop early.
  Type /remote-control to monitor from your phone or browser.

  Evercode keeps coding. ⚙️
═══════════════════════════════════════════════════════════════
```

This banner is the ONLY place where emojis / ASCII decoration are expected —
it's intentional, to give the user a clear visual handoff. After this
message, the agent enters autonomous mode. No more questions until handoff.

**Immediately after emitting the banner, set `started_at` in state.json.**
This is the shift's true start — all elapsed-time computations and the 8-hour
hard cap measure from here, not from pre-flight:

```bash
STARTED_AT=$(date +%Y-%m-%dT%H:%M:%S%z)
jq --arg t "$STARTED_AT" '.started_at = $t' "$RUN_DIR/state.json" \
  > "$RUN_DIR/state.json.tmp" && mv "$RUN_DIR/state.json.tmp" "$RUN_DIR/state.json"
```

## Run State Persistence

The shift may run for hours across many goals. To survive context pressure and
produce accurate handoffs, persist run state to `$RUN_DIR/state.json` after
every phase transition. **This file is the source of truth — not your memory.**

```json
{
  "run_id": "2026-04-19-0847",
  "status": "running",
  "mode": "git",
  "started_at": "2026-04-19T08:47:00-0700",
  "cwd": "/abs/path",
  "branch": "feat/xyz",
  "base_commit": "abc1234",
  "expected_head": "def5678",
  "objective": "Harden error handling in the API layer",
  "hard_cap_hours": 8,
  "average_task_duration_minutes": 10,
  "end_consensus_file": null,
  "key_results": [
    {
      "id": 1,
      "title": "Standardize error responses across API endpoints",
      "why": "Objective needs every endpoint to return consistent error shape",
      "status": "completed",
      "codex_approval_file": "key-results/1/codex-approval.txt",
      "decomp_adversarial_file": "key-results/1/decomp-adversarial.txt",
      "started_at": "2026-04-19T08:50:00-0700",
      "ended_at": "2026-04-19T09:20:00-0700",
      "duration_minutes": 30,
      "tasks": [
        {
          "id": 1,
          "title": "Introduce ApiError type with code/message/details",
          "status": "completed",
          "commit": "def5678",
          "start_commit": "abc1234",
          "code_review_file": "key-results/1/tasks/1/code-review.txt",
          "code_review_rounds": 1,
          "code_review_status": "clean",
          "code_review_evidence": "No P1 or P2 findings.",
          "started_at": "2026-04-19T08:50:00-0700",
          "ended_at": "2026-04-19T09:02:00-0700",
          "duration_minutes": 12,
          "decisions_made": [],
          "issues_noted": []
        }
      ]
    }
  ],
  "test_results": { "passed": 23, "failed": 2, "total": 25 },
  "codex_on": false
}
```

Valid run `status` values: `running`, `completed`, `interrupted`,
`abandoned`, `drift-stopped`, `hard-capped`.

Valid key result `status` values: `proposed`, `codex-rejected`, `in_progress`,
`completed`, `blocked`, `reverted`, `reverted-on-resume`.

Valid task `status` values: `pending`, `in_progress`, `completed`,
`blocked`, `reverted`, `reverted-on-resume`.

Update state.json after:

- Any status change (key result or task)
- Each Codex review round (approval, decomp adversarial, code review)
- Start / end of each task and key result (with timestamps — used for
dynamic duration estimation)
- Each test run
- Any decision made without user input
- When the end-of-shift consensus file is written

Before starting the next task, **re-read state.json AND
`$SKILL_DIR/INVARIANTS.md`** (path stored as `skill_dir` in state.json) to
refresh context after any compaction. See §Per-Task Context Refresh.

## Drift Check (git mode only)

Since evercode works directly on the user's feature branch, the branch or
HEAD could change underneath the run (user makes a commit, rebases, switches
branches).

The state file tracks `expected_head` — the exact commit the agent expects HEAD
to be at. It is updated only when the agent itself makes a commit (Step 6).
Initially equals `BASE_COMMIT`.

Before any step that writes, commits, or rolls back:

```bash
CURRENT_BRANCH=$(git branch --show-current)
CURRENT_HEAD=$(git rev-parse HEAD)
UNEXPECTED_CHANGES=$(git status --porcelain=v1 -z | tr '\0' '\n' | grep -v '.evercode/')
```

Verify:

1. `CURRENT_BRANCH` still matches `BRANCH` from the state file
2. `CURRENT_HEAD` equals `expected_head`
3. Any dirty files in `UNEXPECTED_CHANGES` must be files the current task

  is actively working on (listed in `current-plan.md` or already known from
   this task's execution). Untouched dirty files = external modification.

**If any check fails, the run is over:**

1. Set `status: "drift-stopped"` in state.json.
2. Make NO other repo writes. Do not commit, do not write a handoff file, do

  not rollback.
3. Print a terminal-only summary of what was accomplished before drift.
4. Stop. The human must investigate.

Run the drift check before:

- Inner 2 (Execute — before writing code)
- Inner 3 (Code Review — before first review round)
- Inner 5 (Commit — before staging)
- Any rollback operation
- Writing the handoff note (before both file write and commit)

## The Execution Loop

The loop has two levels. The **outer loop** iterates once per key result —
proposing, getting Codex approval, decomposing, and executing. The **inner
loop** runs once per task and is where the actual Claude↔Codex safety
dance happens.

```
┌──────────────────────────────────────────────────────────────────┐
│                   OUTER LOOP — per KEY RESULT                       │
│                                                                   │
│  0. Check end conditions (§End Conditions). If met → Handoff.     │
│  A. Propose next key result → write $RUN_DIR/proposed-key result.md   │
│  B. Codex key result approval review (gate)                         │
│        Approved → continue. Rejected → record, go back to A.      │
│  C. Write decomposition plan → $RUN_DIR/current-decomp.md         │
│  D. Codex adversarial-review the decomposition                    │
│  E. For each task in order → run INNER LOOP                   │
│  F. Record key result duration, update running avg, go to Outer 0.  │
│                                                                   │
│    ┌──────────────────────────────────────────────────┐           │
│    │          INNER LOOP — per TASK               │           │
│    │                                                   │           │
│    │  0. Context refresh (read INVARIANTS.md + state)  │           │
│    │  1. Write task plan to current-plan.md        │           │
│    │  2. Execute                                       │           │
│    │  3. Codex code review (loop until clean, file gate)│          │
│    │  4. Validate (tests, deliverables, UI screenshot) │           │
│    │  5. Commit (git mode) / Record (degrade mode)     │           │
│    │  6. Update state → next task                  │           │
│    └──────────────────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────────┘
```

**Key Results are NOT pre-planned.** The agent decides each one iteratively,
using what's already been shipped and what the objective still needs. Codex
gates each key result (Outer B) and the shift ends either on dual consensus or
at the 8-hour hard cap (§End Conditions).

### Outer 0: End Condition Check

Before proposing a new key result, evaluate the end conditions (§End
Conditions). If either fires, skip to Handoff. Otherwise continue to A.

### Outer A: Propose the Next Key Result

Write a proposal to `$RUN_DIR/proposed-key result.md`. Overwritten per
iteration. Include:

- Proposed key result title
- **Why:** how it serves the objective, citing completed key results if
relevant (what's been done vs what remains)
- Rough scope (a few bullets) — NOT a full decomposition yet
- Alternatives you considered, and why this one first

Base the proposal on: the objective, `state.key_results[]` (what's shipped,
what's blocked), session history, git state, and specs/docs. Every proposal
must make progress toward the objective; if nothing remains that would, go
to §End Conditions (consensus path) instead.

### Outer B: Codex Key Result Approval

**Default (`codex_on=false`):** Claude self-evaluates the KR's value — does it serve the objective, and would it over-engineer? Record the reasoning in `proposed-key-result.md`; no external review. Proceed to Outer C.

**`codex_on`:** Codex gates the KR via the review below.

Codex gates each key result. Run an adversarial review on the proposal.

**Pass `state.json` + `proposed-key-result.md` to Codex verbatim** (quote inline or reference both paths). Never paraphrase — the exact objective wording and shipped-KR shape are what Codex judges against (see §Condition 2 for the full rationale).

```bash
# $PROMPT = state.json contents (verbatim) + proposed-key-result.md, bundled
#           as a single adversarial-review prompt describing what to evaluate.
# $OUT    = $RUN_DIR/key-results/<G>/codex-approval.txt
echo "$PROMPT" | codex exec - -s read-only -c 'model_reasoning_effort="high"' 2>&1 \
  | tee "$OUT" >/dev/null
```

Invoke via the Bash tool with `timeout: 600000` (10 min). `codex exec` has a
5-minute internal default and high-effort reviews occasionally need longer.

Save the full output to `$RUN_DIR/key-results/<G>/codex-approval.txt` and
record the path in `state.key_results[G].codex_approval_file`. The review must
answer, at minimum: **is this key result worth implementing toward the
objective, or would it over-engineer / over-optimize?**

- **Approved** (no P1/P2 findings contesting the key result's value): mark
`status: "in_progress"`, record `started_at`, proceed to C.
- **Rejected** (Codex contests the key result): mark `status: "codex-rejected"`, record Codex's reasoning, loop back to A. **A revised proposal is a NEW iteration**: fresh `proposed-key-result.md`, new `state.key_results[]` entry, MUST re-pass Outer B before decomposition — never assume the fix satisfies Codex (that's self-approval). If you can't propose anything Codex accepts, invoke §Condition 2 (write `end-consensus-draft.md` citing the rejections); otherwise keep iterating or wait for the 8h cap.

If Codex is unavailable → **Invariant #14**: write a rigorous self-adversarial
review to the same file — challenging the key result's value, not rubber-stamping
it — with the header `CODEX UNAVAILABLE — SELF-REVIEW`. Self-approval without
genuine adversarial reasoning is a protocol violation.

### Outer C: Decomposition Plan

With the key result approved, write a concrete decomposition to
`$RUN_DIR/current-decomp.md`. Overwritten per key result. Include:

- Key Result title and the objective it serves
- Numbered list of **tasks**. Each task must be:
  - Independently meaningful (would make sense as its own commit)
  - Independently testable (has clear acceptance criteria)
- Dependencies between tasks if any
- Risks and open decisions

Use measured durations (`state.average_task_duration_minutes`) to size
the split — don't guess. Keep tasks in the same ballpark as the running
average so estimates remain useful.

### Outer D: Decomposition Adversarial Review

**Default (`codex_on=false`):** Claude self-reviews the task split — each task independently meaningful, testable, sized against the running average. Proceed to Outer E.

**`codex_on`:** Codex adversarial review below.

```bash
# $PROMPT = state.json (verbatim) + current-decomp.md.
# $OUT    = $RUN_DIR/key-results/<G>/decomp-adversarial.txt
echo "$PROMPT" | codex exec - -s read-only -c 'model_reasoning_effort="high"' 2>&1 \
  | tee "$OUT" >/dev/null
```

Save the full output to `$RUN_DIR/key-results/<G>/decomp-adversarial.txt` and
record the path in `state.key_results[G].decomp_adversarial_file`.

If Codex flags real issues (missing task, wrong order, task too
large, dependency cycle), revise `current-decomp.md` and re-run. Max 3
revision rounds; if still contested, record disagreement in state and
proceed.

Then copy the confirmed task list into `state.key_results[G].tasks[]`.

If Codex is unavailable → **Invariant #14** (self-adversarial review to the
same file, header `CODEX UNAVAILABLE — SELF-REVIEW`).

### Outer E: Iterate Tasks

For each task in order, run the **Inner Loop** (below). Before each
task starts: record `started_at`. When it ends: record `ended_at` and
compute `duration_seconds`.

### Outer F: Key Result Complete

When all tasks have a terminal status (`completed`, `blocked`, or
`reverted`):

- Mark `state.key_results[G].status = "completed"` (even if some tasks
were blocked — the key result is "as done as it will get").
- Record `ended_at` and compute key result `duration_seconds`.
- Update `state.average_task_duration_minutes` = running mean over all
completed tasks across all key results. This informs future
decomposition and end-condition estimates.
- `rm "$RUN_DIR/current-decomp.md"` and `rm "$RUN_DIR/proposed-key result.md"`.
- **Full skill refresh at the KR boundary.** Inner 0 only re-reads `INVARIANTS.md`; the long procedural spec drifts after multi-hour KRs (skipped artifacts, stale scaffolds). Re-read the actual files from disk — don't skim:
  ```bash
  SKILL_DIR=$(jq -r .skill_dir "$RUN_DIR/state.json")
  cat "$SKILL_DIR/SKILL.md"; cat "$SKILL_DIR/INVARIANTS.md"
  ```
- Return to **Outer 0**.

---

### Inner 0: Per-Task Context Refresh

**Before planning any task, always do these three reads:**

```bash
SKILL_DIR=$(jq -r .skill_dir "$RUN_DIR/state.json")
cat "$SKILL_DIR/INVARIANTS.md"     # Refresh non-negotiable rules
cat "$RUN_DIR/state.json"          # Where are we exactly?
cat "$RUN_DIR/current-decomp.md"   # What's the parent goal's plan?
```

This is the single most important defense against compaction — disk is truth, memory is not.

### Inner 1: Task Plan

Write a detailed implementation plan for THIS task to
`$RUN_DIR/current-plan.md`. Overwritten per task. Include:

- Task title and reference to parent goal
- Files to create or modify (specific paths)
- Approach and key design decisions
- Test strategy
- Risks or assumptions

### Inner 2: Execute

**Run the drift check** (git mode).

**Git mode:** record a snapshot for scoped rollback. **This is
mandatory for every task — including docs-only tasks, tasks you expect
to pass first try, and tasks delegated to a subagent.** A missing
`pre-files.txt` will fail the Inner 5 pre-commit gate, because scoped
rollback cannot run cleanly without it.

```bash
SUBTASK_START_COMMIT=$(git rev-parse HEAD)
{ git ls-files -z; git ls-files -z --others --exclude-standard; } \
  | sort -zu > "$RUN_DIR/key-results/<G>/tasks/<S>/pre-files.txt"
```

Save `start_commit` in the task's state entry. **Also set
`state.key_results[G].tasks[S].pre_files_recorded = true`** — the
Inner 5 gate checks this flag before staging.

**Degrade mode:** no rollback available. A failed task will leave partial
changes in the working tree.

Implement the plan. Follow existing project patterns. Write tests alongside
the implementation, not after.

Rules during execution:

- **No questions to the user.** Make a reasonable decision and record it in
the task's `decisions_made`.
- **Stay in scope of the task.** Don't silently expand into the next
task's work.
- **Write tests.**
- **Subagent delegation is allowed for Inner 2 ONLY.** Prompt MUST include:
  - "Do NOT run `git add` or `git commit`"
  - "Only create/modify files listed in the plan"
  - "Report all files changed when done"

  The evercode agent MUST then independently run Inner 3–6 itself. A
  subagent's "done" is NOT evidence of quality. The Codex review is.

  **Do NOT delegate Inner 0, 1, 3, 4, 5, or 6.**

### Inner 3: Code Review Loop (Codex) — with file gate

**Default (`codex_on=false`):** Claude self-reviews — re-read every changed file with fresh eyes, run the full test suite, and write `code-review.txt` with a header line `SELF-REVIEW (no Codex)` followed by your findings. Set `code_review_status: "self-reviewed"`. Proceed to Inner 4.

**`codex_on`:** run the Codex review loop below (until no P1/P2, or revert after 10 rounds).

**Run the drift check** before the first review round.

You MUST run the actual `codex review` CLI. Checking output yourself and
deciding "it's fine" is NOT the same as Codex saying it's clean.

```
REVIEW_ROUND      = 0
CODEX_OUTPUT_FILE = "$RUN_DIR/key-results/<G>/tasks/<S>/code-review.txt"
BASE_SHA          = <the task's start_commit from state.json>

while REVIEW_ROUND < 10:
    REVIEW_ROUND += 1
    # Invoke via the Bash tool with timeout: 600000 (10 min):
    codex review --base $BASE_SHA -c 'model_reasoning_effort="high"' 2>&1 \
      | tee $CODEX_OUTPUT_FILE
    # The pre-commit gate will verify CODEX_OUTPUT_FILE exists and contains a verdict.

    Parse the output:
    - NO P1 or P2 findings:
        → code_review_status = "clean"
        → code_review_rounds = REVIEW_ROUND
        → code_review_file = CODEX_OUTPUT_FILE
        → code_review_evidence = <the verdict line, e.g. "No P1 or P2 findings">
        → BREAK
    - P1 or P2 findings:
        → Fix each finding
        → Update state with round + findings
        → CONTINUE (re-run review on the fixes, overwriting CODEX_OUTPUT_FILE)

If REVIEW_ROUND == 10 and still P1/P2:
    → HARD STOP: revert task via scoped rollback, mark blocked
```

**Every fix MUST be re-reviewed.** The loop only exits when Codex reports no
significant issues, or after 10 failed rounds (triggers revert). No "findings
don't apply" escape — fix them or revert.

**"Clean"** = no `[P1]` or `[P2]` markers in the Codex output.

**Audit-trail option:** intermediate rounds MAY be saved as
`review-round-N.txt` alongside `code-review.txt` so the per-round Codex
output is preserved across revisions. This is encouraged for tasks that
take more than ~3 rounds — without it, the iteration history is lost
and post-mortem debugging is impossible. `code-review.txt` MUST always
be the FINAL verdict file (overwritten each round, last write wins) —
it is what the Inner 5 structural gate checks. The `review-round-N.txt`
files are audit-only and not gated.

**Does NOT count as running Inner 3:**

- "Too simple to need review"
- "Tests pass so it's fine"
- Running the review on a different task
- Writing a plausible-looking review to the file without actually running Codex

### Inner 4: Validate

Before marking the task complete:

1. Run the full test suite — all tests must pass.
2. Verify the task's deliverables are actually delivered.
3. **For UI tasks** (templates, pages, CSS, frontend components):

  verification MUST include opening the page in a browser (playwright,
   browse, or preview) and screenshotting as evidence. "Tests pass" is NOT
   sufficient for UI work.
4. Check no unintended files were modified.

**Validation failure is a hard stop.** If tests fail:

- Attempt to fix (1 attempt)
- Re-run `codex review --base $BASE_SHA -c 'model_reasoning_effort="high"' 2>&1 | tee $CODEX_OUTPUT_FILE`
on the fixes
- Re-run tests
- If tests still fail:
  - **Git mode:** scoped rollback of this task, mark `blocked`, record
  failures in `issues_noted`, move to next task.
  - **Degrade mode:** mark task `blocked`, record failures. Partial
  changes remain in the working tree — flag in handoff.

**Principle: never commit code that doesn't pass tests.** The human comes back
to a branch where every commit is green, even if fewer tasks completed.

### Inner 5: Commit (git mode) / Record (degrade mode)

**Git mode — Run the drift check first.**

**Pre-commit structural gate.** Before staging anything, verify ALL of:

1. `state.key_results[G].tasks[S].code_review_status` is one of `"clean"` (Codex),
   `"self-reviewed"` (default self-review), or `"self-reviewed-unavailable"` (Codex errored)
2. `state.key_results[G].tasks[S].code_review_file` is set AND the file
   exists AND is non-empty
3. The file contains the header matching its status: a Codex verdict line
   (`code_review_evidence` matches) when `"clean"`; a `SELF-REVIEW` line when
   `"self-reviewed"`; a `CODEX UNAVAILABLE — SELF-REVIEW` line when
   `"self-reviewed-unavailable"`. This distinguishes a real review from a
   fabricated file written to bypass the gate.
4. `state.key_results[G].tasks[S].pre_files_recorded == true` AND the file
   `pre-files.txt` exists in the task folder. Without it, scoped rollback
   cannot run cleanly if a later task needs to revert this one.

```bash
REVIEW_FILE="$RUN_DIR/key-results/<G>/tasks/<S>/code-review.txt"
PRE_FILES="$RUN_DIR/key-results/<G>/tasks/<S>/pre-files.txt"

[ -s "$REVIEW_FILE" ] || { echo "FATAL: code-review.txt missing or empty"; exit 1; }
[ -s "$PRE_FILES" ]  || { echo "FATAL: pre-files.txt missing — Inner 2 was skipped"; exit 1; }
grep -qF "$CODE_REVIEW_EVIDENCE" "$REVIEW_FILE" || { echo "FATAL: verdict line not in file"; exit 1; }

case "$CODE_REVIEW_STATUS" in
  self-reviewed)             grep -qE '^SELF-REVIEW' "$REVIEW_FILE" || { echo "FATAL: SELF-REVIEW header missing"; exit 1; } ;;
  self-reviewed-unavailable) grep -qE '^CODEX UNAVAILABLE — SELF-REVIEW' "$REVIEW_FILE" || { echo "FATAL: header missing"; exit 1; } ;;
esac
```

If any of this fails, the task CANNOT be committed — revert it. Hard
gate. No exceptions. This exists specifically to prevent the agent from
rationalizing "I'll skip Codex for efficiency" after a context compaction:
the file either exists with real Codex output (or a properly-headered
self-review with `pre-files.txt` recorded), or the task dies here.

Stage only this task's deliverables with targeted `git add`:

```bash
git add src/errors/ApiError.ts tests/errors/ApiError.test.ts
# NEVER: git add . or git add -A
```

Commit:

```
[[ORCA_RAW_HTML_INLINE:%3Ctype%3E]]: [task title]

Evercode key result [[ORCA_RAW_HTML_INLINE:%3CG%3E]] ("[[ORCA_RAW_HTML_INLINE:%3Ckey%20result%20title%3E]]"), task [[ORCA_RAW_HTML_INLINE:%3CS%3E]]/[[ORCA_RAW_HTML_INLINE:%3Ctotal%3E]]:
- [key change 1]
- [key change 2]
```

Update state.json:

- Task `status` → `completed`
- Record commit hash
- **Update `expected_head`** to new HEAD: `git rev-parse HEAD`
- Confirm `code_review_rounds`, `code_review_status`, `code_review_evidence`,
`code_review_file` are recorded
- Update test results

**Degrade mode:** no commit. Update state.json same way but with
`commit: null`. Changes remain in the working tree.

### Inner 5.5: Flush-proxy sentinel (optional)

If this run opted into flushing (`state.json.flush_proxy == true`, set during
pre-flight §1), emit a **unique** sentinel now so the proxy trims conversation
history at this task boundary:

```bash
[ "$(jq -r .flush_proxy "$RUN_DIR/state.json" 2>/dev/null)" = "true" ] && \
  printf '\n<<EC_FLUSH:%s>>\n' "$(date +%s)"
```

Why the gate is on disk, not env: the opt-in must survive compaction and not
depend on `EVERCODE_FLUSH_PROXY` propagating across separate Bash tool calls
(it doesn't — each call is a fresh subprocess of Claude Code). `state.json` is
the source of truth. `EVERCODE_FLUSH_PROXY=1` remains useful only as a
pre-flight hint (§1 reads it to skip the question) and as the classic
launch-time opt-in.

Why this is safe: evercode recovers full state from disk via **Inner 0** every
task, so dropping earlier turns costs nothing. The timestamp makes each sentinel
unique, and the proxy consumes each id exactly once — so it trims once per task
and never re-trims on later turns. If `flush_proxy` is false the line prints
nothing and is a complete no-op; non-proxy users are unaffected. See
`proxy/README.md`.

### Inner 6: Update State and Next Task

Delete `$RUN_DIR/current-plan.md`: `rm "$RUN_DIR/current-plan.md"`.

Re-read state.json, move to the next pending task in the current
key result.

If all tasks in the current key result are done → return to **Outer F**.

## Scoped Rollback (git mode only)

Rollback operates at the **task** level. When a task fails review or
validation, rollback only that task's changes — not the goal, not the
working tree. Previously-completed tasks in the same goal stay committed.

**Run the drift check first.** If it fails, stop the run entirely instead of
rolling back.

```bash
# Locate the in-progress task's start_commit and pre-files.txt
SUBTASK_START=$(python3 -c "
import json
state = json.load(open('$RUN_DIR/state.json'))
for g in state['key_results']:
    for s in g.get('tasks', []):
        if s['status'] == 'in_progress':
            print(s['start_commit'])
            break
")
SUB_DIR="$RUN_DIR/key-results/<G>/tasks/<S>"   # substitute actual IDs

# Capture post-subtask file inventory
{ git ls-files -z; git ls-files -z --others --exclude-standard; } \
  | sort -zu > "$SUB_DIR/post-files.txt"

# Identify NEW files (created by this task)
python3 -c "
import sys
pre = set(open('$SUB_DIR/pre-files.txt','rb').read().split(b'\x00'))
post = set(open('$SUB_DIR/post-files.txt','rb').read().split(b'\x00'))
new_files = post - pre - {b''}
sys.stdout.buffer.write(b'\x00'.join(new_files))
" > "$SUB_DIR/new-files.txt"

# Remove new files FIRST (git checkout errors on files that didn't exist at SUBTASK_START)
xargs -0 rm -f < "$SUB_DIR/new-files.txt"
xargs -0 git rm --cached --ignore-unmatch -- < "$SUB_DIR/new-files.txt" 2>/dev/null

# Identify MODIFIED files (pre-existing)
git diff -z --name-only "$SUBTASK_START" > "$SUB_DIR/modified.txt"
git diff -z --name-only >> "$SUB_DIR/modified.txt"
sort -zu -o "$SUB_DIR/modified.txt" "$SUB_DIR/modified.txt"

# Restore only pre-existing modified files to pre-task state
xargs -0 git checkout "$SUBTASK_START" -- < "$SUB_DIR/modified.txt" 2>/dev/null

# VERIFY — hard gate
git diff --quiet "$SUBTASK_START"
ROLLBACK_OK=$?
```

**Rollback verification is a hard stop.** If `ROLLBACK_OK` != 0:

1. Do NOT continue to the next task.
2. Set run `status: "drift-stopped"` in state.json.
3. Print terminal-only summary — working tree may contain partial changes.
4. The human must investigate.

**Safety check before rollback:** If `git status --porcelain=v1` shows
modifications to files NOT in `modified.txt` or `new-files.txt` for this
task, something unexpected happened. Stop immediately (same hard-stop
path).

## End Conditions

The shift ends when **one** of these two conditions fires (checked at Outer 0
before proposing the next key result):

### Condition 1: Hard Cap — 8 hours elapsed

Compute elapsed time: `now - state.started_at`. If elapsed ≥
`state.hard_cap_hours` (default 8):

- Finish the CURRENT task if one is in progress (run it through the
inner loop including commit). Do NOT start a new task.
- Set run `status: "hard-capped"`.
- Proceed to Handoff.

This is a wall-clock cap, not a "target" — 8h is the ceiling, not a goal.

### Condition 2: Dual Consensus — the objective is done

**Only when `codex_on`.** Default (`codex_on=false`) ends only at Condition 1 (8h cap) — the agent cannot self-judge "done". The dual-consensus path below runs only when Codex is enabled.

The agent proposes "we're done" → Codex must agree before handoff fires.
This prevents the agent from ending early on its own.

**Agent-initiated path:** At Outer 0, if the agent judges that any further
key result would over-engineer or over-optimize the objective:

1. Write a "done reasoning" file to `$RUN_DIR/end-consensus-draft.md`:
  - The verbatim objective from `state.objective` (do NOT paraphrase)
  - Completed key results as bullet points copied from `state.key_results[]`
  (titles and outcomes as recorded, not summarized in new words)
  - Why any plausible next key result would be over-engineering or
  over-optimizing (be specific — enumerate the ideas you considered)
  - Recommended remaining work, if any, for a future shift
2. Run Codex adversarial review on the draft. **Pass the full `state.json`
   to Codex verbatim** alongside the draft — the "done vs keep going"
   decision is only valid if Codex sees the literal objective and the complete
   key-result history. Paraphrasing is forbidden here; a paraphrased
   summary lets the agent quietly drop the inconvenient parts of the
   objective and biases Codex toward agreement.

   **Prompt scaffold for Codex** — wrap the inputs with explicit rejection
   criteria. Without this, Codex has approved drafts whose lead reasoning was
   "time remaining" or "diminishing returns":

   ```
   You are adversarially reviewing a evercode agent's draft argument
   that the shift should end (Condition 2: dual consensus).

   Inputs:
   ----- state.json (verbatim) -----
   <state.json contents>
   ----- end-consensus-draft.md -----
   <draft contents>
   ----- objective (verbatim, for cross-check) -----
   <state.objective>

   Your job: stress-test whether the objective is genuinely fulfilled OR
   whether any further KR would actively over-engineer / over-optimize it.

   FLAG AS [P1] AND REJECT THE DRAFT if its load-bearing rationale (or any
   numbered/bulleted reason) reduces to ANY of the following — even if
   other valid-sounding reasons are also cited:

   - Time remaining / time pressure / "won't fit before the cap"
   - Diminishing returns / lower-value remaining work
   - Deferral to ROADMAP / future shift / TODO file / next session
   - "Foundation in place" / "natural stopping point" / "good break here"
   - "Cleaner to ship what we have than fold in more"

   These are stopping preferences, not evidence the objective is done.
   The 8-hour cap path handles the time case on its own. If you find any
   of the above, do NOT approve — instead, propose a concrete next KR
   that advances an unfulfilled component of the literal objective.

   Approve ("no P1/P2 contesting done") ONLY if you can affirmatively say:
   "Every component of the literal objective is shipped OR any further KR
   targeting an unshipped component would make the result worse, not just
   less polished."
   ```

  ```bash
  # $PROMPT = the scaffold above with state.json + draft + objective inlined.
  # $OUT    = $RUN_DIR/end-consensus.txt
  echo "$PROMPT" | codex exec - -s read-only -c 'model_reasoning_effort="high"' 2>&1 \
    | tee "$OUT" >/dev/null
  ```
3. Save Codex's response to `$RUN_DIR/end-consensus.txt`. Update

  `state.end_consensus_file`.
4. **Parse Codex's verdict:**
  - **Codex agrees** (no P1/P2 findings contesting "done"): the run
   terminates. Proceed to Handoff.
  - **Codex disagrees** (Codex proposes additional valuable work):
  extract Codex's suggested key result. The agent MUST start a new
  iteration at Outer A using that key result as the starting proposal
  (the agent may refine it, but cannot discard it without a
  counter-argument Codex re-reviews). This is how Codex blocks early
  exit.

**The bar for "done":** any further key result would *actively
over-engineer or over-optimize the literal objective* — i.e., make the
result worse, not just less polished or less complete. If you can
articulate any KR that would still measurably advance the objective,
you are not done — even if that KR is messy, ambitious, or unlikely
to finish cleanly.

**Not valid as "done" rationale.** The stopping-preference items in the Codex scaffold above (and Invariant #6) are never sufficient — if your honest "why done" reduces to any of them, do NOT write `end-consensus-draft.md`; return to Outer A and propose another KR.

There is no Codex-initiated end path. Repeated Outer B rejections are a
signal *to the agent* that it should write `end-consensus-draft.md` and run
the agent-initiated path above (citing the rejections as evidence) — but
they never replace the re-review. Codex's "done" verdict only counts when
issued against a full draft that sees the verbatim objective and complete
key-result history.

**If Codex is unavailable** during Condition 2: the agent CANNOT end on
consensus alone. It must either (a) keep proposing key results and running
them through the self-adversarial loop, or (b) wait for the 8h cap.
Self-consensus is not valid consensus.

### Neither condition = keep going

If neither fires, return to Outer A and propose the next key result.

## Time Tracking

Durations drive realistic estimates for task sizing and end-condition
proximity. Track them on every task and key result:

- `started_at` — recorded when the task / key result moves to `in_progress`
- `ended_at` — recorded when it reaches a terminal status
- `duration_minutes` = `(ended_at - started_at)` rounded to minutes

At Outer F (key result completion), update the running mean:

```
state.average_task_duration_minutes =
  mean(duration_minutes for all tasks with status in
       {completed, blocked, reverted} across all key results)
```

Use the running mean when sizing new tasks in Outer C. If a proposed
task looks 3× the running mean, split it. If it looks 3× smaller, merge
with an adjacent one. This keeps durations honest — no static "small/medium/
large" heuristic table.

**Time estimates do NOT influence end conditions.** Do not use the
running mean to predict whether another key result will fit in the
remaining wall-clock and propose "done" on that basis. The agent's
runway estimates are unreliable; Condition 1 (hard cap) + the handoff
procedure handle the actual-cap-fires case cleanly. Always start the
next KR if the objective still needs work — see §Condition 2 for the
list of insufficient "done" rationales.

## Handoff (End of Successful Shift)

When the shift ends (consensus or 8-hour cap), write the handoff from the
**locked templates** in `$SKILL_DIR/HANDOFF.md` — handoff.md structure, the
terminal summary banner, the `status → end_reason` mapping, and the KR-list
ordering rules all live there. That file is the single source for format; read
it when you reach this section.

The **behavior rules** stay here (they must survive compaction):

1. **Drift check first** (git mode). If it fails → terminal-only summary and
   stop; do NOT set "completed" (leave "drift-stopped"), do NOT write a handoff.
2. Read `$RUN_DIR/state.json` as the source of truth.
3. Write `$RUN_DIR/handoff.md` from the HANDOFF.md template. Same location in
   git and degrade mode — no new folder, no commit.
4. Set `status: "completed"` and `completed_at` in state.json.
5. **Do NOT delete the run folder** — previous runs are kept as history.
6. **Do NOT commit the handoff** — it's private notes, not a repo artifact; the
   user can commit it manually after reviewing.
7. If `state.json.flush_proxy` was true, note "flush proxy: on" in the handoff.
   The proxy is a shared, optional service and is NOT auto-stopped at shift end
   — the user stops it with `./proxy/stop.sh` when done with all evercode work.
8. Print the locked terminal summary banner from HANDOFF.md.

Fallback: if `$SKILL_DIR/HANDOFF.md` is unreachable (very old install), write a
minimal handoff from `state.json` — objective, per-KR status, commits
(`git log BASE_COMMIT..HEAD --oneline`), test results, items needing attention.

## Stop / Resume / Abandon (active-shift re-trigger)

When a start/go trigger fires AND an active shift already exists, prompt the
user:

```

Found an active evercode:
  Run ID: RUN_ID
  Branch: BRANCH
  Started: STARTED_AT
  Progress: X/Y key results complete, key result Z in_progress (phase: PHASE)

What do you want to do?
  [1] Stop     — run full end procedure on the active shift
                 (handoff + archive). Then you can start a new one.
  [2] Resume   — revert the in-progress task to its task boundary,
                 continue iterative key result decisions from where we left off.
  [3] Abandon  — leave the repo state as-is, mark this run "abandoned",
                 start a new shift. No handoff. Old folder kept as history.

```

### Stop

Identical to §Ending a Shift Early below — the shared end procedure.

### Resume (task boundary)

1. Run drift check against the old run's `expected_head` and `branch`. If drift
   detected → switch to Stop path (safer than trying to resume into unknown
   state). Warn the user.
2. Read state.json. Find the task with `status: "in_progress"` (there is
   at most one across the whole run, since tasks run sequentially).
3. If an in-progress task exists:
   - **Git mode:** scoped rollback of just that task to its
     `start_commit`. Set its status to `reverted-on-resume`. Update
     `expected_head` to the new HEAD. Completed tasks in the same goal
     stay committed — Resume does NOT revert past work.
   - **Degrade mode:** scoped rollback unavailable — warn the user that the
     in-progress task's partial changes remain in the working tree. Set
     its status to `reverted-on-resume` (conceptually; the user must clean
     up manually).
4. Continue the execution loop from the next `pending` task in the same
   goal (or the first task of the next goal if the current goal is
   exhausted). Do NOT re-confirm goals or task lists — they were already
   approved / decomposed.
5. Record a `resumed_at` timestamp in state.json so the eventual handoff can
   note the discontinuity.

### Abandon

1. Warn the user: "Abandoning an active shift leaves the in-progress goal's
   changes in the working tree — you may want to review or stash them before
   starting a new shift."
2. Set `status: "abandoned"` and `abandoned_at` in the old run's state.json.
3. Do NOT write a handoff, do NOT revert commits, do NOT touch branch state.
4. Proceed to §Pre-flight as if starting fresh.

## Ending a Shift Early (Stop)

Triggered by "stop evercode", "end evercode", etc., when an active shift
exists. Also the Stop path from §Stop/Resume/Abandon.

1. Finish the current atomic operation — don't leave broken code mid-edit.
2. For the in-progress task:
   - If it has **already passed both Inner 3 (Codex review clean) and
     Inner 4 (tests pass)** and all structural-gate requirements are met,
     commit it normally via Inner 5.
   - Otherwise, **revert it** (git mode) or mark blocked and leave partial
     changes (degrade mode). The Codex review requirement and file gate are
     NOT waived by early stop.
3. Mark the containing goal's status appropriately (`completed` if all its
   tasks are terminal, else `blocked`).
4. Write the handoff note with current progress, clearly marking any
   interrupted task as "interrupted — reverted" or "interrupted —
   partial changes in tree".
5. Run the standard Handoff procedure (commit handoff in git mode, leave
   file at cwd root in degrade mode).
6. Set run `status: "interrupted"` (not `completed`) and `ended_at` in
   state.json.
7. Print terminal summary as in Handoff.

## Non-Git Degrade Mode

When evercode runs outside a git repo, it degrades gracefully. The execution
loop and Codex reviews still run, but these guarantees are gone:

| Feature             | Git mode | Degrade mode |
|---------------------|----------|--------------|
| Commit per task | Yes      | No — tasks just update state.json |
| Scoped rollback     | Yes      | No — failed tasks leave partial changes |
| Drift check         | Yes      | No — can't detect external branch/HEAD changes |
| Every-commit-green  | Yes      | N/A — nothing is committed |
| Structural file gate | Yes     | Yes — `code-review.txt` still required |
| Handoff location    | `.evercode/runs/RUN_ID/handoff.md` (file only) | `.evercode/runs/RUN_ID/handoff.md` (file only) |

Prominently flag the limitations in the handoff. If a task fails
validation in degrade mode, its partial changes stay in the working tree —
the user must review and clean them up manually.

## Error Recovery

- **Build breaks:** Fix before moving on. If you can't fix in 2 attempts, revert
  the task (git mode) or mark it blocked (degrade mode) and move on.
- **Merge conflicts:** Should not happen since no one else is committing to
  this branch. If they do, treat as drift — stop the run and write the handoff.
- **Codex unavailable:** fall back to self-review with header `CODEX UNAVAILABLE — SELF-REVIEW` (Invariant #14).

## Principles

The non-negotiable rules live in `INVARIANTS.md` (re-read every task via Inner 0). Two emphases unique to this spec:

- **Scoped rollback, not whole-tree.** A failed task reverts only its own changes; completed tasks stay committed.
- **State file is truth.** Update and re-read `state.json` every phase transition — memory is wrong after hours; disk is not.
```

