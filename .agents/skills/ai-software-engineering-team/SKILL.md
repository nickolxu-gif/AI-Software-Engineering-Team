---
name: ai-software-engineering-team
description: Use when the user asks Codex to start, inspect, pause, resume, review, approve, recover, integrate, or complete a software engineering task in this repository.
---

# AI Software Engineering Team

Codex is the only engineering authority. Human controls strategy, external actions, production, sensitive data, permission expansion, irreversible actions, and review degradation.

## Start every natural-language request

1. Read `handoff.md`, `CODEX_AGENT_DISPATCH_PROTOCOL.md`, `AGENT_ROLE_AND_MODEL_MATRIX.md`, `SOFTWARE_ENGINEERING_WORKFLOW.md`, and `GIT_WORKFLOW.md`.
2. Run `scripts/repo-health.sh` from the main repository root. Stop writes if health or ownership cannot be proven.
3. For ordinary requests, If the control database is absent, initialize it safely with `scripts/team-control init`, then continue. If the user explicitly requires strictly read-only or no writes, do not initialize; perform a read-only Git and file inventory and report control-state unavailability.
4. Convert the request into the seven-question Dispatch Record, classify L1/L2/L3 risk, and define evidence and acceptance before dispatch.

## Respect the MVP 0 interface boundary

The stable CLI is limited to `init`, `start`, `status` for a known dispatch ID, `transition`, approval list, and `scripts/worktree-doctor` inspect/repair. There is no stable CLI for global task/blocker summaries, collaborator records, approval create/consume, generic `PREPARED` recovery, or Mimo.

Use only tested internal orchestration APIs for unsupported flows. Reconcile `PREPARED` only through `OperationCoordinator.reconcile_one` or `reconcile_all` with the correct verifier. If unavailable or uncertain, mark `BLOCKED`. Never edit SQLite directly.

## Map intent to control action

- Start/build/fix: register the Dispatch Record; use one branch, Worktree, and writer.
- Status/blockers: require a known dispatch ID; otherwise report that global listing is unavailable.
- Approvals: list only. Creation and consumption require controlled internal APIs.
- Pause/resume: operate one known task through validated transitions and safe checkpoints; never imply batch pause. Revalidate Git, locks, blockers, and resume state.
- Agent, Blocker, Review, Evidence, and Mimo flows have no stable CLI; orchestrate internally or report unavailable.
- Review/complete: require applicable tests, independent Review, acceptance, integration verification, evidence indexing, and Mimo inventory before closure.

## Minor and recovery

`Minor` means residue after a failed `git worktree add`; it is not MinIO. Use `scripts/worktree-doctor` to inspect before repair. Repair only a current `repairable classification`. Preserve dirty files, extra commits, unknown paths, symlinks, registrations, and metadata; otherwise mark `BLOCKED`. Never run git reset --hard, `git clean -xdf`, force deletion, shell `eval`, or unscoped cleanup.

## Approval boundaries

Use `NEEDS_HUMAN_APPROVAL` before destructive, external, production, sensitive, privileged, irreversible, or strategically expanded work. MVP 1, GitHub Remote setup/first push, and Claude review degradation remain separate Human decisions. Claude fallback requires quota evidence and a current-session explicit yes; no automatic substitute inherits Claude acceptance.

Treat `NEEDS_HUMAN_APPROVAL` as the status `effective_state` overlay while the lifecycle remains in `task.state`, not as a normal lifecycle transition.

## Privacy boundary

No automatic secret scanning or redaction exists. Do not accept or persist logs containing Tokens, credentials, private keys, or sensitive originals. Minimize content manually or with explicit rules before persistence. On suspected secrets, stop and request redacted material; schema validation is not universal secret protection.

Record paths, SHA values, test results, Review disposition, residual risks, and approvals. Never claim merged, released, or complete without fresh evidence.
