# MVP 1 Read-Only Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a localhost-only, exception-first dashboard that reads the MVP 0 Git/SQLite control plane without creating a second write path.

**Architecture:** A Python standard-library HTTP service exposes seven allowlisted JSON endpoints backed by one read-only SQLite snapshot per request and an exact Git command whitelist. A same-origin vanilla HTML/CSS/JavaScript client renders overview, tasks, agents, approvals, and evidence; all engineering actions route back to Codex.

**Tech Stack:** Python 3 standard library (`sqlite3`, `http.server`, `json`, `subprocess`, `unittest`), POSIX shell wrapper, semantic HTML, CSS, vanilla JavaScript.

---

## Scope and file map

Create:

- `team_control/dashboard_read_model.py` — pagination, SQLite snapshot queries, output allowlists, exception ordering, Git observations.
- `team_control/dashboard_server.py` — localhost HTTP server, routing, security headers, static file map, JSON envelopes.
- `team_control/dashboard_main.py` — deterministic dashboard process entry point and structured startup output.
- `apps/dashboard/index.html` — semantic single-page application shell.
- `apps/dashboard/styles.css` — exception-first desktop layout and responsive states.
- `apps/dashboard/app.js` — GET-only API client, refresh timer, routing, rendering, stale/error states.
- `scripts/open-team-dashboard` — repository-bound wrapper used by Codex.
- `tests/test_dashboard_read_model.py` — query, schema, sorting, pagination, WAL, and side-effect tests.
- `tests/test_dashboard_server.py` — route, method, Host/Origin, static map, and response tests.
- `tests/test_dashboard_ui_contract.py` — static UI contract and GET-only browser-code checks.
- `tests/test_dashboard_end_to_end.py` — real temporary repository and HTTP vertical slice.
- `artifacts/dispatches/20260809-001/verification.md` — final commands and observed results.
- `artifacts/dispatches/20260809-001/mimo-inventory.md` — post-implementation inventory.

Modify:

- `team_control/git_context.py` — optional environment overrides for argv-only subprocesses.
- `team_control/store.py` — harden the existing read-only connection.
- `.agents/skills/ai-software-engineering-team/SKILL.md` — map the natural-language open-dashboard intent.
- `USER_OPERATING_GUIDE.md` — replace MVP 0-only dashboard statements with actual MVP 1 usage and troubleshooting.
- `tests/test_git_context.py` — subprocess environment tests.
- `tests/test_store.py` — `query_only`, WAL, and no-write tests.
- `tests/test_skill_contract.py` — dashboard Skill and guide contract.
- `handoff.md` — only after integration, record accepted state, SHA, command, tests, Review, and residual limits.

Do not modify GitHub remotes, remote branches, cloud configuration, MVP 2/3 adapters, authentication, or production settings.

### Task 1: Harden read-only process and SQLite primitives

**Files:**

- Modify: `team_control/git_context.py`
- Modify: `team_control/store.py`
- Modify: `tests/test_git_context.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for environment overrides and SQLite query-only mode**

Add to `tests/test_git_context.py`:

```python
import os


def test_run_argv_applies_explicit_environment_overrides(self):
    with tempfile.TemporaryDirectory() as tmp:
        completed = run_argv(
            [
                "python3",
                "-c",
                "import os; print(os.environ['DASHBOARD_TEST_FLAG'])",
            ],
            Path(tmp),
            env_overrides={"DASHBOARD_TEST_FLAG": "readonly"},
        )
        self.assertEqual(completed.stdout.strip(), "readonly")
        self.assertNotIn("DASHBOARD_TEST_FLAG", os.environ)


def test_run_argv_rejects_non_string_environment_overrides(self):
    with tempfile.TemporaryDirectory() as tmp:
        with self.assertRaises(BoundaryError):
            run_argv(
                ["git", "--version"],
                Path(tmp),
                env_overrides={"GIT_OPTIONAL_LOCKS": 0},
            )
```

Add to `tests/test_store.py`:

```python
def test_read_connection_is_query_only(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(Path(tmp) / "repo")
        store = ControlStore.for_repo(RepoContext.discover(repo))
        store.initialize()

        with store.read_connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA query_only").fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "UPDATE tasks SET title = 'changed' WHERE 1 = 0"
                )


def test_read_connection_uses_two_second_busy_timeout(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(Path(tmp) / "repo")
        store = ControlStore.for_repo(RepoContext.discover(repo))
        store.initialize()
        with store.read_connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0],
                2000,
            )
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest \
  tests.test_git_context.GitContextTests.test_run_argv_applies_explicit_environment_overrides \
  tests.test_git_context.GitContextTests.test_run_argv_rejects_non_string_environment_overrides \
  tests.test_store.StoreTests.test_read_connection_is_query_only \
  tests.test_store.StoreTests.test_read_connection_uses_two_second_busy_timeout -v
```

Expected: failures because `run_argv` has no `env_overrides`, `query_only` is `0`, and busy timeout is `5000`.

- [ ] **Step 3: Add explicit subprocess environment overrides**

Replace `run_argv` in `team_control/git_context.py` with:

```python
def run_argv(argv, cwd, check=True, env_overrides=None):
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not all(isinstance(value, str) for value in argv)
    ):
        raise BoundaryError(
            "subprocess arguments must be a non-empty string argv"
        )
    overrides = {} if env_overrides is None else env_overrides
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in overrides.items()
    ):
        raise BoundaryError("environment overrides must be string pairs")
    environment = dict(os.environ)
    environment.update(overrides)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise GitStateError("command failed to start: %s" % exc) from exc
    if check and completed.returncode != 0:
        raise GitStateError(
            "command failed (%s): %s"
            % (completed.returncode, completed.stderr.strip())
        )
    return completed
