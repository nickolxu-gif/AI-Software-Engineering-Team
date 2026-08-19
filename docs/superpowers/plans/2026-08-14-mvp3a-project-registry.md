# MVP 3A Local Project Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manually maintained, local-only registry of up to 20 Git repositories to the existing control plane, then expose safe, isolated read-only summaries in a Dashboard Projects view. Codex alone registers and retires projects; the browser never creates, changes, scans, or opens projects.

**Architecture:** The current control repository owns the central SQLite registry in its existing Git-common-dir runtime. `ProjectRegistryService` validates a user-supplied local repository once, stores private canonical identity locally, and writes immutable registry events in the same transaction. `ProjectSnapshotReader` subsequently reads only allowlisted Git metadata and an optional target control database through non-mutating, read-only handles. `DashboardReadModel` publishes a public-field allowlist through one new GET endpoint; the vanilla browser renders and filters only that already-fetched data.

**Tech Stack:** Python standard library, SQLite, existing `RepoContext`/`ControlStore`, `http.server`, vanilla browser JavaScript, `unittest`, existing Claude V4 verifier.

---

### Task 1: Central registry contract, schema, and durable service

**Files:**
- Modify: `team_control/contracts.py`
- Modify: `team_control/store.py`
- Create: `team_control/project_registry.py`
- Modify: `tests/test_store.py`
- Create: `tests/test_project_registry.py`

- [ ] **Step 1: Write failing registry-contract and schema tests**

Create `tests/test_project_registry.py` with a temporary central repository plus independently created temporary Git repositories. Cover exact display-name validation, canonical identity uniqueness, the 20-active-project limit, retirement, and transactionally paired audit event:

```python
def test_register_writes_private_identity_and_audit_event_atomically(self):
    result = self.registry.register("LifeLogger", self.target_repo)
    entry = self.store.get_project_registry_entry(result["project_id"])
    events = self.store.list_project_registry_events(result["project_id"])
    self.assertEqual(entry["display_name"], "LifeLogger")
    self.assertEqual(events, [{"event_type": "PROJECT_REGISTERED", "project_id": result["project_id"], "created_at": entry["created_at"]}])
    self.assertNotIn("root_path", self.registry.safe_summary(entry))

def test_register_rejects_symlink_and_duplicate_project(self):
    with self.assertRaises(ContractError):
        self.registry.register("", self.target_repo)
    with self.assertRaises(BoundaryError):
        self.registry.register("Link", self.symlink_to_target)
    self.registry.register("One", self.target_repo)
    with self.assertRaises(ContractError):
        self.registry.register("Two", self.target_repo)
```

Add to `tests/test_store.py` assertions that both `project_registry` and `project_registry_events` are required tables after `initialize()`, so an older central database produces `SCHEMA_MIGRATION_REQUIRED` rather than being silently treated as compatible.

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python3 -m unittest tests.test_project_registry tests.test_store -q`

Expected: import failure for `team_control.project_registry`, then missing schema/store APIs.

- [ ] **Step 3: Add strict registry data contracts and schema**

In `team_control/contracts.py`, add constants and normalizers for a display name of 1–80 visible non-control characters and a local absolute input path. Reject booleans, non-strings, NUL/control characters, empty/whitespace-only values and unknown record fields. Do not reuse task-intake hashes or display names as paths.

Extend `SCHEMA` and `REQUIRED_SCHEMA_COLUMNS` in `team_control/store.py` with exactly these central-only tables:

```sql
CREATE TABLE IF NOT EXISTS project_registry (
    project_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL UNIQUE,
    common_dir_path TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
);
CREATE TABLE IF NOT EXISTS project_registry_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_registry(project_id),
    event_type TEXT NOT NULL CHECK (event_type IN ('PROJECT_REGISTERED', 'PROJECT_RETIRED')),
    created_at TEXT NOT NULL
);
```

Implement store methods using the existing lock/transaction helpers: `create_project_registry_entry`, `get_project_registry_entry`, `list_project_registry_entries(status=None, limit=20)`, `retire_project_registry_entry`, and `list_project_registry_events`. The create and retire helpers must insert their corresponding event in the same transaction; retirement updates only `status`, `updated_at`, and `retired_at`, never deletes rows. Bound all list limits to 20 and order active entries by `created_at, project_id` for stable sampling.

Implement `ProjectRegistryService` with `register(display_name, raw_root)` and `retire(project_id)`. Before discovery, reject a supplied symbolic-link path with `lstat`; resolve it strictly, require a directory, call `RepoContext.discover`, and record both `context.root` and `context.common_dir`. Re-resolve/lstat those identities immediately before persistence and reject any mismatch. The service must never call `ControlStore.for_repo()` for the target repository, `initialize()`, a target migration, Git write command, or a target SQLite write.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_project_registry tests.test_store -q`

