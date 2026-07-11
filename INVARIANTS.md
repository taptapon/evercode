# Evercode Invariants (read before every task)

NON-NEGOTIABLE. Compaction is not an excuse. "Time efficiency" is not an excuse. "Simple change" is not an excuse.

**Mode flag:** `state.json.codex_on` (from `EVERCODE_CODEX=1` at launch). Default **off** — Claude self-reviews. When on, the Codex gates (§"Only when codex_on") also apply.

## Always (every mode)

1. **Drift check before every write** (git mode). If it fails, stop the run.
2. **Self-review + tests before every commit.** Re-read your changes with fresh eyes, run the full suite, write the review to `tasks/<T>/code-review.txt`. Never commit red or unreviewed code.
3. **Pre-commit structural gate.** A task cannot be committed unless its `code-review.txt` exists, is non-empty, and contains the review header (self-review header when `codex_on=false`; Codex verdict line when `codex_on=true`).
4. **No questions to the user mid-shift.** Make judgment calls, record in `decisions_made` in state.json.
5. **Every commit must be green.** If tests fail, revert (git) or mark blocked (degrade). Never commit red code.
6. **Stay on the objective.** Every key result must serve it. Don't refactor unrelated code.
7. **End only at the 8-hour hard cap** (Condition 1). The agent cannot end on its own. With `codex_on`, dual consensus also ends the shift (SKILL.md §Codex Review).
8. **Never push to remote.** The human decides.
9. **Flag non-Codex work.** When `codex_on=false`, every task is self-reviewed — the handoff must state this prominently.

## Only when `codex_on` (EVERCODE_CODEX=1)

10. **Key-result proposal requires Codex approval** (Outer B). Adversarial review: "is this worth implementing toward the objective, or would it over-engineer?" Output to `key-results/<KR>/codex-approval.txt`.
11. **Decomposition requires Codex adversarial review** (Outer D). Output to `key-results/<KR>/decomp-adversarial.txt`.
12. **Task code review uses the Codex loop** (Inner 3). Loop until no P1/P2, output to `tasks/<T>/code-review.txt`. The gate (#3) then requires a Codex verdict line matching `code_review_evidence`.
13. **Dual consensus can end the shift** (Condition 2). Agent proposes "done" AND Codex agrees. Self-consensus is never valid.
14. **If Codex errors mid-run** (command not found / timeout / crash — not a choice to skip): fall back to self-review for that artifact, header `CODEX UNAVAILABLE — SELF-REVIEW: <error>`, flag in handoff. Choosing to skip is a protocol violation.