```

- [ ] **Step 4: Harden `ControlStore.read_connection`**

Replace the method in `team_control/store.py` with:

```python
@contextmanager
def read_connection(self):
    self._validate_repo_paths()
    uri = self.path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 2000")
    try:
        yield connection
    finally:
        connection.close()
```

- [ ] **Step 5: Run focused and full regression tests**

Run:

```bash
python3 -m unittest tests.test_git_context tests.test_store -v
python3 -m unittest discover -s tests -v
```

Expected: both commands exit `0`; existing MVP 0 tests remain green.

- [ ] **Step 6: Commit Task 1**

```bash
git add team_control/git_context.py team_control/store.py \
  tests/test_git_context.py tests/test_store.py
git diff --cached --check
git commit -m "feat(dashboard): harden readonly primitives [20260809-001]"
```

### Task 2: Build the read-model foundation and Git whitelist

**Files:**

- Create: `team_control/dashboard_read_model.py`
- Create: `tests/test_dashboard_read_model.py`

- [ ] **Step 1: Write failing tests for pagination, WAL sidecars, and exact Git commands**

Create `tests/test_dashboard_read_model.py` with the initial cases:

```python
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from team_control.dashboard_read_model import (
    DashboardInputError,
    DashboardReadModel,
    DashboardUnavailableError,
    parse_pagination,
)
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class DashboardReadModelTests(unittest.TestCase):
    def make_model(self, root):
        repo = make_repo(root / "repo")
        context = RepoContext.discover(repo)
        store = ControlStore.for_repo(context)
        store.initialize()
        return repo, store, DashboardReadModel(context, store)

    def test_parse_pagination_applies_defaults_and_caps(self):
        self.assertEqual(parse_pagination({}, 50, 100), (50, 0))
        self.assertEqual(
            parse_pagination({"limit": ["100"], "offset": ["25"]}, 50, 100),
            (100, 25),
        )
        for query in (
            {"limit": ["101"]},
            {"limit": ["-1"]},
            {"offset": ["10001"]},
            {"offset": ["text"]},
        ):
            with self.subTest(query=query):
                with self.assertRaises(DashboardInputError):
                    parse_pagination(query, 50, 100)

    def test_non_empty_wal_requires_readable_regular_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            wal = Path(str(store.path) + "-wal")
            shm = Path(str(store.path) + "-shm")
            wal.write_bytes(b"active")
            if shm.exists():
                shm.unlink()
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "WAL_SIDECAR_UNAVAILABLE")

    def test_git_observation_does_not_refresh_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            index = repo / ".git" / "index"
            before_hash = hashlib.sha256(index.read_bytes()).hexdigest()
            before_mtime = index.stat().st_mtime_ns
            observed = model.project()
            self.assertEqual(observed["head_sha"], run(
                ["git", "rev-parse", "HEAD"], repo
            ).stdout.strip())
            self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), before_hash)
            self.assertEqual(index.stat().st_mtime_ns, before_mtime)
```

- [ ] **Step 2: Run the new test module and verify import failure**

Run:

```bash
python3 -m unittest tests.test_dashboard_read_model -v
```

Expected: `ModuleNotFoundError: team_control.dashboard_read_model`.

- [ ] **Step 3: Implement errors, pagination, sidecar validation, and Git whitelist**

Create `team_control/dashboard_read_model.py` with these public foundations:

```python
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import TeamControlError
from .git_context import canonical_under, run_argv


class DashboardError(TeamControlError):
    code = "DASHBOARD_ERROR"


class DashboardInputError(DashboardError):
    code = "INVALID_REQUEST"


class DashboardNotFoundError(DashboardError):
    code = "TASK_NOT_FOUND"


class DashboardUnavailableError(DashboardError):
    code = "DASHBOARD_UNAVAILABLE"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


READONLY_GIT_PREFIX = (
    "git",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "maintenance.auto=false",
)
ALLOWED_GIT_COMMANDS = frozenset({
    ("rev-parse", "--show-toplevel"),
    ("rev-parse", "--git-common-dir"),
    ("rev-parse", "HEAD"),
    ("symbolic-ref", "--quiet", "--short", "HEAD"),
    ("status", "--porcelain=v1", "--untracked-files=no", "--ignore-submodules=all"),
    ("worktree", "list", "--porcelain"),
    ("remote",),
})
REQUIRED_SCHEMA = {
    "tasks": {"dispatch_id", "title", "objective", "risk_level", "state", "current_head_sha"},
    "events": {"dispatch_id", "sequence", "event_type", "payload_json", "created_at"},
    "approvals": {"approval_id", "dispatch_id", "action", "target_sha", "status"},
    "agents": {"dispatch_id", "agent_id", "role", "state", "report_json"},
    "reviews": {"review_id", "dispatch_id", "disposition", "source_sha"},
    "blockers": {"blocker_id", "dispatch_id", "reason", "status"},
    "evidence": {"evidence_id", "dispatch_id", "kind", "path", "source_sha"},
}