Expected: `OK`; verify failed registration leaves neither registry entry nor event behind.

- [ ] **Step 5: Commit the registry increment**

```bash
git add -- team_control/contracts.py team_control/store.py team_control/project_registry.py tests/test_store.py tests/test_project_registry.py
git commit -m "feat: add local project registry"
```

### Task 2: Codex-only operational commands

**Files:**
- Modify: `team_control/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `USER_OPERATING_GUIDE.md`

- [ ] **Step 1: Write failing command-boundary tests**

Add `projects register`, `projects retire`, and `projects list` tests to `tests/test_cli.py`. The central `--repo` remains mandatory; `register` accepts only `--display-name` and `--path`, `retire` accepts only `--project-id`, and `list` accepts no target path. Verify JSON output is a safe summary and never includes `root_path` or `common_dir_path`.

```python
def test_projects_register_uses_central_repo_and_hides_paths(self):
    payload = self.run_cli(
        ["--repo", str(self.repo), "projects", "register",
         "--display-name", "Example", "--path", str(self.target_repo)]
    )
    self.assertEqual(set(payload), {"project_id", "display_name", "status", "created_at", "updated_at"})
    self.assertNotIn(str(self.target_repo), json.dumps(payload))

def test_projects_has_no_scan_or_browser_style_mutation_commands(self):
    self.assertNotIn("scan", cli.COMMAND_USAGE["projects"])
    with self.assertRaises(ContractError):
        cli.main(["--repo", str(self.repo), "projects", "delete"])
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python3 -m unittest tests.test_cli -q`

Expected: parser rejects the nested `projects` command because it is not implemented yet.

- [ ] **Step 3: Implement a narrow nested `projects` CLI**

In `team_control/cli.py`, add the top-level command token `projects` and a required subcommand parser with only `register`, `retire`, and `list`. `execute()` must initialize/check only the central store selected by `--repo`, construct `ProjectRegistryService(context, store)`, and emit only its safe results. `projects list` returns `{"projects": [...]}` for active entries only; `retire` returns the safe retired entry. No command may enumerate a parent directory, call `git remote`, read source files, or print an exception path.

Update `USER_OPERATING_GUIDE.md` with the three exact commands, for example:

```bash
python3 -m team_control.cli --repo "$PWD" projects register \
  --display-name "LifeLogger" --path "/absolute/local/git/repository"
```

Document that this is a Codex-operated allowlist action, not a browser control, and that it does not initialize the target’s control plane.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_cli -q`

Expected: `OK`; invalid or unavailable target paths produce structured errors without creating target runtime directories.

- [ ] **Step 5: Commit the CLI increment**

```bash
git add -- team_control/cli.py tests/test_cli.py USER_OPERATING_GUIDE.md
git commit -m "feat: add Codex project registry commands"
```

### Task 3: Isolated read-only target sampler and public read model

**Files:**
- Modify: `team_control/project_registry.py`
- Modify: `team_control/dashboard_read_model.py`
- Modify: `tests/test_project_registry.py`
- Modify: `tests/test_dashboard_read_model.py`

- [ ] **Step 1: Write failing safety and isolation tests**

Add tests for five target outcomes: `HEALTHY`, `UNINITIALIZED`, `UNAVAILABLE`, `UNSUPPORTED`, and `IDENTITY_MISMATCH`. Use a target with an initialized compatible control database only for `HEALTHY`; use an ordinary Git repository for `UNINITIALIZED`; simulate a malformed or missing required target table for `UNSUPPORTED`; and alter the stored root/common-dir identity for `IDENTITY_MISMATCH`. Capture target control-db bytes before and after every sampler call.

