# Evercode Handoff Templates (locked)

Read when the shift ends (consensus, 8-hour cap, or early Stop). These templates
are **locked** — exact field order, no free-form prose, no extra sections. Every
shift's ending must look visually identical so the human can scan it at a glance.

The **behavior rules** (drift check first, don't commit the handoff, don't delete
the run folder, flush-proxy note) live in SKILL.md §Handoff; this file is only
the format.

## handoff.md

Write to `$RUN_DIR/handoff.md` (same path in git and degrade mode — no new
folder, no commit). Lives alongside the run's state and artifacts so the whole
run (plans, Codex outputs, handoff) is in one place.

```markdown
# Evercode Handoff — RUN_ID

## Summary
[2-3 sentences: what was accomplished overall, in terms of the large goals]

**Run ID:** RUN_ID
**Mode:** git / degrade
**Branch:** BRANCH              [git mode only]
**Started from:** BASE_COMMIT   [git mode only]
**Objective:** [text or "propose"]
**Commits:** N                  [git mode only]

## Goals

### Goal 1: [Large goal title] — Complete / Blocked / Partial
**What shipped:** [1-2 sentences at goal level]
**Tasks:** M completed / K total (origin: user-approved × J, autonomous × L)

#### Task 1.1: [Title] — Complete
- **Commit:** [short hash + message]   [git mode only]
- **Changes:** [key bullet]
- **Codex review:** Clean after N rounds

#### Task 1.2: [Title] — Blocked
- **Why:** [what Codex flagged or test failed]
- **State:** reverted (git) / partial-in-tree (degrade)

#### Task 1.3 [autonomous]: [Title] — Complete
- (same fields — the [autonomous] tag marks additions the user did not explicitly approve)

**Decisions made (goal-level):**
- [judgment calls made without the user]

### Goal 2: ...

## Test Results
- Passing: X/Y
- New tests added: N

## Items Needing Human Attention
- [Anything you were unsure about]
- [Decisions that should be validated]
- [Tasks Codex flagged that you disagreed with]
- [Tasks that were blocked/reverted and why]
- [Autonomous additions — user should evaluate separately]
- [Degrade-mode: any tasks that left partial changes in working tree]
- [Codex-unavailable tasks, if any]

## How to Review                                  [git mode only]
```bash
# All evercode commits (one per task, grouped under goals in this handoff)
git log BASE_COMMIT..HEAD --oneline

# Full diff
git diff BASE_COMMIT

# Revert a specific task's commit
git revert <commit-hash>

# Or undo all evercode work
git reset --hard BASE_COMMIT
```

## Recommendations for Next Session

- [What to work on next]
- [Any tech debt introduced]
```

## Terminal summary banner

Print after writing handoff.md. The output is a locked template — exact field
order, no embellishment between fields. The only place for judgment is the
**headline**, which is the verbatim first sentence of the handoff's `## Summary`
section (do not paraphrase, do not compose a new one — copy it).

```
══════════════════════════════════════════════════════════════════════
  EVERCODE ENDED — <end_reason>
══════════════════════════════════════════════════════════════════════

  <headline — verbatim first sentence of handoff ## Summary>

  Run ID:    RUN_ID
  Started:   YYYY-MM-DD HH:MM TZ
  Ended:     YYYY-MM-DD HH:MM TZ
  Elapsed:   Xh Ym  (cap: 8h)

  Branch:    BRANCH                                  [git mode only]
  Commits:   N new since BASE_COMMIT                 [git mode only]
  Review:    git log BASE_COMMIT..HEAD --oneline     [git mode only]

  Handoff:   .evercode/runs/RUN_ID/handoff.md
  Run dir:   .evercode/runs/RUN_ID/

──────────────────────────────────────────────────────────────────────
  KEY RESULTS  (T total · C completed · B blocked · R reverted/superseded)
──────────────────────────────────────────────────────────────────────

  ✓ KR2   <title from state.key_results[].title>
  ✓ KR3   <title>
  ⊘ KR4   <title> (blocked — <one-phrase reason from state>)
  ✗ KR1   <title> (superseded by KR2)

──────────────────────────────────────────────────────────────────────
  Tests: P/T total                                   [if recorded in state]
══════════════════════════════════════════════════════════════════════
```

### `<end_reason>` derives from `state.status`

| `state.status`  | `<end_reason>`                                    |
|-----------------|---------------------------------------------------|
| `completed`     | `dual consensus — <rationale, ≤ ~8 words>`        |
| `hard-capped`   | `8-hour hard cap`                                 |
| `interrupted`   | `user stopped`                                    |
| `drift-stopped` | `drift detected — handoff NOT written`            |

For `completed`, the `<rationale>` is a short phrase summarizing **why further
work would not help** — taken from the corresponding section of
`end-consensus-draft.md` (the same "why over-engineering" reasoning Codex just
agreed with). Keep it terse and concrete. Examples:

- `dual consensus — objective fully achieved`
- `dual consensus — remaining work would over-engineer the objective`
- `dual consensus — remaining work blocked on external decisions`
- `dual consensus — remaining work needs human input the agent cannot supply`

### KR list ordering

Order = completed first (in completion order), then blocked, then
reverted/superseded. Glyphs are fixed: `✓` completed, `⊘` blocked, `✗` reverted
or superseded. The parenthesized annotation is **only** the terminal status
reason (≤ ~6 words copied from state) — never free prose.

### `drift-stopped` variant

Omit the headline (no handoff Summary exists). Replace the `Handoff:` line with
`Handoff:   NOT WRITTEN — see drift output above`. Everything else stays.

## Forbidden in the terminal summary

`Status:` paragraphs, narrative sentences between fields, per-KR commentary
beyond the parenthesized status reason, emoji, ASCII art beyond the three
horizontal rules shown. If you feel the urge to add nuance, it belongs in the
handoff file, not this banner.