def parse_pagination(query, default_limit, maximum_limit):
    unknown = set(query) - {"limit", "offset"}
    if unknown:
        raise DashboardInputError("unknown pagination parameter")
    try:
        limit = int(query.get("limit", [str(default_limit)])[0])
        offset = int(query.get("offset", ["0"])[0])
    except (TypeError, ValueError, IndexError) as error:
        raise DashboardInputError("pagination must use integers") from error
    if limit < 0 or limit > maximum_limit or offset < 0 or offset > 10000:
        raise DashboardInputError("pagination is outside the allowed range")
    return limit, offset


class DashboardReadModel:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def _git(self, command, cwd=None, check=True):
        command = tuple(command)
        if command not in ALLOWED_GIT_COMMANDS:
            raise DashboardInputError("Git command is not allowlisted")
        return run_argv(
            [*READONLY_GIT_PREFIX, *command],
            self.context.root if cwd is None else cwd,
            check=check,
            env_overrides={"GIT_OPTIONAL_LOCKS": "0"},
        )

    def source_head_sha(self):
        return self._git(("rev-parse", "HEAD")).stdout.strip()

    def repository_id(self):
        encoded = str(self.context.common_dir).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_wal_sidecars(self):
        database = canonical_under(self.context.common_dir, self.store.path)
        wal = Path(str(database) + "-wal")
        shm = Path(str(database) + "-shm")
        if wal.is_file() and wal.stat().st_size > 0:
            for candidate in (wal, shm):
                if candidate.is_symlink() or not candidate.is_file():
                    raise DashboardUnavailableError(
                        "active WAL sidecars are unavailable",
                        code="WAL_SIDECAR_UNAVAILABLE",
                    )
                canonical_under(self.context.common_dir, candidate)

    def _validate_schema(self, connection):
        for table, required in REQUIRED_SCHEMA.items():
            rows = connection.execute("PRAGMA table_info(%s)" % table).fetchall()
            observed = {row["name"] for row in rows}
            if not required.issubset(observed):
                raise DashboardUnavailableError(
                    "control database schema is unsupported",
                    code="SCHEMA_UNSUPPORTED",
                )

    @contextmanager
    def snapshot(self):
        self._validate_wal_sidecars()
        try:
            with self.store.read_connection() as connection:
                connection.execute("BEGIN")
                self._validate_schema(connection)
                yield connection
                connection.rollback()
        except sqlite3.OperationalError as error:
            raise DashboardUnavailableError(
                "control database is busy or unavailable",
                code="DATABASE_BUSY",
            ) from error

    def health(self):
        self._validate_wal_sidecars()
        with self.snapshot() as connection:
            connection.execute("SELECT 1").fetchone()
        return {
            "status": "HEALTHY",
            "database": "AVAILABLE",
            "schema": "SUPPORTED",
            "git": "AVAILABLE",
            "warnings": [],
        }
