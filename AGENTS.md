# Agent instructions

## Scope and sources

Get the existing arrangement MVP accepted quickly. Read `README.md` and `docs/PLAN.md`; the latter is the only task checklist. Mark completed work there at its acceptance boundary. Use `docs/PROTOCOL.md` and `docs/AUDIO-PROJECTS.md` for current contracts. Old plans/reviews are in Git history and do not authorize work.

Implement the smallest complete slice needed for the current request. Keep deferred features out of the current slice unless the user requests them or they resolve a demonstrated MVP blocker. Avoid another planning document, a framework migration, a second session model or speculative abstractions. Preserve unrelated working-tree changes.

## Implementation and verification

- Reuse Rust, JSONL, the loopback bridge, existing capability helpers and bounded snapshot undo.
- Preserve untouched frames, Hz, gain/pan, audio assets, typed drafts and rejected takes. Tempo changes the authoring grid without moving saved frames. Applied engine state and downloaded project files are distinct.
- Keep structural edits stopped, failures transactional and file publication exclusive. No allocation, blocking locks, I/O, process startup or foreign initialization in audio callbacks.
- Run checks appropriate to the change. Documentation-only work needs link/status and `git diff --check` verification; CSS/presentation needs relevant browser/layout checks. Behavior changes need focused regressions and relevant format/lint/tests. Persistence/audio changes also need save/reopen/export checks; native/FFI/runtime changes need their relevant optional checks. Do not rerun unrelated adapter matrices.
- Use `.github/workflows/ci.yml` and README commands for the standard checks. Distinguish automated/muted native evidence from acoustic listening and physical MIDI/latency acceptance.

## Keep the plan and docs current

- Before starting a slice, compare `docs/PLAN.md` with the relevant code, tests and current request. Correct stale statuses; do not repeat work already delivered or turn deferred ideas into new requirements.
- Before opening or updating a PR, reconcile the plan in the same change. Check off work only when its stated acceptance is met. If implementation is finished but listening, hardware or another acceptance check remains open, say so briefly and leave that check unchecked. Never describe an unrun or skipped check as passed.
- Update remaining work and **Next action** when a slice closes or the user changes scope. Keep the next step concrete and small. Record an observed blocker with a short reproduction or relevant test reference; add follow-ups only when necessary for the current scope or explicitly requested.
- Update `README.md` when run/build commands, user workflows or supported limits change; update `docs/PROTOCOL.md` for command/schema/transport changes and `docs/AUDIO-PROJECTS.md` for asset/ZIP ownership or validation changes. Keep these updates alongside the implementation rather than deferring them to another PR.
- Remove superseded guidance, broken links and contradictory claims encountered in the touched area. Keep one authoritative explanation per topic; link to it rather than copying it. Do not create new roadmaps, review dumps, dated snapshots or cumulative test-count logs. Git history preserves past decisions and evidence.
- Before handoff, check the final diff for unrelated changes, stale plan instructions and inaccurate completion claims. Summarize what changed, checks actually run, remaining acceptance/blockers and PR/merge status. Distinguish local work from merged work. Documentation upkeep alone does not authorize starting the next feature.

## Automatic PR review and merge

The user authorizes automatic fresh-context review and merge of PRs opened for work in this repository. Do not ask for routine merge confirmation when the following conditions are met.

1. After opening a PR, spawn a new review agent with no inherited conversation/history (`fork_turns: "none"`, or the equivalent fresh-context option). Provide only the repository/worktree location, PR URL, intended change and validation commands/results. The reviewer must read this file and inspect the actual PR diff, relevant source and tests independently. The implementing agent's own review does not satisfy this step.
2. Ask the reviewer to find concrete correctness, regression, data-loss, audio/FFI, scope and misleading-documentation problems, including whether plan statuses and current docs match the delivered behavior and verification evidence. Require a clear verdict and actionable findings with file/line evidence; review is mandatory even for docs-only PRs.
3. Fix blocking findings, run affected checks and get a fresh-context review of the updated PR head. Repeat until no blocking findings remain. Report any nonblocking follow-ups without silently expanding scope.
4. Before merging, verify that the reviewed head is still the current PR head, applicable checks (including both Linux/macOS CI jobs) pass, the PR is mergeable and ready, and repository approval/branch-protection requirements are satisfied. If new commits or conflict-resolution changes alter the reviewed diff, review again. Do not bypass protections, ignore failures or treat missing/skipped required checks as passes.
5. When all conditions hold, merge automatically using the repository's allowed merge method. Report the PR link, review verdict, checks and merge result. If review tooling, checks, permissions or required approvals prevent completion, leave the PR open and report the specific blocker; never claim review or merge succeeded without evidence.

This is an instruction for agents working here, not a GitHub automation service. An agent must remain active to perform the review, checks and merge.
