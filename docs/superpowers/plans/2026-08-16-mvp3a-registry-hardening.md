# MVP 3A Registry Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final MVP 3A registry hardening findings without expanding write authority or exposing target-repository data.

**Architecture:** Keep all project-registry reads bounded and cursor-based. Validate cursors as RFC3339 timestamps plus UUIDs. Migrate registry tables with set-based SQLite copies inside the existing transaction after explicitly rejecting orphan audit rows.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`.

---

### Task 1: Fail closed for malformed event cursors and paginate registry entries

**Files:**
- Modify: `team_control/store.py`
- Modify: `tests/test_project_registry.py`

- [x] **Step 1: Write failing tests**

```python
with self.assertRaises(ContractError):
    self.store.list_project_registry_events(cursor={
        "created_at": "not-a-timestamp", "event_id": valid_event_id,
    })
first = self.store.list_project_registry_entries_page(limit=20)
second = self.store.list_project_registry_entries_page(
    limit=20, cursor=first["next_cursor"],
)
```

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest -v tests.test_project_registry`

Expected: the malformed cursor is accepted and the entry-page API is absent.

- [x] **Step 3: Implement minimal bounded APIs**

```python
def _validate_project_registry_event_cursor(self, cursor):
    # require exactly created_at/event_id, RFC3339 created_at and UUID event_id

def list_project_registry_entries_page(self, status=None, limit=20, cursor=None):
    # query limit + 1 ordered by created_at, project_id
```

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest -v tests.test_project_registry`

Expected: all registry tests pass.

### Task 2: Make legacy migration bounded and classified

**Files:**
- Modify: `team_control/store.py`
- Modify: `tests/test_store.py`

- [x] **Step 1: Write failing tests**

```python
# Insert an orphan legacy event with foreign_keys disabled.
with self.assertRaises(SchemaUnsupportedError):
    store.initialize()
```

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest -v tests.test_store.StoreTests.test_init_rejects_legacy_registry_orphan_event`

Expected: raw `sqlite3.IntegrityError` or no classified error before the implementation.

- [x] **Step 3: Implement set-based migration**

```python
# reject orphan rows with NOT EXISTS before DDL changes
# INSERT INTO project_registry_migrated (...) SELECT ... FROM project_registry
# INSERT INTO project_registry_events_migrated (...) SELECT ... FROM project_registry_events
```

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest -v tests.test_store`

Expected: all store tests pass; migration no longer calls `fetchall()` for registry tables.

### Task 3: Full verification and independent acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-08-16-mvp3a-registry-hardening.md` (checklist only)

- [x] **Step 1: Run both full suites**

Run: `python3 -m unittest discover -s tests -q` and `python3.14 -m unittest discover -s tests -q`

Expected: both exit 0.

- [x] **Step 2: Verify staged scope**

Run: `git diff --check` and `git diff --cached --check`

Expected: no whitespace errors and no changes outside the listed files and review evidence.

- [ ] **Step 3: Run fresh immutable Claude V4 review packets**

Expected: every package emits strict JSON `PASS`; warning, error, empty output, or no verdict blocks integration.