```

- [ ] **Step 4: Run the focused tests and adjust only verified defects**

Run:

```bash
python3 -m unittest tests.test_dashboard_read_model -v
```

Expected: pagination and sidecar tests pass; project test still fails because `project()` is not implemented.

- [ ] **Step 5: Commit the foundation**

```bash
git add team_control/dashboard_read_model.py tests/test_dashboard_read_model.py
git diff --cached --check
git commit -m "feat(dashboard): add readonly read model foundation [20260809-001]"
```

### Task 3: Implement project, task, agent, approval, event, and evidence queries

**Files:**

- Modify: `team_control/dashboard_read_model.py`
- Modify: `tests/test_dashboard_read_model.py`

- [ ] **Step 1: Add failing API-shape tests**

Add tests that create two tasks and assert explicit fields only:

```python
def test_tasks_are_exception_first_and_field_allowlisted(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo, store, model = self.make_model(Path(tmp))
        control = ControlPlane(RepoContext.discover(repo), store)
        control.create_task("20260809-010", "Normal", "Normal task", "L1")
        control.create_task("20260809-011", "Blocked", "Blocked task", "L2")
        store.transition("20260809-011", "DISPATCHED", "dispatch")
        store.transition("20260809-011", "IN_PROGRESS", "start")
        store.add_blocker("20260809-011", "test blocker", "Codex", "fix test")

        result = model.tasks({}, state=None, risk=None, attention=None, search=None)
        self.assertEqual(result["items"][0]["dispatch_id"], "20260809-011")
        self.assertEqual(
            set(result["items"][0]),
            {
                "dispatch_id", "title", "objective", "risk_level", "state",
                "effective_state", "owner", "agent", "updated_at",
                "current_head_sha", "attention_reasons",
            },
        )


def test_approvals_never_expose_hashes_or_idempotency_keys(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo, store, model = self.make_model(Path(tmp))
        control = ControlPlane(RepoContext.discover(repo), store)
        task = control.create_task("20260809-012", "Approval", "Approval task", "L2")
        control.request_approval(
            task["dispatch_id"], "external_action", task["current_head_sha"],
            {"scope": "test"}, "dashboard-test-nonce-000001", 10,
        )
        result = model.approvals({})
        item = result["items"][0]
        self.assertNotIn("nonce_hash", item)
        self.assertNotIn("request_hash", item)
        self.assertNotIn("idempotency_key", item)


def test_unknown_event_payload_is_not_returned(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo, store, model = self.make_model(Path(tmp))
        with store.mutation() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO tasks VALUES (?, 1, ?, ?, 'L1', 'PLANNED', NULL, ?, ?, 'Codex', NULL, NULL, NULL, NULL, ?, ?)",
                ("20260809-013", "Unknown event", "Test", "a" * 40, "a" * 40, now, now),
            )
            connection.execute(
                "INSERT INTO events VALUES (?, 1, ?, ?, ?)",
                ("20260809-013", "UNKNOWN_PRIVATE", json.dumps({"secret": "hidden"}), now),
            )
        item = model.events("20260809-013", {})["items"][0]
        self.assertEqual(item["details"], {})
        self.assertNotIn("hidden", json.dumps(item))
```

- [ ] **Step 2: Run query tests and verify missing methods**

Run:

```bash
python3 -m unittest tests.test_dashboard_read_model -v
```

Expected: failures identify missing `project`, `tasks`, `approvals`, `task`, `events`, and `evidence` methods.

- [ ] **Step 3: Implement project and list-query helpers**

Add these fixed helpers and public methods to `DashboardReadModel`:

```python
TASK_FIELDS = (
    "dispatch_id", "title", "objective", "risk_level", "state", "owner",
    "agent", "updated_at", "current_head_sha",
)


def _page(rows, limit, offset):
    selected = rows[: limit + 1]
    return {
        "items": selected[:limit],
        "limit": limit,
        "offset": offset,
        "has_more": len(selected) > limit,
    }


def project(self):
    head = self._git(("rev-parse", "HEAD")).stdout.strip()
    branch = self._git(
        ("symbolic-ref", "--quiet", "--short", "HEAD"), check=False
    ).stdout.strip()
    dirty = bool(self._git((
        "status", "--porcelain=v1", "--untracked-files=no",
        "--ignore-submodules=all",
    )).stdout.strip())
    remotes = [line for line in self._git(("remote",)).stdout.splitlines() if line]
    worktrees = self._git(("worktree", "list", "--porcelain")).stdout
    with self.snapshot() as connection:
        counts = self._counts(connection)
        attention = self._attention_items(connection, limit=20)
    return {
        "repository_name": self.context.common_dir.parent.name,
        "branch": branch or "DETACHED",
        "head_sha": head,
        "remote_configured": bool(remotes),
        "worktree_count": worktrees.count("worktree "),
        "health": "ATTENTION" if dirty or attention else "HEALTHY",
        "counts": counts,
        "attention_items": attention,
    }
```

Implement `_counts`, `_attention_items`, and `tasks` with parameterized SQL only. Compute `effective_state = NEEDS_HUMAN_APPROVAL` when an unexpired pending approval exists. Compute attention reasons from pending approval, open blocker, stale review/evidence, lifecycle states, and head drift. Sort in Python by `(attention_priority, -updated_timestamp, dispatch_id)` after bounded SQL selection.

- [ ] **Step 4: Implement detail and subresource allowlists**

Implement the public signatures exactly:

```python
def task(self, dispatch_id):
    """Return one allowlisted task detail or raise TASK_NOT_FOUND."""

def events(self, dispatch_id, query):
    """Return paginated sanitized events; never return payload_json."""

def evidence(self, dispatch_id, query):
    """Return indexed metadata and stale flag; never read evidence contents."""

def approvals(self, query):
    """Return approval allowlist without request, nonce, or idempotency hashes."""
```

Known event summaries must be defined by a constant mapping:

```python
EVENT_SUMMARIES = {
    "TASK_CREATED": "任务已登记",
    "STATE_CHANGED": "任务状态已变化",
    "WORKTREE_ATTACHED": "Worktree 已关联",
    "AGENT_STATUS_UPDATED": "Agent 已汇报",
    "BLOCKER_ADDED": "新增阻塞",
    "BLOCKER_RESOLVED": "阻塞已解决",
    "REVIEW_ADDED": "新增审查结论",
    "EVIDENCE_ADDED": "新增验收证据",
}
```

For unknown event types, emit the type, timestamp, `summary="未识别事件"`, and `details={}`. For evidence paths, return only already-validated repository-relative `path` as `relative_path`.

- [ ] **Step 5: Run read-model tests and full regression**

```bash
python3 -m unittest tests.test_dashboard_read_model -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass with no unbounded list query and no secret fields.

- [ ] **Step 6: Commit Task 3**

```bash
git add team_control/dashboard_read_model.py tests/test_dashboard_read_model.py
git diff --cached --check
git commit -m "feat(dashboard): expose allowlisted control snapshots [20260809-001]"
```

### Task 4: Implement the localhost HTTP server and API contract

**Files:**

- Create: `team_control/dashboard_server.py`
- Create: `tests/test_dashboard_server.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Create a server test fixture that runs on port `0` and test:

```python
def test_get_health_has_envelope_and_security_headers(self):
    response, payload = self.request("GET", "/api/health")
    self.assertEqual(response.status, 200)
    self.assertEqual(payload["schema_version"], 1)
    self.assertRegex(payload["generated_at"], r"^\d{4}-\d{2}-\d{2}T")
    self.assertRegex(payload["source_head_sha"], r"^[0-9a-f]{40}$")
    self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
    self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
    self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))