```python
def test_sampler_is_read_only_and_one_bad_project_does_not_hide_the_next(self):
    healthy = self.register_compatible_target("Healthy")
    broken = self.register_unavailable_target("Broken")
    target_digest = self.digest(healthy.control_db)
    cards = self.model.projects()
    self.assertEqual([card["display_name"] for card in cards["items"]], ["Healthy", "Broken"])
    self.assertEqual(cards["items"][0]["control_status"], "HEALTHY")
    self.assertEqual(cards["items"][1]["control_status"], "UNAVAILABLE")
    self.assertEqual(self.digest(healthy.control_db), target_digest)

def test_projects_public_payload_has_an_exact_allowlist(self):
    item = self.model.projects()["items"][0]
    self.assertEqual(set(item), {
        "project_id", "display_name", "registry_status", "sampled_at",
        "head_sha", "control_status", "task_counts", "latest_task_updated_at",
    })
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python3 -m unittest tests.test_project_registry tests.test_dashboard_read_model -q`

Expected: no `projects()` model method and no sampler result contract.

- [ ] **Step 3: Implement a non-mutating sampler**

Add `ProjectSnapshotReader` to `team_control/project_registry.py`. It receives only a central registry entry; it must lstat/resolve the stored root and common directory, rediscover the repository, and compare both exact canonical identities before reading. Identity failure returns an `IDENTITY_MISMATCH` card and does not bind to the new location.

For an identity-valid target, execute only the existing allowlisted `git rev-parse HEAD` prefix with `GIT_OPTIONAL_LOCKS=0`, disabled global/system config, terminal prompts disabled, a fixed subprocess timeout, and no remote/worktree/source-file command. Locate the target database as `<common-dir>/team/runtime/team.db` with lstat checks; do not create parent directories. Open it by URI with `mode=ro`, `PRAGMA query_only=ON`, an SQLite authorizer that denies attach/detach and mutation, and a bounded busy timeout. Validate a target-read schema that is explicitly limited to the already-existing task/agent/review/blocker/intent/intake tables; it must not require the new central registry tables. Query only state counts and `MAX(updated_at)` with fixed SQL.

Map errors to public statuses without raw exception text or paths. A failed Git HEAD becomes `HEAD_UNAVAILABLE`; a missing target database becomes `UNINITIALIZED`; a malformed/old schema becomes `UNSUPPORTED`; inaccessible, timeout, busy, or other bounded read failure becomes `UNAVAILABLE`. Return zero task counts only for a successfully read compatible empty database, never for a failure state.

In `DashboardReadModel`, instantiate the registry service from the existing central `context`/`store` and add `projects()`. It calls `list_active` once, samples at most 20 records in stable order, and returns only the exact public allowlist above plus top-level `{"items": ..., "count": ...}`. No target return value may carry a path, remote, branch, objective, context, evidence, nonce, error, or provider text.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_project_registry tests.test_dashboard_read_model -q`

Expected: `OK`; prove the sampler neither creates target `team/runtime` nor changes any target database/WAL files.

- [ ] **Step 5: Commit the read-model increment**

```bash
git add -- team_control/project_registry.py team_control/dashboard_read_model.py tests/test_project_registry.py tests/test_dashboard_read_model.py
git commit -m "feat: sample registered projects read-only"
```

### Task 4: Read-only HTTP endpoint and Dashboard Projects view

**Files:**
- Modify: `team_control/dashboard_server.py`
- Modify: `apps/dashboard/index.html`
- Modify: `apps/dashboard/app.js`
- Modify: `apps/dashboard/styles.css`
- Modify: `tests/test_dashboard_server.py`
- Modify: `tests/test_dashboard_ui_contract.py`
- Modify: `tests/test_dashboard_end_to_end.py`

- [ ] **Step 1: Write failing HTTP and browser-boundary tests**

Add `/api/projects` tests for GET/HEAD/OPTIONS success and unknown query rejection. Assert `POST`, `PUT`, `PATCH`, and `DELETE` remain `405 READ_ONLY`, no `/api/projects/register` or `/api/projects/delete` route exists, and JSON text cannot contain a registered path or `common_dir_path`.

```python
def test_projects_endpoint_is_public_field_only_and_read_only(self):
    response, payload, body = self.request("GET", "/api/projects")
    self.assertEqual(response.status, 200)
    self.assertEqual(set(payload["data"]), {"items", "count"})
    self.assertNotIn(str(self.registered_target), body.decode("utf-8"))
    response, payload, _ = self.request("POST", "/api/projects")
    self.assertEqual((response.status, payload["error"]["code"]), (405, "READ_ONLY"))

