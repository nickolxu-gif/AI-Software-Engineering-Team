# CodeBuddy V4.10 Project Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-local CodeBuddy/GLM-5.2 V4.10 adapter that reuses the approved global packet and stream-runner core without modifying global configuration or the Hub checkout.

**Architecture:** The adapter is a thin, project-owned shell boundary. It binds the current Git root, bounded changed-file scope, local `.review-evidence` receipt directory, and `reports/` report path to the global `claude-emergency-verifier` V4.10.3 `review_packet.py` and `codebuddy_stream_runner.py`, checking their pinned SHA-256 values before provider start. It keeps the global immutable packet, single-use claim, strict verdict validation, no-tools, no-session-persistence, one-turn, and sanitized receipt contract.

**Tech Stack:** POSIX shell, Python 3, CodeBuddy 2.128 GLM-5.2, unittest, global V4.10.3 packet/stream utilities.

---

### Task 1: Record the L2 dispatch and adapter contract

**Files:**
- Create: `artifacts/dispatches/20260813-010/dispatch.md`
- Create: `docs/superpowers/plans/2026-08-13-codebuddy-v4-project-adapter.md`

- [x] **Step 1: Record scope and gates**

The dispatch records this as L2, with no GitHub, push, global configuration, credential, or provider fallback expansion. Acceptance requires shell syntax, contract tests, packet preflight, and one authorized CodeBuddy review result; a `BLOCKED` result does not accept the intake candidate.

### Task 2: Write the failing adapter contract tests

**Files:**
- Create: `tests/test_codebuddy_adapter.py`

- [x] **Step 1: Assert the adapter contract**

The test must require an executable `scripts/codebuddy-verify.sh`, the global V4.10.3 core paths and pinned hashes, `review_packet.py` build/claim/receipt/validation calls, `--tools ''`, `--no-session-persistence`, `--max-turns 1`, `--strict-mcp-config`, `--output-format stream-json`, and no dangerous permission bypass.

- [x] **Step 2: Run the contract test and observe the expected missing-wrapper failure**

Run `python3 -m unittest tests.test_codebuddy_adapter -q`. It must fail because the project adapter does not yet exist.

### Task 3: Implement the thin project adapter

**Files:**
- Create: `scripts/codebuddy-verify.sh`
- Modify: `.gitignore`

- [x] **Step 1: Add the wrapper**

Reuse the reviewed V4 wrapper shape while binding `project_root=$(pwd -P)`, the fixed global V4.10.3 core paths, pinned SHA checks, local packet evidence, explicit file allowlisting, and the project report path. Preserve fail-closed reason codes for packet, local diff, claim, transport, parse, and receipt failures.

- [x] **Step 2: Ignore local packet evidence**

Add `/.review-evidence/` to `.gitignore`; reports remain visible as local review evidence and are not staged by the adapter task.

- [x] **Step 3: Run the contract test and shell syntax check**

Run `python3 -m unittest tests.test_codebuddy_adapter -q` and `bash -n scripts/codebuddy-verify.sh`. Both must pass.

### Task 4: Verify the adapter against the current candidate

**Files:**
- Modify: `artifacts/dispatches/20260813-010/dispatch.md`

- [x] **Step 1: Run deterministic packet preflight**

Run the adapter with the current candidate delta:

```bash
scripts/codebuddy-verify.sh \
  --base-ref 65aa59a1de0cddc6327e0b7f01d324d4b4b60223 \
  --head-ref HEAD \
  --prompt 'Review only the immutable V4 packet for the task-intake acknowledgement and migration delta.' \
  --report reports/codebuddy-verifier-20260813-010.md \
  --file .agents/skills/ai-software-engineering-team/SKILL.md \
  --file docs/superpowers/specs/2026-08-12-mvp2d-task-intake-design.md \
  --file team_control/dashboard_server.py \
  --file team_control/store.py \
  --file team_control/task_intakes.py \
  --file tests/test_dashboard_server.py \
  --file tests/test_store.py \
  --file tests/test_task_intakes.py
```

- [x] **Step 2: Accept only a parsed V4 verdict**

Record provider, model, verdict, packet fingerprint, and receipt path. `PASS_WITH_WARNINGS`, `MODIFY`, transport failure, parse failure, quota failure, or missing result keeps the candidate blocked.

- [x] **Step 3: Run the full local regression**

Run `python3 -m unittest discover -s tests -q`, `/opt/homebrew/bin/python3.14 -m unittest discover -s tests -q`, and `git diff --check`. A green adapter test does not replace the project regression.

### Task 6: Harden the adapter after independent review

**Files:**
- Modify: `scripts/codebuddy-verify.sh`
- Modify: `tests/test_codebuddy_adapter.py`
- Modify: `artifacts/dispatches/20260813-010/dispatch.md`

- [x] **Step 1: Close provider and file-boundary findings**

The adapter now requires a strict `PASS`, accepts only new `reports/` paths, validates refs, requires changed tracked source files, excludes sensitive/runtime paths, fixes the CodeBuddy executable, and clears setting sources.

- [x] **Step 2: Add runtime fake-provider coverage**

The test harness executes a temporary wrapper copy against a fake V4 stream and proves `PASS_WITH_WARNINGS` returns non-zero while retaining only the sanitized report.

- [x] **Step 3: Re-run both full interpreter suites and obtain fresh fallback authorization before any new CodeBuddy call**

Both interpreter suites passed with `338/338`; a new CodeBuddy call remains authorization-gated because the adapter hardening changed the packet fingerprint.

### Task 5: Commit the adapter only after its checks pass

**Files:**
- Commit: `scripts/codebuddy-verify.sh`, `.gitignore`, `tests/test_codebuddy_adapter.py`, and dispatch/plan records.

- [x] **Step 1: Review staged scope**

Run `git diff --cached --check` and `git status --short`; preserve unrelated untracked `reports/` files.

- [x] **Step 2: Commit**

Run `git add -- scripts/codebuddy-verify.sh .gitignore tests/test_codebuddy_adapter.py artifacts/dispatches/20260813-010/dispatch.md docs/superpowers/plans/2026-08-13-codebuddy-v4-project-adapter.md` followed by `git commit -m 'feat: add project CodeBuddy V4 adapter'`.