def test_business_write_methods_are_rejected_without_side_effects(self):
    before = self.database_digest()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with self.subTest(method=method):
            response, payload = self.request(method, "/api/tasks")
            self.assertEqual(response.status, 405)
            self.assertEqual(payload["error"]["code"], "READ_ONLY")
    self.assertEqual(self.database_digest(), before)


def test_origin_and_host_policy(self):
    response, payload = self.request(
        "GET", "/api/health", headers={"Origin": "https://example.invalid"}
    )
    self.assertEqual(response.status, 403)
    self.assertEqual(payload["error"]["code"], "ORIGIN_REJECTED")
    response, payload = self.request(
        "GET", "/api/health", headers={"Host": "example.invalid"}
    )
    self.assertEqual(response.status, 400)
    self.assertEqual(payload["error"]["code"], "HOST_REJECTED")


def test_static_map_rejects_unknown_and_encoded_paths(self):
    for path in ("/secret", "/../handoff.md", "/%2e%2e/handoff.md", "/a%2fb"):
        with self.subTest(path=path):
            response, payload = self.request("GET", path)
            self.assertEqual(response.status, 404)
```

- [ ] **Step 2: Run server tests and verify import failure**

```bash
python3 -m unittest tests.test_dashboard_server -v
```

Expected: `ModuleNotFoundError: team_control.dashboard_server`.

- [ ] **Step 3: Implement immutable route and asset maps**

Create `team_control/dashboard_server.py` with these constants and factory:

```python
import json
import mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .dashboard_read_model import DashboardError, DashboardInputError


STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def create_server(model, assets_dir, host="127.0.0.1", port=0):
    if host != "127.0.0.1":
        raise ValueError("dashboard host must be 127.0.0.1")
    handler = make_handler(model, Path(assets_dir).resolve())
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
```

- [ ] **Step 4: Implement handler routing and errors**

`make_handler` must create a handler class that:

- validates Host before routing;
- permits absent Origin for GET/HEAD with valid Host;
- requires exact `http://localhost:60414` or `http://127.0.0.1:60414`-shaped Origin using the actual listening port when present;
- requires Origin for OPTIONS;
- maps only the seven API paths and four static paths;
- writes JSON with `ensure_ascii=False`, `allow_nan=False`, and compact separators;
- maps `DashboardInputError` to `400`, missing task to `404`, unavailable database/schema/WAL to `503`, unexpected exceptions to sanitized `500`;
- overrides `log_message` to write no access log;
- makes `do_POST`, `do_PUT`, `do_PATCH`, and `do_DELETE` call one `405 READ_ONLY` response.
- emits `X-Team-Repository-ID` with the 64-character hexadecimal repository identifier on every response so the launcher can safely reuse only the same repository service.

The success envelope must be built by one function:

```python
def success_envelope(model, data):
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_head_sha": model.source_head_sha(),
        "data": data,
    }
```

- [ ] **Step 5: Run server and regression tests**

```bash
python3 -m unittest tests.test_dashboard_server -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass; write methods produce no database or Git changes.

- [ ] **Step 6: Commit Task 4**

```bash
git add team_control/dashboard_server.py tests/test_dashboard_server.py
git diff --cached --check
git commit -m "feat(dashboard): add localhost readonly API [20260809-001]"
```

### Task 5: Build the exception-first browser interface

**Files:**

- Create: `apps/dashboard/index.html`
- Create: `apps/dashboard/styles.css`
- Create: `apps/dashboard/app.js`
- Create: `tests/test_dashboard_ui_contract.py`

- [ ] **Step 1: Write failing static UI contract tests**

Create `tests/test_dashboard_ui_contract.py`:

```python
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "apps" / "dashboard" / "index.html"
CSS = ROOT / "apps" / "dashboard" / "styles.css"
JS = ROOT / "apps" / "dashboard" / "app.js"


