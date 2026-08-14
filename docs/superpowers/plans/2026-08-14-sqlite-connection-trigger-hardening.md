# SQLite Connection and Trigger Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close remaining MVP 2D SQLite connection and trigger threat-model gaps without changing browser capabilities.

**Architecture:** Configure every `ControlStore` connection with an SQLite authorizer that denies database attachment and detachment. Preflight rejects all explicit persistent triggers and all temporary triggers on the active connection; this conservative policy avoids fragile SQL-text parsing. Explicit indexes remain checked only for the intake tables because indexes do not execute writes.

**Tech Stack:** Python standard-library `sqlite3`; `unittest`; SQLite authorizer and catalog views.

---

## Dispatch seven questions

1. **Purpose:** Prevent hidden SQLite connection state or trigger side effects from changing task-intake records outside the verified schema contract.
2. **Done:** `ATTACH`/`DETACH` are denied on every connection; persistent or temporary triggers return `SCHEMA_UNSUPPORTED`; no browser API, migration auto-repair, merge, push, or release is added.
3. **Proof:** Dual Python full suites pass; focused tests prove attach/detach rejection and trigger rejection; Claude returns strict `PASS` for a fresh immutable delta packet.
4. **Anti-shortcut:** Do not parse trigger SQL, ignore TEMP triggers, silently drop trigger/residue data, or convert `PASS_WITH_WARNINGS` to acceptance.
5. **Bounds:** Change only `team_control/store.py`, `tests/test_store.py`, `tests/test_task_intakes.py`, and the MVP 2D spec. Stop if a prior accepted feature requires a trigger/attached database, or if recovery/migration is required.
6. **Trade-off:** Prefer `SCHEMA_UNSUPPORTED` to executing under unknown trigger state; preserve the no-direct-SQLite-repair rule.
7. **Unknowns:** If the environment needs triggers or attached databases, record evidence and stop `BLOCKED`; do not whitelist ad hoc.

### Task 1: Deny attached database state on all connections

**Files:**

- Modify: `team_control/store.py:677-680` and `team_control/store.py:743-760`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_control_store_connections_deny_attach_and_detach(self):
    with tempfile.TemporaryDirectory() as tmp:
        store, _ = self.make_store(Path(tmp))
        store.initialize()
        with store.mutation() as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("ATTACH DATABASE ':memory:' AS outside")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DETACH DATABASE main")
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_store.StoreTests.test_control_store_connections_deny_attach_and_detach -v`

Expected: `ATTACH` succeeds before the authorizer exists.

- [ ] **Step 3: Implement the shared connection setup**

```python
@staticmethod
def _deny_database_attachment(action, argument1, argument2, database, source):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

@classmethod
def _configure_connection(cls, connection):
    connection.row_factory = sqlite3.Row
    connection.set_authorizer(cls._deny_database_attachment)
    return connection
```

Use `_configure_connection()` in `_connect()` and `read_connection()` before their pragmas; keep foreign-key and query-only settings unchanged.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python3 -m unittest tests.test_store -q`

Expected: `OK`.

```bash
git add team_control/store.py tests/test_store.py
git commit -m "fix: deny attached control store databases"
```

### Task 2: Make trigger preflight connection-complete

**Files:**

- Modify: `team_control/store.py:_validate_task_intake_schema_objects`
- Test: `tests/test_task_intakes.py`

- [ ] **Step 1: Write failing tests**

```python
def test_current_task_intake_schema_rejects_unrelated_persistent_trigger(self):
    with self.store.mutation() as connection:
        connection.execute(
            "CREATE TRIGGER unrelated_trigger AFTER INSERT ON tasks BEGIN SELECT 1; END"
        )
    with self.assertRaises(SchemaUnsupportedError):
        self.store.require_schema_compatible()

def test_schema_preflight_rejects_temporary_trigger_on_active_connection(self):
    with self.store.mutation() as connection:
        connection.execute(
            "CREATE TEMP TRIGGER temporary_trigger AFTER INSERT ON tasks BEGIN SELECT 1; END"
        )
        with self.assertRaises(SchemaUnsupportedError):
            self.store._require_schema_compatible_in_connection(connection)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_task_intakes.TaskIntakeTests.test_current_task_intake_schema_rejects_unrelated_persistent_trigger tests.test_task_intakes.TaskIntakeTests.test_schema_preflight_rejects_temporary_trigger_on_active_connection -v`

Expected: both fail because current preflight searches only selected main-database trigger SQL.

- [ ] **Step 3: Implement catalog-only checks**

```python
persistent = connection.execute(
    "SELECT name FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL"
).fetchall()
temporary = connection.execute(
    "SELECT name FROM sqlite_temp_master WHERE type = 'trigger'"
).fetchall()
if persistent or temporary:
    raise SchemaUnsupportedError("control database has unsupported triggers")
```

Retain the existing explicit-index check only for `task_intake_requests` and `task_intake_handlings`. Do not inspect or parse trigger bodies.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python3 -m unittest tests.test_task_intakes -q`

Expected: `OK`.

```bash
git add team_control/store.py tests/test_task_intakes.py
git commit -m "fix: reject untrusted control store triggers"
```

### Task 3: Document and independently accept the bounded change

**Files:**

- Modify: `docs/superpowers/specs/2026-08-12-mvp2d-task-intake-design.md`
- Verify: `tests/`

- [ ] **Step 1: Update the contract**

Document that every `ControlStore` connection denies `ATTACH/DETACH`; persistent and temporary triggers are unsupported; violations return `SCHEMA_UNSUPPORTED` without cleanup; browser capabilities remain unchanged.

- [ ] **Step 2: Run deterministic verification**

```bash
git diff --check
python3 -m unittest discover -s tests -q
/opt/homebrew/bin/python3.14 -m unittest discover -s tests -q
```

Expected: all commands exit `0`.

- [ ] **Step 3: Commit and call Claude**

```bash
git add docs/superpowers/specs/2026-08-12-mvp2d-task-intake-design.md
git commit -m "docs: define control store trigger boundary"
```

Build a new immutable V4 packet limited to this plan's commits. Accept only strict JSON `PASS`; `PASS_WITH_WARNINGS`, errors, or missing verdict remain `BLOCKED`. Do not merge, push, or invoke a fallback reviewer.

## Plan self-review

- Scope coverage: Task 1 hardens connection attachment state; Task 2 handles persistent and temporary triggers without SQL parsing; Task 3 defines and verifies the boundary.
- Placeholder scan: every implementation task has exact files, assertions, commands, expected result, and commit scope.
- Type consistency: authorizer returns `sqlite3.SQLITE_DENY`/`sqlite3.SQLITE_OK`; catalog queries run only on the active configured connection.