def test_dashboard_assets_have_projects_view_without_registration_controls(self):
    self.assertIn('data-view="projects"', HTML.read_text(encoding="utf-8"))
    self.assertIn("/api/projects", JS.read_text(encoding="utf-8"))
    for forbidden in ("/api/projects/register", "showDirectoryPicker", "root_path", "common_dir_path"):
        self.assertNotIn(forbidden, JS.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python3 -m unittest tests.test_dashboard_server tests.test_dashboard_ui_contract tests.test_dashboard_end_to_end -q`

Expected: `/api/projects` is currently not found and the Projects navigation button is absent.

- [ ] **Step 3: Add one GET-only route and bounded UI**

In `team_control/dashboard_server.py`, recognize only `GET`, `HEAD`, and validated `OPTIONS /api/projects`, with no query parameters, and dispatch it to `model.projects()`. Keep success envelopes, Host/Origin validation, security headers, and all existing intent/task-intake behavior unchanged. Do not add a POST handler.

In `apps/dashboard/index.html`, add a `data-view="projects"` navigation button. In `apps/dashboard/app.js`, fetch `/api/projects` in the same refresh generation as `/api/project`, `/api/tasks`, and `/api/approvals`; require matching central `source_head_sha`; save only `projects.data.items`. Add a count to the overview and render a Projects card list with a local text input that filters only display name, public state labels, and already-returned task counts. Cards display display name, safe status, short HEAD, task counts, sample time, and “请回到 Codex 中继续”; do not add project action buttons or target-detail links. Add only the CSS needed for bounded cards and responsive filters.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_dashboard_server tests.test_dashboard_ui_contract tests.test_dashboard_end_to_end -q`

Expected: `OK`; browser refresh still works when one registered project is unavailable and existing single-project views are unchanged.

- [ ] **Step 5: Commit the dashboard increment**

```bash
git add -- team_control/dashboard_server.py apps/dashboard/index.html apps/dashboard/app.js apps/dashboard/styles.css tests/test_dashboard_server.py tests/test_dashboard_ui_contract.py tests/test_dashboard_end_to_end.py
git commit -m "feat: show registered projects in dashboard"
```

### Task 5: Documentation, verification, acceptance, and local integration

**Files:**
- Modify: `USER_OPERATING_GUIDE.md`
- Create: `artifacts/dispatches/20260814-001/verification.md`
- Modify: `handoff.md`

- [ ] **Step 1: Complete the operator-facing boundary guide**

Document: registration/retirement/listing only through Codex’s local command; path privacy; 20-project cap; each health status and its non-remedial meaning; local browser filtering; and the explicit exclusions (no scan, GitHub, remote, target initialization, cross-project writes, credentials, or browser project management). Include a recovery note: `UNINITIALIZED`, `UNSUPPORTED`, and `UNAVAILABLE` are status signals to investigate through Codex, never browser repair commands.

- [ ] **Step 2: Run all local verification from the candidate worktree**

```bash
git diff --check c2db9dfec1302716b420553ff6837955c208d819..HEAD
python3 -m unittest discover -s tests -q
python3.14 -m unittest discover -s tests -q
./scripts/repo-health.sh
```

Expected: diff check clean, both complete suites pass, health reports `PASS`. Record exact command outcomes and candidate SHA in `artifacts/dispatches/20260814-001/verification.md`; do not record absolute paths or raw provider output.

- [ ] **Step 3: Request scope-bound Claude V4 final acceptance**

Run the existing verifier in strict V4 safe mode against the final diff from `c2db9dfec1302716b420553ff6837955c208d819`. Use immutable packets no larger than 60 KB, split by safety boundary as needed, and require strict JSON `PASS` for every packet. Invariants: explicit allowlist only; no target initialization/migration/write; target identity checked on every sample; no path/remote/context/evidence/raw errors reach browser; no project browser mutation route; failure is isolated and not represented as healthy/empty; prior Dashboard endpoints remain bounded. Persist only the safe receipt/report reference under the dispatch artifact.

- [ ] **Step 4: Gate, integrate locally, and verify main only if every acceptance gate passes**

Only if all tests, health, and Claude’s strict `PASS` are present, update task state to the approved acceptance state, run the MiMo inventory and Codex decision record, merge the branch with a no-fast-forward local merge to `main`, and rerun:

```bash
python3 -m unittest discover -s tests -q
python3.14 -m unittest discover -s tests -q
./scripts/repo-health.sh
```

Record the merge SHA, test evidence, accepted reviewer receipt, known exclusions, and user operating path in `handoff.md` and `verification.md`. Do not configure a remote, push, release, delete a worktree, alter global verifier settings, or call a fallback reviewer. If any verifier packet is empty, malformed, warning-only, blocked, or non-PASS, record `BLOCKED` and leave main untouched.