class DashboardUiContractTests(unittest.TestCase):
    def test_five_views_and_readonly_status_exist(self):
        html = HTML.read_text(encoding="utf-8")
        for label in ("总览", "任务", "Agents", "审批", "证据", "只读"):
            self.assertIn(label, html)
        for landmark in ("<nav", "<main", "aria-live", "aria-current"):
            self.assertIn(landmark, html)

    def test_javascript_uses_get_only(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertNotRegex(javascript, r"\b(POST|PUT|PATCH|DELETE)\b")
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)
        self.assertIn("fetch(url, { method: 'GET'", javascript)

    def test_refresh_and_stale_thresholds_are_explicit(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("const REFRESH_INTERVAL_MS = 15000", javascript)
        self.assertIn("const STALE_AFTER_MS = 45000", javascript)

    def test_css_has_focus_and_narrow_layout(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertRegex(css, r"@media\s*\(max-width:\s*1024px\)")
```

- [ ] **Step 2: Run UI tests and verify missing files**

```bash
python3 -m unittest tests.test_dashboard_ui_contract -v
```

Expected: `FileNotFoundError` for `apps/dashboard/index.html`.

- [ ] **Step 3: Create the semantic HTML shell**

`apps/dashboard/index.html` must contain:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>软件 AI 工程团队</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <header class="topbar">
    <div><strong>软件 AI 工程团队</strong><span class="readonly">只读</span></div>
    <div id="source-meta">正在连接本地控制平面</div>
  </header>
  <div class="shell">
    <nav aria-label="工作台视图">
      <button data-view="overview" aria-current="page">总览</button>
      <button data-view="tasks">任务</button>
      <button data-view="agents">Agents</button>
      <button data-view="approvals">审批</button>
      <button data-view="evidence">证据</button>
    </nav>
    <main id="content" tabindex="-1"></main>
  </div>
  <div id="status-region" role="status" aria-live="polite"></div>
  <noscript>此工作台需要启用 JavaScript；请回到 Codex 查看状态。</noscript>
  <script src="/app.js" defer></script>
</body>
</html>
```

- [ ] **Step 4: Implement exception-first CSS**

Use CSS custom properties for danger, warning, normal, background, text, border, and focus. The first viewport must include: top status bar, attention banner, four count cards, priority queue, and Codex guidance. State badges must combine text with color. At `max-width: 1024px`, collapse the sidebar and two-column panels to one column.

- [ ] **Step 5: Implement GET-only rendering and refresh**

`apps/dashboard/app.js` must start with:

```javascript
'use strict';

const REFRESH_INTERVAL_MS = 15000;
const STALE_AFTER_MS = 45000;
const state = {
  view: 'overview',
  lastSuccessAt: 0,
  sourceHeadSha: null,
  data: null,
  error: null,
};

async function getJson(url) {
  const response = await fetch(url, { method: 'GET', headers: { Accept: 'application/json' } });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || '本地工作台请求失败');
    error.code = payload.error?.code || 'REQUEST_FAILED';
    throw error;
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
```

Implement `renderOverview`, `renderTasks`, `renderAgents`, `renderApprovals`, `renderEvidence`, `renderLoading`, `renderEmpty`, `renderError`, and `renderStale`. Every dynamic value must pass through `escapeHtml`; use event listeners instead of inline JavaScript. Approval and blocker cards must say “请回到 Codex 处理” and must not contain action forms.

The data-loading contract is explicit: fetch `/api/project`, `/api/tasks?limit=100&offset=0`, and `/api/approvals?limit=100&offset=0` in parallel. Fetch task detail only for active tasks on that bounded first page, with at most four concurrent requests; aggregate those detail responses for the Agents view. The Evidence view requires a selected task and calls only `/api/tasks/:dispatch_id/evidence?limit=100&offset=0`. Do not invent global endpoints.

- [ ] **Step 6: Run UI contract and full tests**

```bash
python3 -m unittest tests.test_dashboard_ui_contract -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass; search confirms no browser write method or storage API.

- [ ] **Step 7: Commit Task 5**

```bash
git add apps/dashboard tests/test_dashboard_ui_contract.py
git diff --cached --check
git commit -m "feat(dashboard): add exception-first web console [20260809-001]"
```

### Task 6: Add the Codex-owned launcher

**Files:**

- Create: `team_control/dashboard_main.py`
- Create: `scripts/open-team-dashboard`
- Modify: `tests/test_dashboard_server.py`

- [ ] **Step 1: Write failing launcher tests**

Add tests for structured startup, non-loopback rejection, `--no-open`, and missing database. The successful test must start the subprocess, read exactly one startup JSON line, request `/api/health`, then terminate the process cleanly.

```python
def test_dashboard_main_emits_one_structured_startup_line(self):
    result = self.start_dashboard("--no-open", "--port", "0")
    payload = json.loads(result.stdout.readline())
    self.assertEqual(payload["status"], "started")
    self.assertEqual(payload["host"], "127.0.0.1")
    self.assertRegex(payload["url"], r"^http://127\.0\.0\.1:\d+$")
    self.assertNotIn("database", payload)
```

- [ ] **Step 2: Run the launcher test and verify missing entry point**

```bash
python3 -m unittest tests.test_dashboard_server -v
```

Expected: failure because `team_control.dashboard_main` and wrapper do not exist.

- [ ] **Step 3: Implement `dashboard_main.py`**

The entry point must:

- require `--repo` and allow only `--port` plus `--no-open`;
- discover `RepoContext`, require an existing database, and never initialize it;
- derive the default port from the first two bytes of SHA-256 over the canonical Git common directory: `49152 + value % 16383`; explicit `--port 0` remains test-only;
- probe the default port once; reuse only when the response is healthy and `X-Team-Repository-ID` exactly matches `model.repository_id()`; fail `PORT_IN_USE` on mismatch;
- create `DashboardReadModel`, run `health()`, then create the server on `127.0.0.1` when no matching service exists;
- emit one compact startup JSON line with `status`, `host`, `port`, `url`, and `source_head_sha`;
- open the browser with `webbrowser.open(url)` only after the server is listening and unless `--no-open`;
- install SIGINT/SIGTERM handlers that raise `KeyboardInterrupt`; close the server in `finally` without calling `shutdown()` from the serving thread or writing runtime state.

Use this parser skeleton:

```python
def build_parser():
    parser = argparse.ArgumentParser(prog="open-team-dashboard", allow_abbrev=False)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-open", action="store_true")
    return parser
```

- [ ] **Step 4: Implement the repository-bound shell wrapper**

Create executable `scripts/open-team-dashboard`:

```sh
#!/bin/sh
set -eu

for argument do
    case "$argument" in
        --repo|--repo=*)
            printf '%s\n' '{"error":{"code":"WRAPPER_REPO_OVERRIDE","message":"wrapper repository identity cannot be overridden"}}' >&2
            exit 2
            ;;
    esac
done

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd -P)
cd "$repo_root"
exec python3 -m team_control.dashboard_main --repo "$repo_root" "$@"
```

Set mode with `chmod 755 scripts/open-team-dashboard`.

- [ ] **Step 5: Run focused and full tests**

```bash
python3 -m unittest tests.test_dashboard_server -v
python3 -m unittest discover -s tests -v
```

Expected: launcher starts on a random loopback port, health returns `200`, no control database is created when missing.

- [ ] **Step 6: Commit Task 6**

```bash
git add team_control/dashboard_main.py scripts/open-team-dashboard \
  tests/test_dashboard_server.py
