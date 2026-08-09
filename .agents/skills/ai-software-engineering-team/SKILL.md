---
name: ai-software-engineering-team
description: Use when the user asks Codex to start, inspect, pause, resume, review, approve, recover, integrate, or complete a software engineering task in this repository.
---

# AI Software Engineering Team

Codex is the only engineering authority. Human controls strategy, external actions, production, sensitive data, permission expansion, irreversible actions, and review degradation.

## Start every natural-language request

1. Read `handoff.md`, `CODEX_AGENT_DISPATCH_PROTOCOL.md`, `AGENT_ROLE_AND_MODEL_MATRIX.md`, `SOFTWARE_ENGINEERING_WORKFLOW.md`, and `GIT_WORKFLOW.md`.
2. Run `scripts/repo-health.sh` from the main repository root. Stop writes if health or ownership cannot be proven.
3. Use `scripts/team-control`. If the control database is absent, initialize it safely and continue the requested status or task flow; do not stop at “database missing.” Reconcile every unfinished `PREPARED` operation before any new write.
4. Convert the request into the seven-question Dispatch Record, classify L1/L2/L3 risk, and define evidence and acceptance before dispatch.

## Map intent to control action

- Start/build/fix: register the Dispatch Record, then start one short branch, one Worktree, and one writing Agent.
- Status/blockers/approvals: query current facts and report state, Git SHA, evidence, Review, blockers, and pending Human action—not only a percentage.
- Pause: request `PAUSE_REQUESTED`, stop new work, wait for writer acknowledgement, then mark `PAUSED`.
- Resume: revalidate Git, locks, blockers, and saved resume state before continuing.
- Review/complete: require applicable tests, independent Review, acceptance, integration verification, evidence indexing, and Mimo inventory before closure.

## Minor and recovery

`Minor` means residue after a failed `git worktree add`; it is not MinIO. Use `scripts/worktree-doctor` to inspect before repair. Repair only a current `repairable classification`. Preserve dirty files, extra commits, unknown paths, symlinks, registrations, and metadata; otherwise mark `BLOCKED`. Never run git reset --hard, `git clean -xdf`, force deletion, shell `eval`, or unscoped cleanup.

## Approval boundaries

Use `NEEDS_HUMAN_APPROVAL` before destructive, external, production, sensitive, privileged, irreversible, or strategically expanded work. MVP 1, GitHub Remote setup/first push, and Claude review degradation remain separate Human decisions. Claude fallback requires quota evidence and a current-session explicit yes; no automatic substitute inherits Claude acceptance.

Record paths, SHA values, test results, Review disposition, residual risks, and approvals. Never claim merged, released, or complete without fresh evidence.
