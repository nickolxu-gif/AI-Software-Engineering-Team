---
name: ai-software-engineering-team
description: Use when the user asks Codex to open the team dashboard or start, inspect, pause, resume, review, approve, recover, integrate, or complete a software engineering task in this repository.
---

# AI Software Engineering Team

Codex is the only engineering authority. Human controls strategy, external, production, sensitive, privileged, irreversible, and review-degradation actions.

## Start every natural-language request

1. Read `handoff.md`, `CODEX_AGENT_DISPATCH_PROTOCOL.md`, `AGENT_ROLE_AND_MODEL_MATRIX.md`, `SOFTWARE_ENGINEERING_WORKFLOW.md`, and `GIT_WORKFLOW.md`.
2. Run `scripts/repo-health.sh` from main. Stop writes unless health and ownership are proven.
3. For ordinary non-dashboard requests, If the control database is absent, initialize it safely with `scripts/team-control init`. If the user explicitly requires strictly read-only or sends Dashboard open/view intents, do not initialize; perform a read-only Git and file inventory and report control-state unavailability.
4. Run only if the control database exists: writable requests run once per request: `scripts/team-control process-pending-intents --limit 10`. If init fails or it is unavailable, do not run the queue and stop subsequent writes. Report individual `REJECTED` or `BLOCKED` results and continue; a non-zero result means control-plane failure: stop subsequent writes and give only a read-only explanation. This foreground loop is not a daemon.
5. Process up to 10 `PENDING` task intakes: Dispatch/blocker before `TaskIntakeService.acknowledge()` to `ACKNOWLEDGED`; browser cannot act.
6. Convert the current request into the seven-question Dispatch Record, classify L1/L2/L3 risk, and define evidence and acceptance before dispatch.

## Open the MVP 1 dashboard

Map “打开工程工作台”, “查看团队全局状态”, or “打开软件 AI 工程团队工作台” to `scripts/open-team-dashboard`.

Before launch inspect health. The dashboard never initializes a missing database: report `DATABASE_UNAVAILABLE` and return to Codex. Bind only to `127.0.0.1`; never expose it. The browser remains read-only for engine actions and only submits bounded intent requests; it never processes them. Other actions return to Codex.

## Respect the MVP 0 interface boundary

Stable entry points: `init`, `start`, `status` for a known dispatch ID, `transition`, approvals, `process-pending-intents`, `open-team-dashboard`, and `scripts/worktree-doctor`. There is no stable CLI for collaborator writes, approval create/consume, `PREPARED` recovery, or Mimo.

Use tested internal APIs only. Reconcile `PREPARED` through `OperationCoordinator` with the correct verifier; otherwise mark `BLOCKED`. Never edit SQLite directly.

## Map intent to control action

- Start/build/fix: one task, branch, Worktree, writer.
- Status/action: use a known dispatch ID; dashboard observes.
- Pause/resume: safe transitions; revalidate Git, locks, blockers, resume.
- Review/complete: tests, independent Review, acceptance, integration verification, evidence, Mimo inventory.

## Minor and recovery

`Minor` means residue after a failed `git worktree add`; it is not MinIO. Use `scripts/worktree-doctor` to inspect before repair. Repair only a current `repairable classification`. Preserve unknown or dirty facts; otherwise mark `BLOCKED`. Never run git reset --hard, `git clean -xdf`, force deletion, shell `eval`, or unscoped cleanup.

## Approval boundaries

Use `NEEDS_HUMAN_APPROVAL` before destructive, external, production, sensitive, privileged, irreversible, or expanded work. MVP 2/3, GitHub Remote, and Claude review degradation remain Human decisions. Claude fallback needs quota evidence and a current-session explicit yes.

Treat `NEEDS_HUMAN_APPROVAL` as the status `effective_state` overlay while the lifecycle remains in `task.state`, not as a normal lifecycle transition.

## Privacy boundary

No automatic secret scanning or redaction exists. Do not persist Tokens, credentials, private keys, or sensitive originals. Minimize; request redacted material when uncertain.

Record paths, SHA, tests, Review, risks, and approvals. Never claim merged, released, or complete without fresh evidence.