git diff --cached --check
git commit -m "feat(dashboard): add Codex-owned launcher [20260809-001]"
```

### Task 7: Integrate the project Skill and user guide

**Files:**

- Modify: `.agents/skills/ai-software-engineering-team/SKILL.md`
- Modify: `USER_OPERATING_GUIDE.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write failing documentation contract tests**

Add assertions:

```python
def test_skill_maps_open_dashboard_to_readonly_launcher(self):
    skill = SKILL_PATH.read_text(encoding="utf-8")
    for required in (
        "open-team-dashboard",
        "127.0.0.1",
        "never initializes a missing database",
        "browser remains read-only",
        "return to Codex",
    ):
        self.assertIn(required, skill)


def test_guide_documents_mvp1_actual_usage_and_limits(self):
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    for required in (
        "打开软件 AI 工程团队工作台",
        "scripts/open-team-dashboard",
        "每 15 秒",
        "45 秒",
        "请回到 Codex 处理",
        "不会配置 GitHub Remote",
    ):
        self.assertIn(required, guide)
    self.assertNotIn("MVP 1 的本地只读前端工作台尚未实现", guide)
```

- [ ] **Step 2: Run the contract tests and verify stale documentation**

```bash
python3 -m unittest tests.test_skill_contract -v
```

Expected: failures because Skill and guide still describe MVP 1 as unavailable.

- [ ] **Step 3: Update Skill intent mapping**

Add a section stating that “打开工程工作台 / 查看团队全局状态 / 打开软件 AI 工程团队工作台” maps to:

```text
scripts/open-team-dashboard
```

The Skill must require main-root health inspection first, never initialize a missing control database for this read-only viewer, never bind beyond `127.0.0.1`, and tell the user to return to Codex for any action.

- [ ] **Step 4: Update the operating guide**

Replace stale MVP 1 roadmap statements with:

- one-sentence natural-language open flow;
- what each of the five views means;
- refresh/stale behavior;
- no CLI requirement for the Human;
- missing database, service stopped, stale data, and head-drift troubleshooting;
- explicit distinction between MVP 1 read-only UI and future MVP 2 intents;
- explicit statement that GitHub Remote remains separate and unconfigured.

- [ ] **Step 5: Run documentation and full tests**

```bash
python3 -m unittest tests.test_skill_contract -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass and no stale “MVP 1 not implemented” statement remains.

- [ ] **Step 6: Commit Task 7**

```bash
git add .agents/skills/ai-software-engineering-team/SKILL.md \
  USER_OPERATING_GUIDE.md tests/test_skill_contract.py
git diff --cached --check
git commit -m "docs(dashboard): add natural-language operating flow [20260809-001]"
```

### Task 8: Prove the vertical slice and no-side-effect boundary

**Files:**

- Create: `tests/test_dashboard_end_to_end.py`
- Modify: `tests/test_dashboard_read_model.py`
- Modify: `tests/test_dashboard_server.py`

- [ ] **Step 1: Write a real temporary-repository end-to-end test**

The test must:

1. create a temporary Git repository and initialize the control store;
2. create normal, blocked, and pending-approval tasks;
3. record the main database/WAL digest, Git index digest/mtime, HEAD, refs, and `git status`;
4. start the dashboard on port `0`;
5. call all seven APIs plus `/`, `/styles.css`, and `/app.js`;
6. assert the blocked and approval tasks lead the overview queue;
7. assert no forbidden approval/event fields are present;
8. send all rejected write methods;
9. stop the server;
10. prove the recorded business/Git facts are unchanged.

Use a deterministic snapshot helper:

```python
def repository_snapshot(repo, database):
    index = repo / ".git" / "index"
    wal = Path(str(database) + "-wal")
    return {
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "wal_sha256": hashlib.sha256(wal.read_bytes()).hexdigest() if wal.exists() else None,
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "index_mtime_ns": index.stat().st_mtime_ns,
        "head": run(["git", "rev-parse", "HEAD"], repo).stdout.strip(),
        "refs": run(["git", "show-ref"], repo).stdout,
        "status": run(["git", "status", "--porcelain"], repo).stdout,
    }
```

- [ ] **Step 2: Verify the new test fails before final hardening**

```bash
python3 -m unittest tests.test_dashboard_end_to_end -v
```

Expected: at least one assertion exposes any remaining envelope, ordering, or side-effect defect.

- [ ] **Step 3: Fix only reproduced gaps**

Apply minimal changes to the read model/server/UI. Do not add new endpoints or write paths. For every discovered defect, add a focused regression test to the closest test module before changing production code.

- [ ] **Step 4: Run the complete suite and repository checks**

```bash
python3 -m unittest discover -s tests -v
git diff --check
git status --short --branch
```

Expected: all tests pass; no whitespace errors; only intended task files differ from the task base.

- [ ] **Step 5: Commit Task 8**

```bash
git add tests/test_dashboard_end_to_end.py \
  tests/test_dashboard_read_model.py tests/test_dashboard_server.py \
  team_control/dashboard_read_model.py team_control/dashboard_server.py \
  apps/dashboard
