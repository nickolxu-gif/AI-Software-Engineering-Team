# MVP 2D 受控任务需求入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded local dashboard intake inbox for new engineering requests without allowing the browser to create or execute engineering tasks.

**Architecture:** Introduce `TaskIntakeService` beside the existing task-bound `IntentService`. Its contract and persistence are independent because an intake has no `dispatch_id` or trusted Git SHA. Reuse the existing session-token, loopback-origin, JSON-size, response-envelope and schema-preflight boundaries; add a small overview form that submits an intake and only renders safe summaries.

**Tech Stack:** Python standard library, SQLite, existing `http.server` dashboard, vanilla browser JavaScript, `unittest`.

---

### Task 1: Contract and durable inbox

**Files:**
- Modify: `team_control/contracts.py`
- Create: `team_control/task_intakes.py`
- Modify: `team_control/store.py`
- Test: `tests/test_task_intakes.py`

- [ ] **Step 1: Write failing contract and idempotency tests**

```python
def test_normalize_task_intake_requires_exact_bounded_fields(self):
    request = {
        "title": "Add a safe task entry",
        "objective": "Let the dashboard submit a request",
        "context": "No Git from browser",
        "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
    }
    self.assertEqual(normalize_task_intake_request(request)["title"], request["title"])
    for invalid in ({}, {**request, "unknown": True}, {**request, "title": ""}):
        with self.assertRaises(ContractError):
            normalize_task_intake_request(invalid)

def test_submit_is_idempotent_and_does_not_create_a_task(self):
    first = self.service.submit(self.request)
    second = self.service.submit(self.request)
    self.assertEqual(first["intake_id"], second["intake_id"])
    self.assertEqual(self.store.list_tasks(), [])
```

- [ ] **Step 2: Run the focused test to verify RED**

Run: `python3 -m unittest tests.test_task_intakes -q`
Expected: import failure for `team_control.task_intakes`.

- [ ] **Step 3: Implement the minimum private intake contract and store API**

Add `TASK_INTAKE_REQUEST_FIELDS`, UTF-8 / exact-field / UUID validation, bounded text validation, and a domain-separated request hash. Add `task_intake_requests` to `ControlStore.initialize()` and required schema columns. Implement `create_task_intake`, `get_task_intake`, and bounded pending/list helpers; enforce a unique idempotency key and return `PENDING` records.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_task_intakes -q`
Expected: `OK`.

- [ ] **Step 5: Commit the isolated storage increment**

```bash
git add -- team_control/contracts.py team_control/task_intakes.py team_control/store.py tests/test_task_intakes.py
git commit -m "feat: add bounded task intake inbox"
```

### Task 2: Dashboard API and safe read model

**Files:**
- Modify: `team_control/dashboard_server.py`
- Modify: `team_control/dashboard_read_model.py`
- Test: `tests/test_dashboard_server.py`
- Test: `tests/test_dashboard_read_model.py`

- [ ] **Step 1: Write failing HTTP boundary tests**

```python
def test_task_intake_requires_session_token_and_returns_safe_summary(self):
    token = self.session_token()
    response, payload, body = self.request_json(
        "POST", "/api/task-intakes", self.task_intake_request(),
        headers={"X-Team-Intent-Token": token},
    )
    self.assertEqual(response.status, 202)
    self.assertEqual(set(payload["data"]), {
        "intake_id", "title", "objective", "status", "result_code",
        "created_at", "updated_at",
    })
    self.assertNotIn("context", body.decode("utf-8"))

def test_task_intake_rejects_wrong_origin_or_missing_token(self):
    before = self.database_digest()
    response, payload, body = self.request_json(
        "POST", "/api/task-intakes", self.task_intake_request(),
    )
    self.assertEqual(response.status, 403)
    self.assertEqual(payload["error"]["code"], "TOKEN_REJECTED")
    self.assertEqual(self.database_digest(), before)
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python3 -m unittest tests.test_dashboard_server -q`
Expected: `POST /api/task-intakes` is rejected or route is absent.

- [ ] **Step 3: Add the route and read-model summaries**

Reuse `_intent_request` only for transport parsing; dispatch `/api/task-intakes` to a new handler that applies existing Host/Origin/token checks and `TaskIntakeService.submit`. Add an overview-safe, capped `pending_task_intakes` field that never returns context, request hash or idempotency key. Preserve 405 for every other business-write route.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_dashboard_server tests.test_dashboard_read_model -q`
Expected: `OK`.

- [ ] **Step 5: Commit the API increment**

```bash
git add -- team_control/dashboard_server.py team_control/dashboard_read_model.py tests/test_dashboard_server.py tests/test_dashboard_read_model.py
git commit -m "feat: expose safe task intake endpoint"
```

### Task 3: Overview form, operating guide and regression checks

**Files:**
- Modify: `apps/dashboard/app.js`
- Modify: `apps/dashboard/styles.css`
- Modify: `tests/test_dashboard_server.py`
- Modify: `tests/test_dashboard_ui_contract.py`
- Modify: `USER_OPERATING_GUIDE.md`

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_dashboard_assets_expose_task_intake_without_execution_controls(self):
    self.assertIn("/api/task-intakes", app)
    self.assertIn("提交新工程需求", app)
    for forbidden in ("process-pending-intents", "git merge", "git push", "nonce"):
        self.assertNotIn(forbidden, app)
```

- [ ] **Step 2: Run the focused UI tests to verify RED**

Run: `python3 -m unittest tests.test_dashboard_server tests.test_dashboard_ui_contract -q`
Expected: assertion failure for the missing task intake UI.

- [ ] **Step 3: Implement a bounded overview form and documentation**

Render title, objective and optional context fields only on overview. Submit through the existing ephemeral session token; generate a UUID; disable only while pending; show no returned context. Add compact CSS and guide text explaining that the form is an inbox, and that Codex must turn it into a seven-question Dispatch Record in a later engineering request.

- [ ] **Step 4: Run focused UI tests to verify GREEN**

Run: `python3 -m unittest tests.test_dashboard_server tests.test_dashboard_ui_contract -q`
Expected: `OK`.

- [ ] **Step 5: Run complete local verification and commit**

```bash
python3 -m unittest discover -s tests -q
python3.14 -m unittest discover -s tests -q
git diff --check 6858bb7..HEAD
git add -- apps/dashboard/app.js apps/dashboard/styles.css tests/test_dashboard_server.py tests/test_dashboard_ui_contract.py USER_OPERATING_GUIDE.md
git commit -m "feat: add dashboard task intake form"
```

### Task 4: Acceptance, integration and handoff

**Files:**
- Create: `artifacts/dispatches/20260812-008/verification.md`
- Modify: `handoff.md`

- [ ] **Step 1: Run a scope-bound Claude review**

Run `codex-claude-verify --tier normal --base-ref 6858bb7` with invariants: no browser task/Git execution; exact bounded request contract; no context/hash disclosure; existing intent boundary remains unchanged. Keep only the safe receipt in the task artifact.

- [ ] **Step 2: If and only if Claude returns explicit PASS, merge locally**

Use a no-fast-forward merge into local `main`; do not push, configure GitHub, delete worktrees or alter global verifier settings.

- [ ] **Step 3: Verify local main and record the handoff**

Run both Python suites, `git diff --check`, and `scripts/repo-health.sh` from `main`; record exact SHAs, tests, reviewer verdict and remaining boundary in `verification.md` and `handoff.md`.