git diff --cached --check
git commit -m "test(dashboard): prove readonly vertical slice [20260809-001]"
```

### Task 9: Manual presentation, independent Review, evidence, and integration

**Files:**

- Create: `artifacts/dispatches/20260809-001/verification.md`
- Create: `artifacts/dispatches/20260809-001/mimo-inventory.md`
- Modify after integration: `handoff.md`

- [ ] **Step 1: Run a real local demonstration**

From the task Worktree:

```bash
scripts/open-team-dashboard --no-open --port 0
```

Capture the emitted URL, request `/api/health`, `/api/project`, `/api/tasks`, and `/`. Verify HTTP `200`, real dispatch `20260809-001`, current source HEAD, and no write controls. Keep the server bound to `127.0.0.1` and terminate it after verification.

- [ ] **Step 2: Write verification evidence**

`verification.md` must record:

- task base and candidate full SHA;
- exact test commands, test count, exit code, and timestamp;
- real API URL bound to loopback only;
- database/main-WAL/index/HEAD/refs/status before/after evidence;
- five-view UI checks and stale/error behavior;
- changed-file inventory;
- known limitations: no writes, no remote access, no GitHub, no MVP 2 intents.

Do not include credentials, raw process environments, approval nonces, or sensitive source content.

- [ ] **Step 3: Request Claude Code independent implementation Review**

Run one read-only medium-effort review bound to the candidate SHA. Require architecture, code quality, security, tests, scope, and `ACCEPT / MODIFY / BLOCK`. Save the substantive report to:

```text
artifacts/dispatches/20260809-001/implementation-review-claude.md
```

If Claude returns `MODIFY`, add regression tests, fix in the same Worktree, commit, rerun the full suite, and request a new review. If Claude reports quota/limit failure, wait; do not invoke fallback without current-session Human `yes`.

- [ ] **Step 4: Run Mimo inventory after implementation acceptance**

Use the installed local command against an isolated archive, never the live task Worktree:

```bash
inventory_root=$(mktemp -d)
git archive HEAD | tar -x -C "$inventory_root"
mimo --never-ask --trust run --pure --dir "$inventory_root" \
  "只读盘点 Dispatch 20260809-001。对照设计、提交、测试和 Claude 审查，输出目标与结果、关键决策、被否决方案、返工、验证、残余风险、可复用经验和后续行动。不要修改文件。"
```

Codex validates the output against Git/test evidence and writes only the distilled, non-sensitive result to `mimo-inventory.md`. If Mimo is unavailable or output is empty, record the failure and leave the task `INTEGRATED` rather than falsely closing it.

- [ ] **Step 5: Commit acceptance artifacts and ensure clean candidate**

```bash
git add artifacts/dispatches/20260809-001
git diff --cached --check
git commit -m "docs(acceptance): record MVP 1 verification [20260809-001]"
python3 -m unittest discover -s tests -v
git status --short --branch
```

Expected: full suite passes and task Worktree is clean.

- [ ] **Step 6: Codex-only integration gate**

Codex must verify:

```bash
git -C "/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team" status --porcelain
git -C "/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team" worktree list
git -C "/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team/.worktrees/20260809-001-codex-mvp1-readonly-dashboard" status --porcelain
git -C "/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team/.worktrees/20260809-001-codex-mvp1-readonly-dashboard" merge main
python3 -m unittest discover -s tests -v
```

Only after clean state, current main merge, full tests, and Claude `ACCEPT`, transition the task to `REVIEWING`, then `ACCEPTED`, and merge with `--no-ff` from main. Never push; GitHub Remote is still out of scope.

- [ ] **Step 7: Verify main and update handoff**

On integrated `main`:

```bash
python3 -m unittest discover -s tests -v
./scripts/repo-health.sh
git diff --check HEAD^ HEAD
git log -1 --oneline --decorate
```

Update `handoff.md` with the observed merge SHA, test count, Claude disposition/report path, launcher command, database location, read-only limitations, Mimo status, and next authorized stage. Commit the handoff separately on main, rerun repository health, then transition through `INTEGRATED` and `CLOSED` only when Mimo inventory and all closure evidence exist.

- [ ] **Step 8: Clean the task Worktree only after closure proof**

Verify branch ancestry and a clean Worktree, then use the documented Codex cleanup flow. If any check fails, retain the Worktree and report `BLOCKED`; do not force removal.

## Plan self-review checklist

- Every design requirement maps to a task: read-only WAL/Git boundary (Tasks 1–4, 8), seven APIs (Tasks 3–4), five views (Task 5), launcher (Task 6), Skill/guide (Task 7), real evidence/Review/Mimo/integration (Tasks 8–9).
- No GitHub Remote, cloud, remote binding, browser write intent, Codex App Server, or MVP 2/3 implementation is included.
- Public names are consistent: `DashboardReadModel`, `create_server`, `dashboard_main`, `scripts/open-team-dashboard`, `20260809-001`.
- Every code change starts with a failing test, runs focused tests, then full regression before its commit.
- No placeholder markers or unspecified “appropriate handling” steps remain.
