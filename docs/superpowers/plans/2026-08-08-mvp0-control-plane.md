# MVP 0 AI Software Engineering Team Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, auditable MVP 0 control plane that lets Codex create and track engineering tasks, enforce lifecycle and approval gates, inspect/repair the accepted Minor Worktree risk, and expose the workflow through a project Skill.

**Architecture:** Codex remains the only engineering authority. A Python standard-library Orchestrator is the only SQLite writer, stores runtime state under the Git common directory, and exposes deterministic JSON commands to the project Skill. Git remains the code fact source; every Git mutation that changes control-plane state uses a recoverable operation log, while Worktree repair fails closed unless safety is proven.

**Tech Stack:** Python 3.9 standard library (`sqlite3`, `fcntl`, `subprocess`, `hashlib`, `argparse`, `unittest`), POSIX shell, Git, JSON Schema documents without a runtime third-party validator.

---

## Scope and execution order

This plan implements MVP 0 only. It does not create the web dashboard, realtime adapter, remote GitHub repository, or any cloud service.

Execute tasks strictly in order. Each task ends with an atomic commit. The executor works only in the assigned Worktree; Codex owns integration, `main`, Worktree lifecycle, and `handoff.md`.

## File map

| Path | Responsibility |
|---|---|
| `team_control/errors.py` | Domain exceptions and machine-readable error codes |
| `team_control/git_context.py` | Canonical repository discovery, safe argv execution, identifier/path validation |
| `team_control/contracts.py` | State constants and lightweight record validation |
| `team_control/state_machine.py` | Legal lifecycle, pause, and resume transitions |
| `team_control/store.py` | SQLite schema, single-writer lock, task/event/approval/operation/evidence persistence |
| `team_control/operations.py` | `PREPARED → Git → verify → COMMITTED` execution and reconciliation |
| `team_control/doctor.py` | Worktree Minor-risk inspection and safe repair classification |
| `team_control/evidence.py` | SHA-256 evidence indexing and distilled artifact writing |
| `team_control/service.py` | Codex-authorized use cases and gate orchestration |
| `team_control/cli.py` | Deterministic JSON command interface used by the Skill |
| `scripts/team-control` | Stable POSIX entrypoint to the Python module |
| `scripts/worktree-doctor` | Stable POSIX entrypoint for inspect/repair |
| `schemas/*.schema.json` | Versioned external task/event/approval/evidence contracts |
| `.agents/skills/ai-software-engineering-team/SKILL.md` | Natural-language workflow and routing instructions |
| `USER_OPERATING_GUIDE.md` | No-CLI operating manual for Human |
| `examples/dispatches/mvp0-example.json` | Valid example Dispatch Record |
| `tests/` | Standard-library unit and integration tests |

### Task 1: Safe repository context and test foundation

**Files:**
- Create: `team_control/__init__.py`
- Create: `team_control/errors.py`
- Create: `team_control/git_context.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Test: `tests/test_git_context.py`

- [ ] **Step 1: Write the failing repository-safety tests**

```python
# tests/helpers.py
import shutil
import subprocess
from pathlib import Path


def run(argv, cwd, check=True):
    return subprocess.run(
        argv,
        cwd=str(cwd),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def make_repo(path):
    path.mkdir()
    run(["git", "init", "-b", "main"], path)
    run(["git", "config", "user.name", "MVP0 Test"], path)
    run(["git", "config", "user.email", "mvp0@example.invalid"], path)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    (path / ".gitignore").write_text("/.worktrees/\n", encoding="utf-8")
    scripts = path / "scripts"
    scripts.mkdir()
    source_script = Path(__file__).resolve().parents[1] / "scripts" / "new-agent-worktree.sh"
    shutil.copy2(source_script, scripts / "new-agent-worktree.sh")
    run(["git", "add", "README.md", ".gitignore", "scripts/new-agent-worktree.sh"], path)
    run(["git", "commit", "-m", "test: initialize fixture"], path)
    return path
```

```python
# tests/test_git_context.py
import tempfile
import unittest
from pathlib import Path

from team_control.errors import BoundaryError
from team_control.git_context import RepoContext, canonical_under, validate_component
from tests.helpers import make_repo


class GitContextTests(unittest.TestCase):
    def test_discovers_shared_git_common_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            self.assertEqual(context.root, repo.resolve())
            self.assertEqual(context.common_dir, (repo / ".git").resolve())

    def test_rejects_path_outside_registered_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with self.assertRaises(BoundaryError):
                canonical_under(root, root.parent / "escape")

    def test_rejects_shell_metacharacters_in_component(self):
        for value in ("bad/id", "../bad", "bad name", "x;touch-pwned"):
            with self.subTest(value=value):
                with self.assertRaises(BoundaryError):
                    validate_component(value, "dispatch-id")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify the expected import failure**

Run: `python3 -m unittest tests.test_git_context -v`

Expected: `ERROR` containing `No module named 'team_control'`.

- [ ] **Step 3: Implement domain errors and safe repository discovery**

```python
# team_control/__init__.py
__version__ = "0.1.0"
```

```python
# team_control/errors.py
class TeamControlError(Exception):
    code = "TEAM_CONTROL_ERROR"


class BoundaryError(TeamControlError):
    code = "BOUNDARY_ERROR"


class GitStateError(TeamControlError):
    code = "GIT_STATE_ERROR"


class ContractError(TeamControlError):
    code = "CONTRACT_ERROR"


class TransitionError(TeamControlError):
    code = "TRANSITION_ERROR"


class ApprovalError(TeamControlError):
    code = "APPROVAL_ERROR"


class ReconciliationError(TeamControlError):
    code = "RECONCILIATION_ERROR"
```

```python
# team_control/git_context.py
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import BoundaryError, GitStateError


COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_component(value, label):
    if not COMPONENT_RE.fullmatch(value) or ".." in value:
        raise BoundaryError("%s must match %s and not contain '..'" % (label, COMPONENT_RE.pattern))
    return value


def canonical_under(root, candidate):
    resolved_root = Path(root).resolve(strict=True)
    resolved_candidate = Path(candidate).resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise BoundaryError("path escapes registered root: %s" % resolved_candidate) from exc
    return resolved_candidate


def run_argv(argv, cwd, check=True):
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(v, str) for v in argv):
        raise BoundaryError("subprocess arguments must be a non-empty string argv")
    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
    )
    if check and completed.returncode != 0:
        raise GitStateError("command failed (%s): %s" % (completed.returncode, completed.stderr.strip()))
    return completed


@dataclass(frozen=True)
class RepoContext:
    root: Path
    common_dir: Path

    @classmethod
    def discover(cls, candidate):
        start = Path(candidate).resolve(strict=True)
        top = Path(run_argv(["git", "rev-parse", "--show-toplevel"], start).stdout.strip()).resolve(strict=True)
        common_raw = Path(run_argv(["git", "rev-parse", "--git-common-dir"], start).stdout.strip())
        common = common_raw if common_raw.is_absolute() else top / common_raw
        return cls(root=top, common_dir=common.resolve(strict=True))

    @property
    def runtime_dir(self):
        return self.common_dir / "team" / "runtime"
```

- [ ] **Step 4: Run the repository-safety tests**

Run: `python3 -m unittest tests.test_git_context -v`

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 5: Commit the foundation**

```bash
git add -- team_control/__init__.py team_control/errors.py team_control/git_context.py tests/__init__.py tests/helpers.py tests/test_git_context.py
git diff --cached --check
git commit -m "feat(control-plane): add safe repository context"
```

### Task 2: Versioned contracts and state vocabulary

**Files:**
- Create: `team_control/contracts.py`
- Create: `schemas/task.schema.json`
- Create: `schemas/event.schema.json`
- Create: `schemas/approval.schema.json`
- Create: `schemas/evidence.schema.json`
- Create: `schemas/agent-status.schema.json`
- Create: `schemas/review.schema.json`
- Create: `schemas/blocker.schema.json`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

```python
# tests/test_contracts.py
import unittest

from team_control.contracts import TASK_STATES, validate_record
from team_control.errors import ContractError


class ContractTests(unittest.TestCase):
    def test_state_vocabulary_contains_pause_and_fail_closed_states(self):
        self.assertIn("PAUSE_REQUESTED", TASK_STATES)
        self.assertIn("PAUSED", TASK_STATES)
        self.assertIn("UNKNOWN", TASK_STATES)

    def test_task_requires_dispatch_and_git_identity(self):
        with self.assertRaises(ContractError):
            validate_record("task", {"dispatch_id": "20260808-003"})

    def test_valid_task_is_accepted(self):
        record = {
            "schema_version": 1,
            "dispatch_id": "20260808-003",
            "title": "Example",
            "objective": "Prove contract",
            "risk_level": "L1",
            "state": "PLANNED",
            "task_base_sha": "a" * 40,
            "owner": "Codex",
        }
        self.assertEqual(validate_record("task", record), record)
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `python3 -m unittest tests.test_contracts -v`

Expected: `ERROR` containing `No module named 'team_control.contracts'`.

- [ ] **Step 3: Implement the vocabulary and lightweight runtime validation**

```python
# team_control/contracts.py
import re

from .errors import ContractError


TASK_STATES = frozenset({
    "PLANNED", "NEEDS_CLARIFICATION", "DISPATCHED", "IN_PROGRESS",
    "PAUSE_REQUESTED", "PAUSED", "BLOCKED", "NEEDS_DIRECTION",
    "NEEDS_HUMAN_APPROVAL", "REVIEWING", "ACCEPTED", "INTEGRATED",
    "RELEASED", "CLOSED", "UNKNOWN",
})
RISK_LEVELS = frozenset({"L1", "L2", "L3"})
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

REQUIRED = {
    "task": ("schema_version", "dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("schema_version", "dispatch_id", "sequence", "event_type", "created_at"),
    "approval": ("schema_version", "approval_id", "dispatch_id", "action", "target_sha", "request_hash", "expires_at"),
    "evidence": ("schema_version", "evidence_id", "dispatch_id", "kind", "path", "sha256", "created_at"),
    "agent_status": ("schema_version", "dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("schema_version", "review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "created_at"),
    "blocker": ("schema_version", "blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}


def validate_record(kind, record):
    if kind not in REQUIRED:
        raise ContractError("unknown contract kind: %s" % kind)
    missing = [key for key in REQUIRED[kind] if key not in record]
    if missing:
        raise ContractError("missing %s fields: %s" % (kind, ", ".join(missing)))
    if record.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    if kind == "task":
        if record["state"] not in TASK_STATES:
            raise ContractError("unknown task state: %s" % record["state"])
        if record["risk_level"] not in RISK_LEVELS:
            raise ContractError("unknown risk level: %s" % record["risk_level"])
        if not SHA_RE.fullmatch(record["task_base_sha"]):
            raise ContractError("task_base_sha must be a full hexadecimal SHA")
    return record
```

- [ ] **Step 4: Add the four JSON Schema documents**

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"task.schema.json","type":"object","required":["schema_version","dispatch_id","title","objective","risk_level","state","task_base_sha","owner"],"properties":{"schema_version":{"const":1},"dispatch_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]*$"},"title":{"type":"string","minLength":1},"objective":{"type":"string","minLength":1},"risk_level":{"enum":["L1","L2","L3"]},"state":{"enum":["PLANNED","NEEDS_CLARIFICATION","DISPATCHED","IN_PROGRESS","PAUSE_REQUESTED","PAUSED","BLOCKED","NEEDS_DIRECTION","NEEDS_HUMAN_APPROVAL","REVIEWING","ACCEPTED","INTEGRATED","RELEASED","CLOSED","UNKNOWN"]},"task_base_sha":{"type":"string","pattern":"^[0-9a-f]{40,64}$"},"owner":{"const":"Codex"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"event.schema.json","type":"object","required":["schema_version","dispatch_id","sequence","event_type","created_at"],"properties":{"schema_version":{"const":1},"dispatch_id":{"type":"string"},"sequence":{"type":"integer","minimum":1},"event_type":{"type":"string","minLength":1},"created_at":{"type":"string","format":"date-time"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"approval.schema.json","type":"object","required":["schema_version","approval_id","dispatch_id","action","target_sha","request_hash","expires_at"],"properties":{"schema_version":{"const":1},"approval_id":{"type":"string"},"dispatch_id":{"type":"string"},"action":{"type":"string"},"target_sha":{"type":"string","pattern":"^[0-9a-f]{40,64}$"},"request_hash":{"type":"string","pattern":"^[0-9a-f]{64}$"},"nonce_hash":{"type":"string","pattern":"^[0-9a-f]{64}$"},"expires_at":{"type":"string","format":"date-time"},"consumed_at":{"type":["string","null"],"format":"date-time"},"idempotency_key":{"type":"string"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"evidence.schema.json","type":"object","required":["schema_version","evidence_id","dispatch_id","kind","path","sha256","created_at"],"properties":{"schema_version":{"const":1},"evidence_id":{"type":"string"},"dispatch_id":{"type":"string"},"kind":{"enum":["commit","diff","test","review","approval","artifact"]},"path":{"type":"string"},"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},"source_sha":{"type":["string","null"]},"created_at":{"type":"string","format":"date-time"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"agent-status.schema.json","type":"object","required":["schema_version","dispatch_id","agent_id","role","state","updated_at"],"properties":{"schema_version":{"const":1},"dispatch_id":{"type":"string"},"agent_id":{"type":"string"},"role":{"type":"string"},"model":{"type":["string","null"]},"state":{"enum":["IN_PROGRESS","COMPLETED","BLOCKED","NEEDS_DIRECTION"]},"progress":{"type":"integer","minimum":0,"maximum":100},"updated_at":{"type":"string","format":"date-time"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"review.schema.json","type":"object","required":["schema_version","review_id","dispatch_id","reviewer","disposition","source_sha","created_at"],"properties":{"schema_version":{"const":1},"review_id":{"type":"string"},"dispatch_id":{"type":"string"},"reviewer":{"type":"string"},"disposition":{"enum":["ACCEPT","MODIFY","BLOCK","ESCALATE"]},"source_sha":{"type":"string"},"report_path":{"type":["string","null"]},"created_at":{"type":"string","format":"date-time"}},"additionalProperties":true}
```

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"blocker.schema.json","type":"object","required":["schema_version","blocker_id","dispatch_id","reason","owner","status","created_at"],"properties":{"schema_version":{"const":1},"blocker_id":{"type":"string"},"dispatch_id":{"type":"string"},"reason":{"type":"string"},"owner":{"type":"string"},"status":{"enum":["OPEN","RESOLVED"]},"resolution_condition":{"type":["string","null"]},"created_at":{"type":"string","format":"date-time"}},"additionalProperties":true}
```

- [ ] **Step 5: Run and commit the contract tests**

Run: `python3 -m unittest tests.test_contracts -v`

Expected: `Ran 3 tests ... OK`.

```bash
git add -- team_control/contracts.py schemas/task.schema.json schemas/event.schema.json schemas/approval.schema.json schemas/evidence.schema.json schemas/agent-status.schema.json schemas/review.schema.json schemas/blocker.schema.json tests/test_contracts.py
git diff --cached --check
git commit -m "feat(control-plane): define versioned contracts"
```

### Task 3: Lifecycle, pause, and resume state machine

**Files:**
- Create: `team_control/state_machine.py`
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing transition tests**

```python
# tests/test_state_machine.py
import unittest

from team_control.errors import TransitionError
from team_control.state_machine import next_state


class StateMachineTests(unittest.TestCase):
    def test_normal_delivery_path(self):
        self.assertEqual(next_state("PLANNED", "DISPATCHED"), ("DISPATCHED", None))
        self.assertEqual(next_state("REVIEWING", "ACCEPTED"), ("ACCEPTED", None))

    def test_pause_saves_and_restores_resume_state(self):
        self.assertEqual(next_state("IN_PROGRESS", "PAUSE_REQUESTED"), ("PAUSE_REQUESTED", "IN_PROGRESS"))
        self.assertEqual(next_state("PAUSED", "IN_PROGRESS", resume_state="IN_PROGRESS"), ("IN_PROGRESS", None))

    def test_pause_cannot_skip_safe_checkpoint(self):
        with self.assertRaises(TransitionError):
            next_state("IN_PROGRESS", "PAUSED")

    def test_illegal_release_is_rejected(self):
        with self.assertRaises(TransitionError):
            next_state("PLANNED", "RELEASED")
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `python3 -m unittest tests.test_state_machine -v`

Expected: `ERROR` containing `No module named 'team_control.state_machine'`.

- [ ] **Step 3: Implement the transition table**

```python
# team_control/state_machine.py
from .errors import TransitionError


ALLOWED = {
    "PLANNED": {"DISPATCHED", "NEEDS_CLARIFICATION", "BLOCKED"},
    "NEEDS_CLARIFICATION": {"PLANNED", "BLOCKED"},
    "DISPATCHED": {"IN_PROGRESS", "BLOCKED"},
    "IN_PROGRESS": {"REVIEWING", "BLOCKED", "NEEDS_DIRECTION", "PAUSE_REQUESTED"},
    "NEEDS_DIRECTION": {"IN_PROGRESS", "BLOCKED"},
    "REVIEWING": {"ACCEPTED", "IN_PROGRESS", "BLOCKED", "PAUSE_REQUESTED"},
    "BLOCKED": {"IN_PROGRESS", "REVIEWING", "PAUSE_REQUESTED", "UNKNOWN"},
    "PAUSE_REQUESTED": {"PAUSED", "BLOCKED"},
    "PAUSED": {"IN_PROGRESS", "REVIEWING", "BLOCKED"},
    "ACCEPTED": {"INTEGRATED", "BLOCKED"},
    "INTEGRATED": {"RELEASED", "BLOCKED"},
    "RELEASED": {"CLOSED", "BLOCKED"},
    "UNKNOWN": {"BLOCKED"},
    "CLOSED": set(),
}


def next_state(current, target, resume_state=None):
    if target not in ALLOWED.get(current, set()):
        raise TransitionError("illegal transition: %s -> %s" % (current, target))
    if target == "PAUSE_REQUESTED":
        return target, current
    if current == "PAUSED":
        if target != resume_state:
            raise TransitionError("resume target does not match saved state")
        return target, None
    return target, resume_state
```

- [ ] **Step 4: Run and commit the state-machine tests**

Run: `python3 -m unittest tests.test_state_machine -v`

Expected: `Ran 4 tests ... OK`.

```bash
git add -- team_control/state_machine.py tests/test_state_machine.py
git diff --cached --check
git commit -m "feat(control-plane): enforce lifecycle transitions"
```

### Task 4: Shared SQLite store and single-writer lock

**Files:**
- Create: `team_control/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing storage-location and schema tests**

```python
# tests/test_store.py
import tempfile
import unittest
from pathlib import Path

from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo


class StoreTests(unittest.TestCase):
    def test_database_lives_under_git_common_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            self.assertEqual(store.path, context.common_dir / "team" / "runtime" / "team.db")
            self.assertTrue(store.path.is_file())

    def test_schema_contains_control_plane_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            store.initialize()
            with store.read_connection() as connection:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"tasks", "events", "approvals", "operations", "evidence",
                             "agents", "reviews", "blockers"}.issubset(names))
```

- [ ] **Step 2: Run tests and verify the missing store module**

Run: `python3 -m unittest tests.test_store -v`

Expected: `ERROR` containing `No module named 'team_control.store'`.

- [ ] **Step 3: Implement the shared store, schema, and file lock**

```python
# team_control/store.py
import fcntl
import sqlite3
from contextlib import contextmanager


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tasks (
    dispatch_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    state TEXT NOT NULL,
    resume_state TEXT,
    task_base_sha TEXT NOT NULL,
    current_head_sha TEXT NOT NULL,
    owner TEXT NOT NULL,
    agent TEXT,
    slug TEXT,
    branch TEXT,
    worktree_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dispatch_id, sequence)
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    action TEXT NOT NULL,
    target_sha TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    nonce_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    action TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    target_sha TEXT NOT NULL,
    phase TEXT NOT NULL,
    result_json TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_sha TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT,
    state TEXT NOT NULL,
    progress INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dispatch_id, agent_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    reviewer TEXT NOT NULL,
    disposition TEXT NOT NULL,
    source_sha TEXT NOT NULL,
    report_path TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blockers (
    blocker_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    reason TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    resolution_condition TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class ControlStore:
    def __init__(self, path, lock_path):
        self.path = path
        self.lock_path = lock_path

    @classmethod
    def for_repo(cls, context):
        runtime = context.runtime_dir
        return cls(runtime / "team.db", runtime / "control-plane.lock")

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.mutation() as connection:
            connection.executescript(SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def mutation(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def read_connection(self):
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
```

- [ ] **Step 4: Run and commit the store tests**

Run: `python3 -m unittest tests.test_store -v`

Expected: `Ran 2 tests ... OK`.

```bash
git add -- team_control/store.py tests/test_store.py
git diff --cached --check
git commit -m "feat(control-plane): add shared transactional store"
```

### Task 5: Task creation, event ordering, and controlled transitions

**Files:**
- Modify: `team_control/store.py`
- Create: `team_control/service.py`
- Test: `tests/test_task_service.py`

- [ ] **Step 1: Write failing task and event tests**

```python
# tests/test_task_service.py
import tempfile
import unittest
from pathlib import Path

from team_control.errors import TransitionError
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(context)
        self.store.initialize()
        self.control = ControlPlane(context, self.store)
        self.head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_task_records_monotonic_event(self):
        task = self.control.create_task("20260808-003", "Example", "Exercise lifecycle", "L1")
        self.assertEqual(task["state"], "PLANNED")
        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1])

    def test_transition_records_next_sequence(self):
        self.control.create_task("20260808-003", "Example", "Exercise lifecycle", "L1")
        self.control.transition("20260808-003", "DISPATCHED", "scope approved")
        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1, 2])

    def test_illegal_transition_does_not_write_event(self):
        self.control.create_task("20260808-003", "Example", "Exercise lifecycle", "L1")
        with self.assertRaises(TransitionError):
            self.control.transition("20260808-003", "RELEASED", "invalid")
        self.assertEqual(len(self.store.list_events("20260808-003")), 1)
```

- [ ] **Step 2: Run tests and verify the missing service failure**

Run: `python3 -m unittest tests.test_task_service -v`

Expected: `ERROR` containing `No module named 'team_control.service'`.

- [ ] **Step 3: Add task/event methods to `ControlStore`**

```python
# Add imports at top of team_control/store.py
import json
from datetime import datetime, timezone

from .contracts import validate_record
from .state_machine import next_state


def utc_now():
    return datetime.now(timezone.utc).isoformat()
```

```python
# Add inside ControlStore
    def create_task(self, record):
        validate_record("task", record)
        now = utc_now()
        with self.mutation() as connection:
            connection.execute(
                """INSERT INTO tasks (
                       dispatch_id, schema_version, title, objective, risk_level, state, resume_state,
                       task_base_sha, current_head_sha, owner, agent, slug, branch, worktree_path,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)""",
                (record["dispatch_id"], 1, record["title"], record["objective"],
                 record["risk_level"], record["state"], record["task_base_sha"],
                 record["task_base_sha"], record["owner"], now, now),
            )
            connection.execute(
                "INSERT INTO events VALUES (?, 1, ?, ?, ?)",
                (record["dispatch_id"], "TASK_CREATED", json.dumps(record, sort_keys=True), now),
            )
        return self.get_task(record["dispatch_id"])

    def get_task(self, dispatch_id):
        with self.read_connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)).fetchone()
        return dict(row) if row else None

    def list_events(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE dispatch_id = ? ORDER BY sequence", (dispatch_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, dispatch_id, target, reason):
        now = utc_now()
        with self.mutation() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)).fetchone()
            if row is None:
                raise KeyError(dispatch_id)
            target_state, resume_state = next_state(row["state"], target, row["resume_state"])
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()[0]
            connection.execute(
                "UPDATE tasks SET state = ?, resume_state = ?, updated_at = ? WHERE dispatch_id = ?",
                (target_state, resume_state, now, dispatch_id),
            )
            payload = {"from": row["state"], "to": target_state, "reason": reason}
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (dispatch_id, sequence, "STATE_CHANGED", json.dumps(payload, sort_keys=True), now),
            )
        return self.get_task(dispatch_id)

    def attach_worktree(self, dispatch_id, agent, slug, branch, path):
        with self.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET agent = ?, slug = ?, branch = ?, worktree_path = ?, updated_at = ? WHERE dispatch_id = ?",
                (agent, slug, branch, path, utc_now(), dispatch_id),
            )
        return self.get_task(dispatch_id)
```

- [ ] **Step 4: Implement the initial `ControlPlane` service**

```python
# team_control/service.py
from .contracts import validate_record
from .git_context import run_argv, validate_component


class ControlPlane:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def current_head(self):
        return run_argv(["git", "rev-parse", "HEAD"], self.context.root).stdout.strip()

    def create_task(self, dispatch_id, title, objective, risk_level):
        validate_component(dispatch_id, "dispatch-id")
        record = {
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "title": title,
            "objective": objective,
            "risk_level": risk_level,
            "state": "PLANNED",
            "task_base_sha": self.current_head(),
            "owner": "Codex",
        }
        validate_record("task", record)
        return self.store.create_task(record)

    def transition(self, dispatch_id, target, reason):
        return self.store.transition(dispatch_id, target, reason)

    def status(self, dispatch_id):
        return {"task": self.store.get_task(dispatch_id), "events": self.store.list_events(dispatch_id)}
```

- [ ] **Step 5: Run and commit task-service tests**

Run: `python3 -m unittest tests.test_task_service -v`

Expected: `Ran 3 tests ... OK`.

```bash
git add -- team_control/store.py team_control/service.py tests/test_task_service.py
git diff --cached --check
git commit -m "feat(control-plane): persist tasks and lifecycle events"
```

### Task 6: Action-level approval gate and atomic nonce consumption

**Files:**
- Modify: `team_control/store.py`
- Modify: `team_control/service.py`
- Test: `tests/test_approvals.py`

- [ ] **Step 1: Write failing approval replay and target-drift tests**

```python
# tests/test_approvals.py
import tempfile
import unittest
from pathlib import Path

from team_control.errors import ApprovalError
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        repo = make_repo(Path(self.tmp.name) / "repo")
        context = RepoContext.discover(repo)
        self.store = ControlStore.for_repo(context)
        self.store.initialize()
        self.control = ControlPlane(context, self.store)
        self.task = self.control.create_task("20260808-004", "Approval", "Prove one-shot approval", "L3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_nonce_can_be_consumed_only_once(self):
        nonce = "human-confirmation"
        approval = self.control.request_approval(
            "20260808-004", "integrate", self.task["current_head_sha"], {"branch": "candidate"}, nonce, 10
        )
        self.assertEqual(self.control.status("20260808-004")["effective_state"], "NEEDS_HUMAN_APPROVAL")
        operation = self.control.consume_approval(approval["approval_id"], nonce, self.task["current_head_sha"])
        self.assertEqual(operation["phase"], "PREPARED")
        self.assertEqual(self.control.status("20260808-004")["effective_state"], "PLANNED")
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(approval["approval_id"], nonce, self.task["current_head_sha"])

    def test_target_sha_drift_rejects_approval(self):
        approval = self.control.request_approval(
            "20260808-004", "integrate", self.task["current_head_sha"], {}, "nonce", 10
        )
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(approval["approval_id"], "nonce", "b" * 40)
```

- [ ] **Step 2: Run tests and verify the missing methods**

Run: `python3 -m unittest tests.test_approvals -v`

Expected: `ERROR` containing `ControlPlane` has no attribute `request_approval`.

- [ ] **Step 3: Add atomic approval methods to `ControlStore`**

```python
# Add imports to team_control/store.py
import hashlib
import uuid
from datetime import timedelta


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
```

```python
# Add inside ControlStore
    def create_approval(self, dispatch_id, action, target_sha, request_hash, nonce, ttl_minutes, idempotency_key):
        approval_id = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        with self.mutation() as connection:
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'PENDING', ?)",
                (approval_id, dispatch_id, action, target_sha, request_hash,
                 sha256_text(nonce), expires, idempotency_key),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id):
        with self.read_connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def pending_approvals(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE dispatch_id = ? AND status = 'PENDING' ORDER BY expires_at",
                (dispatch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def consume_approval(self, approval_id, nonce, actual_sha):
        now = utc_now()
        with self.mutation() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None or row["status"] != "PENDING" or row["consumed_at"] is not None:
                from .errors import ApprovalError
                raise ApprovalError("approval is missing or already consumed")
            if row["nonce_hash"] != sha256_text(nonce):
                from .errors import ApprovalError
                raise ApprovalError("approval nonce mismatch")
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                from .errors import ApprovalError
                raise ApprovalError("approval expired")
            if row["target_sha"] != actual_sha:
                from .errors import ApprovalError
                raise ApprovalError("approval target SHA drifted")
            operation_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, 'PREPARED', NULL, ?, ?, ?)",
                (operation_id, row["dispatch_id"], row["action"], row["request_hash"],
                 row["target_sha"], row["idempotency_key"], now, now),
            )
            connection.execute(
                "UPDATE approvals SET status = 'CONSUMED', consumed_at = ? WHERE approval_id = ?",
                (now, approval_id),
            )
            operation = connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(operation)
```

- [ ] **Step 4: Add canonical request hashing and service methods**

```python
# Add imports to team_control/service.py
import hashlib
import json
import uuid
```

```python
# Add inside ControlPlane
    def request_approval(self, dispatch_id, action, target_sha, parameters, nonce, ttl_minutes):
        request_json = json.dumps(
            {"dispatch_id": dispatch_id, "action": action, "target_sha": target_sha, "parameters": parameters},
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        return self.store.create_approval(
            dispatch_id, action, target_sha, request_hash, nonce, ttl_minutes, str(uuid.uuid4())
        )

    def consume_approval(self, approval_id, nonce, actual_sha):
        return self.store.consume_approval(approval_id, nonce, actual_sha)

    def status(self, dispatch_id):
        task = self.store.get_task(dispatch_id)
        approvals = self.store.pending_approvals(dispatch_id)
        git_cwd = task["worktree_path"] if task.get("worktree_path") else self.context.root
        actual_head_sha = run_argv(["git", "rev-parse", "HEAD"], git_cwd).stdout.strip()
        return {
            "task": task,
            "events": self.store.list_events(dispatch_id),
            "pending_approvals": approvals,
            "effective_state": "NEEDS_HUMAN_APPROVAL" if approvals else task["state"],
            "actual_head_sha": actual_head_sha,
            "head_drift": actual_head_sha != task["current_head_sha"],
        }
```

- [ ] **Step 5: Run and commit approval tests**

Run: `python3 -m unittest tests.test_approvals -v`

Expected: `Ran 2 tests ... OK`.

```bash
git add -- team_control/store.py team_control/service.py tests/test_approvals.py
git diff --cached --check
git commit -m "feat(control-plane): enforce one-shot approval gates"
```

### Task 7: Recoverable Git operation log and reconciliation

**Files:**
- Create: `team_control/operations.py`
- Modify: `team_control/store.py`
- Test: `tests/test_operations.py`

- [ ] **Step 1: Write failing crash-recovery tests**

```python
# tests/test_operations.py
import tempfile
import unittest
from pathlib import Path

from team_control.git_context import RepoContext
from team_control.operations import OperationCoordinator
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.store.create_task({"schema_version": 1, "dispatch_id": "20260808-005", "title": "Ops",
                                "objective": "Recover operation", "risk_level": "L1", "state": "PLANNED",
                                "task_base_sha": head, "owner": "Codex"})
        self.ops = OperationCoordinator(self.context, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reconcile_commits_verified_prepared_operation(self):
        operation = self.store.prepare_operation(
            "20260808-005", "verify-head", "a" * 64,
            run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip(), "ops-1"
        )
        result = self.ops.reconcile_one(operation["operation_id"], lambda op: {"verified": True})
        self.assertEqual(result["phase"], "COMMITTED")

    def test_reconcile_blocks_unknown_postcondition(self):
        operation = self.store.prepare_operation(
            "20260808-005", "unknown", "b" * 64,
            run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip(), "ops-2"
        )
        result = self.ops.reconcile_one(operation["operation_id"], lambda op: {"verified": None})
        self.assertEqual(result["phase"], "BLOCKED")
```

- [ ] **Step 2: Run tests and verify the missing operations module**

Run: `python3 -m unittest tests.test_operations -v`

Expected: `ERROR` containing `No module named 'team_control.operations'`.

- [ ] **Step 3: Add operation methods to `ControlStore`**

```python
# Add inside ControlStore
    def prepare_operation(self, dispatch_id, action, request_hash, target_sha, idempotency_key):
        operation_id = str(uuid.uuid4())
        now = utc_now()
        with self.mutation() as connection:
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, 'PREPARED', NULL, ?, ?, ?)",
                (operation_id, dispatch_id, action, request_hash, target_sha,
                 idempotency_key, now, now),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id):
        with self.read_connection() as connection:
            row = connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def finish_operation(self, operation_id, phase, result):
        if phase not in {"COMMITTED", "FAILED", "BLOCKED"}:
            raise ValueError("invalid terminal operation phase")
        with self.mutation() as connection:
            connection.execute(
                "UPDATE operations SET phase = ?, result_json = ?, updated_at = ? WHERE operation_id = ? AND phase = 'PREPARED'",
                (phase, json.dumps(result, sort_keys=True), utc_now(), operation_id),
            )
        return self.get_operation(operation_id)

    def prepared_operations(self):
        with self.read_connection() as connection:
            rows = connection.execute("SELECT * FROM operations WHERE phase = 'PREPARED' ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Implement explicit reconciliation**

```python
# team_control/operations.py
from .errors import ReconciliationError
from .git_context import run_argv


class OperationCoordinator:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def reconcile_one(self, operation_id, verifier):
        operation = self.store.get_operation(operation_id)
        if operation is None or operation["phase"] != "PREPARED":
            raise ReconciliationError("operation is not PREPARED")
        result = verifier(operation)
        verified = result.get("verified")
        if verified is True:
            return self.store.finish_operation(operation_id, "COMMITTED", result)
        if verified is False:
            return self.store.finish_operation(operation_id, "FAILED", result)
        return self.store.finish_operation(operation_id, "BLOCKED", result)

    def reconcile_all(self, verifiers):
        results = []
        for operation in self.store.prepared_operations():
            verifier = verifiers.get(operation["action"])
            if verifier is None:
                results.append(self.store.finish_operation(
                    operation["operation_id"], "BLOCKED", {"verified": None, "reason": "no verifier"}
                ))
            else:
                results.append(self.reconcile_one(operation["operation_id"], verifier))
        return results

    def execute_git(self, dispatch_id, action, request_hash, target_sha, idempotency_key, argv, verifier,
                    on_verified=None):
        operation = self.store.prepare_operation(
            dispatch_id, action, request_hash, target_sha, idempotency_key
        )
        completed = run_argv(argv, self.context.root, check=False)
        verified = verifier(operation)
        verified.update({
            "command_returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
        })
        if verified.get("verified") is True and on_verified is not None:
            on_verified(verified)
        return self.reconcile_one(operation["operation_id"], lambda ignored: verified)
```

- [ ] **Step 5: Run and commit operation tests**

Run: `python3 -m unittest tests.test_operations -v`

Expected: `Ran 2 tests ... OK`.

```bash
git add -- team_control/operations.py team_control/store.py tests/test_operations.py
git diff --cached --check
git commit -m "feat(control-plane): add recoverable operation log"
```

### Task 8: Worktree Doctor for the accepted Minor risk

**Files:**
- Create: `team_control/doctor.py`
- Modify: `team_control/service.py`
- Modify: `team_control/store.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write failing classification and repair tests**

```python
# tests/test_doctor.py
import shutil
import tempfile
import unittest
from pathlib import Path

from team_control.doctor import WorktreeDoctor
from team_control.errors import ReconciliationError
from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        (self.repo / ".worktrees").mkdir()
        self.context = RepoContext.discover(self.repo)
        self.base = run(["git", "rev-parse", "main"], self.repo).stdout.strip()
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.store.create_task({"schema_version": 1, "dispatch_id": "20260808-006", "title": "Minor",
                                "objective": "Repair branch-only residue", "risk_level": "L1", "state": "PLANNED",
                                "task_base_sha": self.base, "owner": "Codex"})
        self.doctor = WorktreeDoctor(self.context, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_branch_only_at_base_is_repairable(self):
        run(["git", "branch", "agent/codex/20260808-006-minor", self.base], self.repo)
        report = self.doctor.inspect("20260808-006", "codex", "minor", self.base)
        self.assertEqual(report["classification"], "REPAIRABLE_BRANCH_ONLY")
        repaired = self.doctor.repair(report)
        self.assertEqual(repaired["classification"], "HEALTHY")

    def test_unknown_directory_residue_is_blocked(self):
        path = self.repo / ".worktrees" / "20260808-006-codex-minor"
        path.mkdir(parents=True)
        (path / "unknown.txt").write_text("preserve me", encoding="utf-8")
        report = self.doctor.inspect("20260808-006", "codex", "minor", self.base)
        self.assertEqual(report["classification"], "BLOCKED_PATH_RESIDUE")
        self.assertTrue((path / "unknown.txt").is_file())

    def test_advanced_branch_is_blocked(self):
        run(["git", "branch", "agent/codex/20260808-006-minor", self.base], self.repo)
        run(["git", "checkout", "agent/codex/20260808-006-minor"], self.repo)
        (self.repo / "change.txt").write_text("commit\n", encoding="utf-8")
        run(["git", "add", "change.txt"], self.repo)
        run(["git", "commit", "-m", "test: advance branch"], self.repo)
        run(["git", "checkout", "main"], self.repo)
        report = self.doctor.inspect("20260808-006", "codex", "minor", self.base)
        self.assertEqual(report["classification"], "BLOCKED_BRANCH_ADVANCED")

    def test_stale_metadata_is_preserved_and_blocked(self):
        path = self.repo / ".worktrees" / "20260808-006-codex-minor"
        run(["git", "worktree", "add", "-b", "agent/codex/20260808-006-minor", str(path), self.base], self.repo)
        shutil.rmtree(path)
        report = self.doctor.inspect("20260808-006", "codex", "minor", self.base)
        self.assertEqual(report["classification"], "BLOCKED_STALE_METADATA")
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)

    def test_expected_path_registered_to_other_branch_is_blocked(self):
        path = self.repo / ".worktrees" / "20260808-006-codex-minor"
        run(["git", "worktree", "add", "-b", "agent/codex/other-task", str(path), self.base], self.repo)
        report = self.doctor.inspect("20260808-006", "codex", "minor", self.base)
        self.assertEqual(report["classification"], "BLOCKED_REGISTRATION_MISMATCH")
```

- [ ] **Step 2: Run tests and verify the missing doctor module**

Run: `python3 -m unittest tests.test_doctor -v`

Expected: `ERROR` containing `No module named 'team_control.doctor'`.

- [ ] **Step 3: Implement inspection without deletion**

```python
# team_control/doctor.py
import hashlib
import json
import os

from .errors import ReconciliationError
from .git_context import canonical_under, run_argv, validate_component
from .operations import OperationCoordinator


def registered_worktrees(context):
    output = run_argv(["git", "worktree", "list", "--porcelain"], context.root).stdout
    entries = {}
    current = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree "):])
            entries[current] = {"branch": None}
        elif current and line.startswith("branch refs/heads/"):
            entries[current]["branch"] = line[len("branch refs/heads/"):]
    return entries


class WorktreeDoctor:
    def __init__(self, context, store):
        self.context = context
        self.store = store
        self.operations = OperationCoordinator(context, store)

    def expected(self, dispatch_id, agent, slug):
        for value, label in ((dispatch_id, "dispatch-id"), (agent, "agent"), (slug, "slug")):
            validate_component(value, label)
        root = canonical_under(self.context.root, self.context.root / ".worktrees")
        path = canonical_under(self.context.root, root / ("%s-%s-%s" % (dispatch_id, agent, slug)))
        branch = "agent/%s/%s-%s" % (agent, dispatch_id, slug)
        return branch, path

    def inspect(self, dispatch_id, agent, slug, task_base_sha):
        branch, path = self.expected(dispatch_id, agent, slug)
        branch_check = run_argv(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch],
            self.context.root,
            check=False,
        )
        branch_exists = branch_check.returncode == 0
        branch_sha = None
        if branch_exists:
            branch_sha = run_argv(["git", "rev-parse", branch], self.context.root).stdout.strip()
        registrations = registered_worktrees(self.context)
        registration = registrations.get(os.path.realpath(str(path)))
        registered = registration is not None
        registered_branch = registration["branch"] if registration else None
        path_exists = os.path.lexists(str(path))
        if registered and registered_branch != branch:
            classification = "BLOCKED_REGISTRATION_MISMATCH"
        elif registered and path_exists and branch_exists:
            classification = "HEALTHY"
        elif path_exists and not registered:
            classification = "BLOCKED_PATH_RESIDUE"
        elif branch_exists and branch_sha != task_base_sha:
            classification = "BLOCKED_BRANCH_ADVANCED"
        elif registered and not path_exists and branch_exists and branch_sha == task_base_sha:
            classification = "BLOCKED_STALE_METADATA"
        elif branch_exists and not registered and not path_exists and branch_sha == task_base_sha:
            classification = "REPAIRABLE_BRANCH_ONLY"
        elif not branch_exists and not registered and not path_exists:
            classification = "NO_RESIDUE"
        else:
            classification = "BLOCKED_UNKNOWN"
        return {
            "classification": classification,
            "dispatch_id": dispatch_id,
            "agent": agent,
            "slug": slug,
            "branch": branch,
            "path": str(path),
            "task_base_sha": task_base_sha,
            "branch_sha": branch_sha,
            "registered": registered,
            "registered_branch": registered_branch,
            "path_exists": path_exists,
        }
```

- [ ] **Step 4: Implement only the proven-safe branch-only reconstruction**

```python
# Add inside WorktreeDoctor
    def repair(self, report):
        classification = report["classification"]
        if classification == "HEALTHY":
            self.store.attach_worktree(
                report["dispatch_id"], report["agent"], report["slug"], report["branch"], report["path"]
            )
            return report
        if classification != "REPAIRABLE_BRANCH_ONLY":
            raise ReconciliationError("doctor refuses repair: %s" % classification)
        request_json = json.dumps(report, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        operation = self.operations.execute_git(
            report["dispatch_id"],
            "doctor-reconstruct-worktree",
            request_hash,
            report["task_base_sha"],
            "doctor:%s:%s:%s" % (report["dispatch_id"], report["branch"], report["task_base_sha"]),
            ["git", "worktree", "add", report["path"], report["branch"]],
            lambda ignored: {
                "verified": self.inspect(
                    report["dispatch_id"], report["agent"], report["slug"], report["task_base_sha"]
                )["classification"] == "HEALTHY"
            },
            lambda ignored: self.store.attach_worktree(
                report["dispatch_id"], report["agent"], report["slug"], report["branch"], report["path"]
            ),
        )
        if operation["phase"] != "COMMITTED":
            raise ReconciliationError("worktree reconstruction could not be verified")
        repaired = self.inspect(
            report["dispatch_id"], report["agent"], report["slug"], report["task_base_sha"]
        )
        if repaired["classification"] != "HEALTHY":
            raise ReconciliationError("repair did not produce a healthy Worktree")
        return repaired

    def create(self, report):
        if report["classification"] != "NO_RESIDUE":
            raise ReconciliationError("doctor refuses create: %s" % report["classification"])
        request_json = json.dumps(report, sort_keys=True, separators=(",", ":"))
        operation = self.operations.execute_git(
            report["dispatch_id"],
            "create-worktree",
            hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
            report["task_base_sha"],
            "create:%s:%s" % (report["dispatch_id"], report["task_base_sha"]),
            [str(self.context.root / "scripts" / "new-agent-worktree.sh"),
             report["dispatch_id"], report["agent"], report["slug"]],
            lambda ignored: {
                "verified": self.inspect(
                    report["dispatch_id"], report["agent"], report["slug"], report["task_base_sha"]
                )["classification"] == "HEALTHY"
            },
            lambda ignored: self.store.attach_worktree(
                report["dispatch_id"], report["agent"], report["slug"], report["branch"], report["path"]
            ),
        )
        if operation["phase"] != "COMMITTED":
            raise ReconciliationError("worktree creation could not be verified")
        return self.inspect(
            report["dispatch_id"], report["agent"], report["slug"], report["task_base_sha"]
        )
```

- [ ] **Step 5: Add the write-task isolation use case**

```python
# Add inside ControlPlane in team_control/service.py
    def start_write_task(self, dispatch_id, title, objective, risk_level, agent, slug):
        task = self.create_task(dispatch_id, title, objective, risk_level)
        from .doctor import WorktreeDoctor
        doctor = WorktreeDoctor(self.context, self.store)
        report = doctor.inspect(dispatch_id, agent, slug, task["task_base_sha"])
        doctor.create(report)
        return self.store.transition(dispatch_id, "DISPATCHED", "isolated Worktree verified")
```

- [ ] **Step 6: Run the focused safety tests and the full suite**

Run: `python3 -m unittest tests.test_doctor -v`

Expected: `Ran 5 tests ... OK`.

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass; no test deletes data outside its temporary directory.

- [ ] **Step 7: Commit the Doctor and isolation service**

```bash
git add -- team_control/doctor.py team_control/service.py team_control/store.py tests/test_doctor.py
git diff --cached --check
git commit -m "feat(control-plane): add fail-closed worktree doctor"
```

### Task 9: Agent reports, blockers, reviews, and evidence artifacts

**Files:**
- Create: `team_control/evidence.py`
- Create: `examples/dispatches/mvp0-example.json`
- Modify: `team_control/store.py`
- Modify: `team_control/service.py`
- Test: `tests/test_evidence.py`

- [ ] **Step 1: Write failing evidence-integrity tests**

```python
# tests/test_evidence.py
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from team_control.evidence import EvidenceManager
from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class EvidenceTests(unittest.TestCase):
    def test_recorded_hash_matches_file_and_artifact_is_distilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            store.create_task({"schema_version": 1, "dispatch_id": "20260808-007", "title": "Evidence",
                               "objective": "Hash evidence", "risk_level": "L1", "state": "PLANNED",
                               "task_base_sha": head, "owner": "Codex"})
            result_file = repo / "result.txt"
            result_file.write_text("PASS\n", encoding="utf-8")
            manager = EvidenceManager(context, store)
            record = manager.record("20260808-007", "test", result_file, head)
            self.assertEqual(record["sha256"], hashlib.sha256(b"PASS\n").hexdigest())
            artifact = manager.write_summary("20260808-007")
            data = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(data["dispatch_id"], "20260808-007")
            self.assertNotIn("file_contents", data)

    def test_agent_review_and_blocker_records_are_queryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            store.create_task({"schema_version": 1, "dispatch_id": "20260808-007", "title": "Reports",
                               "objective": "Track collaborators", "risk_level": "L2", "state": "PLANNED",
                               "task_base_sha": head, "owner": "Codex"})
            store.upsert_agent_status({"schema_version": 1, "dispatch_id": "20260808-007",
                                       "agent_id": "worker-1", "role": "executor", "model": "configured-default",
                                       "state": "IN_PROGRESS", "progress": 25, "updated_at": "2026-08-08T00:00:00+00:00"})
            store.add_blocker("20260808-007", "dependency unavailable", "Codex", "dependency restored")
            store.add_review("20260808-007", "reviewer-1", "MODIFY", head, "artifacts/review.md")
            self.assertEqual(len(store.list_agent_status("20260808-007")), 1)
            self.assertEqual(len(store.list_blockers("20260808-007")), 1)
            self.assertEqual(len(store.list_reviews("20260808-007")), 1)
```

- [ ] **Step 2: Run tests and verify the missing evidence module**

Run: `python3 -m unittest tests.test_evidence -v`

Expected: `ERROR` containing `No module named 'team_control.evidence'`.

- [ ] **Step 3: Add evidence persistence to `ControlStore`**

```python
# Add inside ControlStore
    def upsert_agent_status(self, record):
        validate_record("agent_status", record)
        with self.mutation() as connection:
            connection.execute(
                """INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(dispatch_id, agent_id) DO UPDATE SET
                     role=excluded.role, model=excluded.model, state=excluded.state,
                     progress=excluded.progress, report_json=excluded.report_json,
                     updated_at=excluded.updated_at""",
                (record["dispatch_id"], record["agent_id"], record["role"], record.get("model"),
                 record["state"], record.get("progress", 0), json.dumps(record, sort_keys=True),
                 record["updated_at"]),
            )
        return record

    def list_agent_status(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM agents WHERE dispatch_id = ? ORDER BY agent_id", (dispatch_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_blocker(self, dispatch_id, reason, owner, resolution_condition):
        blocker_id = str(uuid.uuid4())
        now = utc_now()
        with self.mutation() as connection:
            connection.execute(
                "INSERT INTO blockers VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)",
                (blocker_id, dispatch_id, reason, owner, resolution_condition, now, now),
            )
        return blocker_id

    def list_blockers(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM blockers WHERE dispatch_id = ? ORDER BY created_at", (dispatch_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_review(self, dispatch_id, reviewer, disposition, source_sha, report_path):
        review_id = str(uuid.uuid4())
        with self.mutation() as connection:
            connection.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                (review_id, dispatch_id, reviewer, disposition, source_sha, report_path, utc_now()),
            )
        return review_id

    def list_reviews(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reviews WHERE dispatch_id = ? ORDER BY created_at", (dispatch_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_evidence(self, record):
        validate_record("evidence", record)
        with self.mutation() as connection:
            connection.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["evidence_id"], record["dispatch_id"], record["kind"],
                 record["path"], record["sha256"], record.get("source_sha"), record["created_at"]),
            )
        return record

    def list_evidence(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence WHERE dispatch_id = ? ORDER BY created_at", (dispatch_id,)
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Extend task status with collaborator and quality state**

```python
# Replace ControlPlane.status in team_control/service.py
    def status(self, dispatch_id):
        task = self.store.get_task(dispatch_id)
        approvals = self.store.pending_approvals(dispatch_id)
        git_cwd = task["worktree_path"] if task.get("worktree_path") else self.context.root
        actual_head_sha = run_argv(["git", "rev-parse", "HEAD"], git_cwd).stdout.strip()
        return {
            "task": task,
            "events": self.store.list_events(dispatch_id),
            "agents": self.store.list_agent_status(dispatch_id),
            "blockers": self.store.list_blockers(dispatch_id),
            "reviews": self.store.list_reviews(dispatch_id),
            "evidence": self.store.list_evidence(dispatch_id),
            "pending_approvals": approvals,
            "effective_state": "NEEDS_HUMAN_APPROVAL" if approvals else task["state"],
            "actual_head_sha": actual_head_sha,
            "head_drift": actual_head_sha != task["current_head_sha"],
        }
```

- [ ] **Step 5: Implement hashing and distilled JSON output**

```python
# team_control/evidence.py
import hashlib
import json
import uuid
from pathlib import Path

from .git_context import canonical_under
from .store import utc_now


class EvidenceManager:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def record(self, dispatch_id, kind, path, source_sha=None):
        safe_path = canonical_under(self.context.root, path)
        digest = hashlib.sha256(safe_path.read_bytes()).hexdigest()
        record = {
            "schema_version": 1,
            "evidence_id": str(uuid.uuid4()),
            "dispatch_id": dispatch_id,
            "kind": kind,
            "path": str(safe_path.relative_to(self.context.root)),
            "sha256": digest,
            "source_sha": source_sha,
            "created_at": utc_now(),
        }
        return self.store.add_evidence(record)

    def write_summary(self, dispatch_id):
        target = self.context.root / "artifacts" / "dispatches" / dispatch_id / "evidence-index.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "generated_at": utc_now(),
            "evidence": self.store.list_evidence(dispatch_id),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target
```

- [ ] **Step 6: Add a valid example Dispatch Record**

```json
{
  "schema_version": 1,
  "dispatch_id": "20260808-example",
  "title": "MVP 0 example",
  "objective": "Demonstrate the minimum task contract",
  "risk_level": "L1",
  "state": "PLANNED",
  "task_base_sha": "0000000000000000000000000000000000000000",
  "owner": "Codex"
}
```

- [ ] **Step 7: Run and commit evidence tests**

Run: `python3 -m unittest tests.test_evidence -v`

Expected: `Ran 2 tests ... OK`.

```bash
git add -- team_control/evidence.py team_control/store.py team_control/service.py tests/test_evidence.py examples/dispatches/mvp0-example.json
git diff --cached --check
git commit -m "feat(control-plane): add evidence integrity index"
```

### Task 10: Deterministic JSON command interface

**Files:**
- Create: `team_control/cli.py`
- Create: `team_control/__main__.py`
- Create: `scripts/team-control`
- Create: `scripts/worktree-doctor`
- Modify: `team_control/store.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke tests**

```python
# tests/test_cli.py
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_repo, run


class CliTests(unittest.TestCase):
    def test_init_start_and_status_emit_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            init_result = run(["python3", "-m", "team_control", "--repo", str(repo), "init"], Path.cwd())
            self.assertEqual(json.loads(init_result.stdout)["status"], "initialized")
            run(["python3", "-m", "team_control", "--repo", str(repo), "start",
                 "--dispatch-id", "20260808-008", "--title", "CLI", "--objective", "Emit JSON", "--risk", "L1",
                 "--agent", "codex", "--slug", "cli-smoke"], Path.cwd())
            status = run(["python3", "-m", "team_control", "--repo", str(repo), "status",
                          "--dispatch-id", "20260808-008"], Path.cwd())
            payload = json.loads(status.stdout)
            self.assertEqual(payload["task"]["state"], "DISPATCHED")
            self.assertTrue(Path(payload["task"]["worktree_path"]).is_dir())

    def test_domain_error_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run(["python3", "-m", "team_control", "--repo", str(repo), "init"], Path.cwd())
            result = run(["python3", "-m", "team_control", "--repo", str(repo), "start",
                          "--dispatch-id", "bad/id", "--title", "Bad", "--objective", "Reject", "--risk", "L1",
                          "--agent", "codex", "--slug", "bad"],
                         Path.cwd(), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "BOUNDARY_ERROR")
```

- [ ] **Step 2: Run tests and verify the missing module entrypoint**

Run: `python3 -m unittest tests.test_cli -v`

Expected: `FAIL` or `ERROR` because `team_control.__main__` does not exist.

- [ ] **Step 3: Add approval listing to `ControlStore`**

```python
# Add inside ControlStore
    def list_approvals(self, dispatch_id=None):
        with self.read_connection() as connection:
            if dispatch_id:
                rows = connection.execute(
                    "SELECT * FROM approvals WHERE dispatch_id = ? ORDER BY expires_at", (dispatch_id,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM approvals ORDER BY expires_at").fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Implement the JSON CLI**

```python
# team_control/cli.py
import argparse
import json
import sys
from pathlib import Path

from .doctor import WorktreeDoctor
from .errors import TeamControlError
from .git_context import RepoContext
from .service import ControlPlane
from .store import ControlStore


def emit(value, stream=sys.stdout):
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser():
    parser = argparse.ArgumentParser(prog="team-control")
    parser.add_argument("--repo", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    start = commands.add_parser("start")
    start.add_argument("--dispatch-id", required=True)
    start.add_argument("--title", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--risk", choices=("L1", "L2", "L3"), required=True)
    start.add_argument("--agent", required=True)
    start.add_argument("--slug", required=True)
    status = commands.add_parser("status")
    status.add_argument("--dispatch-id", required=True)
    transition = commands.add_parser("transition")
    transition.add_argument("--dispatch-id", required=True)
    transition.add_argument("--to", required=True)
    transition.add_argument("--reason", required=True)
    approvals = commands.add_parser("approvals")
    approvals.add_argument("--dispatch-id")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("mode", choices=("inspect", "repair"))
    doctor.add_argument("--dispatch-id", required=True)
    doctor.add_argument("--agent", required=True)
    doctor.add_argument("--slug", required=True)
    doctor.add_argument("--base-sha", required=True)
    return parser


def execute(args):
    context = RepoContext.discover(Path(args.repo))
    store = ControlStore.for_repo(context)
    if args.command == "init":
        store.initialize()
        return {"status": "initialized", "database": str(store.path)}
    if not store.path.is_file():
        from .errors import ContractError
        raise ContractError("control plane is not initialized; run init first")
    control = ControlPlane(context, store)
    if args.command == "start":
        return control.start_write_task(
            args.dispatch_id, args.title, args.objective, args.risk, args.agent, args.slug
        )
    if args.command == "status":
        return control.status(args.dispatch_id)
    if args.command == "transition":
        return control.transition(args.dispatch_id, args.to, args.reason)
    if args.command == "approvals":
        return {"approvals": store.list_approvals(args.dispatch_id)}
    if args.command == "doctor":
        doctor = WorktreeDoctor(context, store)
        report = doctor.inspect(args.dispatch_id, args.agent, args.slug, args.base_sha)
        return report if args.mode == "inspect" else doctor.repair(report)
    raise AssertionError(args.command)


def main(argv=None):
    try:
        result = execute(build_parser().parse_args(argv))
        emit(result)
        return 0
    except TeamControlError as exc:
        emit({"error": {"code": exc.code, "message": str(exc)}}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# team_control/__main__.py
from .cli import main


raise SystemExit(main())
```

- [ ] **Step 5: Add stable POSIX wrappers**

```sh
#!/bin/sh
# scripts/team-control
set -eu
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd -P)
cd "$repo_root"
exec python3 -m team_control --repo "$repo_root" "$@"
```

```sh
#!/bin/sh
# scripts/worktree-doctor
set -eu
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd -P)
cd "$repo_root"
exec python3 -m team_control --repo "$repo_root" doctor "$@"
```

Run: `chmod +x scripts/team-control scripts/worktree-doctor`

- [ ] **Step 6: Run shell syntax, CLI, and full tests**

Run: `sh -n scripts/team-control && sh -n scripts/worktree-doctor`

Expected: exit `0`, no output.

Run: `python3 -m unittest tests.test_cli -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 7: Commit the command interface**

```bash
git add -- team_control/cli.py team_control/__main__.py team_control/store.py scripts/team-control scripts/worktree-doctor tests/test_cli.py
git diff --cached --check
git commit -m "feat(control-plane): expose deterministic JSON commands"
```

### Task 11: Project Skill and no-CLI operating guide

**Files:**
- Create: `.agents/skills/ai-software-engineering-team/SKILL.md`
- Create: `USER_OPERATING_GUIDE.md`
- Test: `tests/test_skill_contract.py`

- [ ] **Step 1: Write failing Skill contract tests**

```python
# tests/test_skill_contract.py
import unittest
from pathlib import Path


class SkillContractTests(unittest.TestCase):
    def test_skill_preserves_codex_control_and_minor_risk_boundary(self):
        text = Path(".agents/skills/ai-software-engineering-team/SKILL.md").read_text(encoding="utf-8")
        for required in (
            "Codex is the only engineering authority",
            "handoff.md",
            "scripts/repo-health.sh",
            "scripts/team-control",
            "scripts/worktree-doctor",
            "Minor",
            "NEEDS_HUMAN_APPROVAL",
            "Never run git reset --hard",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_user_guide_contains_no_cli_phrases(self):
        text = Path("USER_OPERATING_GUIDE.md").read_text(encoding="utf-8")
        for phrase in ("进入软件工程团队", "查看当前任务状态", "哪些事项等我批准", "暂停任务", "继续任务"):
            self.assertIn(phrase, text)
```

- [ ] **Step 2: Run tests and verify missing files**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: `ERROR` with `FileNotFoundError` for the Skill or guide.

- [ ] **Step 3: Create the project Skill**

```markdown
---
name: ai-software-engineering-team
description: Use when the user starts, checks, pauses, resumes, reviews, approves, or integrates a software engineering task in this repository.
---

# AI Software Engineering Team

Codex is the only engineering authority. Human approves strategy, external actions, production, permission expansion, sensitive data, irreversible actions, and required review degradation.

## Start every request

1. Read `handoff.md`, `CODEX_AGENT_DISPATCH_PROTOCOL.md`, `AGENT_ROLE_AND_MODEL_MATRIX.md`, `SOFTWARE_ENGINEERING_WORKFLOW.md`, and `GIT_WORKFLOW.md`.
2. Run `scripts/repo-health.sh` from the main repository root.
3. Run `scripts/team-control init`, then reconcile every unfinished `PREPARED` operation before any new write.
4. Convert the request into the seven-question Dispatch Record and classify it L1/L2/L3.
5. Ask Human only when a documented confirmation trigger is hit.

## Natural-language intents

- Start/build/fix → create the Dispatch Record, then use `scripts/team-control start`.
- Status/blockers → use `scripts/team-control status` and report evidence, not only percentages.
- Pause → transition to `PAUSE_REQUESTED`, stop new work, wait for writer acknowledgement, then transition to `PAUSED`.
- Resume → revalidate Git, locks, blockers, and saved resume state before transition.
- Pending approvals → use `scripts/team-control approvals`; bind approval to dispatch, action, target SHA, and request hash.
- Worktree creation failure or Minor risk → inspect with `scripts/worktree-doctor`; repair only when it reports a repairable classification.

## Engineering boundaries

- One writing task equals one short branch, one Worktree, and one writing Agent.
- Codex alone manages `main`, merges, conflicts, Worktree lifecycle, branch deletion, and `handoff.md`.
- Reviewer is independent and read-only; Claude Code is the L3 final independent quality gate.
- Never run git reset --hard, git clean -xdf, force deletion, shell eval, or unscoped cleanup.
- Preserve unknown directories, branches, metadata, commits, and dirty files; mark the task `BLOCKED` when safety cannot be proven.

## Completion

Run applicable tests, independent Review, integration verification, evidence indexing, and Mimo inventory. Do not mark complete without paths, SHA values, test results, Review disposition, residual risks, and required Human approvals.
```

- [ ] **Step 4: Create the no-CLI operating guide**

```markdown
# AI 软件工程团队操作手册（MVP 0）

## 你如何使用

日常只需要在 Codex 中说自然语言，不需要 VS Code，也不需要自己输入命令。

- “进入软件工程团队，帮我实现……”
- “查看当前任务状态和阻塞。”
- “哪些事项等我批准？”
- “暂停任务 20260808-008。”
- “继续任务 20260808-008。”
- “让独立 Reviewer 检查后再整合。”

## Codex 会自动完成什么

Codex 读取项目交接和协议，检查 Git 健康，建立七问派活单，创建隔离 Worktree，调度 Agent，维护状态，组织测试和 Review，并在安全门禁满足后整合。

## Minor 风险如何处理

这里的 Minor 指 `git worktree add` 失败后可能残留目录、分支或 Worktree metadata。Codex 会先检查实际状态：只有分支仍位于任务基线、目标目录没有未知文件且 metadata 可以安全重建时，Doctor 才会修复。发现未提交修改、额外提交或未知目录时，任务转为 `BLOCKED`，不会自动删除。

## 什么时候需要你确认

删除或覆盖关键原件、批量迁移、生产或真实业务系统、外部发送、凭据或敏感数据、权限扩大、不可逆动作，以及 Claude 验收降级，需要你明确确认。

## MVP 0 的界面

MVP 0 的主界面就是 Codex。前端只读工作台属于 MVP 1；完成 MVP 0 验收后再建设。
```

- [ ] **Step 5: Run and commit Skill contract tests**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: `Ran 2 tests ... OK`.

```bash
git add -- .agents/skills/ai-software-engineering-team/SKILL.md USER_OPERATING_GUIDE.md tests/test_skill_contract.py
git diff --cached --check
git commit -m "docs(control-plane): add project skill and operating guide"
```

### Task 12: End-to-end acceptance and Codex-only handoff

**Files:**
- Create: `tests/test_mvp0_end_to_end.py`
- Create: `artifacts/dispatches/20260808-mvp0-acceptance/verification.md`
- Modify: `handoff.md` (Codex only, after independent Review and acceptance)

- [ ] **Step 1: Write the end-to-end lifecycle test**

```python
# tests/test_mvp0_end_to_end.py
import tempfile
import unittest
from pathlib import Path

from team_control.doctor import WorktreeDoctor
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class Mvp0EndToEndTests(unittest.TestCase):
    def test_task_pause_resume_review_accept_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            control = ControlPlane(context, store)
            task = control.create_task("20260808-009", "E2E", "Complete lifecycle", "L1")
            for target in ("DISPATCHED", "IN_PROGRESS", "PAUSE_REQUESTED", "PAUSED", "IN_PROGRESS",
                           "REVIEWING", "ACCEPTED", "INTEGRATED", "RELEASED", "CLOSED"):
                task = control.transition("20260808-009", target, "e2e")
            self.assertEqual(task["state"], "CLOSED")
            sequences = [event["sequence"] for event in store.list_events("20260808-009")]
            self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
```

- [ ] **Step 2: Run all automated verification**

Run:

```bash
sh -n scripts/new-agent-worktree.sh
sh -n scripts/repo-health.sh
sh -n scripts/team-control
sh -n scripts/worktree-doctor
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: every command exits `0`; all tests pass; `git diff --check` has no output.

- [ ] **Step 3: Perform independent L2 Review**

Give the Reviewer read-only access to:

```text
design spec
this implementation plan
main...task-branch diff
full test output
Minor-risk Doctor test evidence
approval replay and operation reconciliation evidence
```

Expected Review output: `ACCEPT`, or `MODIFY` with file/line evidence. Resolve every `Critical` and `High`; rerun the full suite after modifications.

- [ ] **Step 4: Write the verification artifact with observed literals**

Run `git merge-base main HEAD` and `git rev-parse HEAD`, then use `apply_patch` to create `artifacts/dispatches/20260808-mvp0-acceptance/verification.md`. Record the observed full task-base SHA and candidate SHA as literal values. Also record the exact unittest count and `OK` result, shell syntax result, Doctor scenario result, approval replay/drift result, operation reconciliation result, the actual independent Review report path and `ACCEPT` disposition, plus every residual risk with owner and next action. If no residual risk exists, write the literal sentence `Residual risks: None recorded.` Do not leave bracketed tokens or template markers in the committed artifact.

- [ ] **Step 5: Commit the acceptance evidence**

```bash
git add -- tests/test_mvp0_end_to_end.py artifacts/dispatches/20260808-mvp0-acceptance/verification.md
git diff --cached --check
git commit -m "test(control-plane): verify MVP 0 lifecycle"
```

- [ ] **Step 6: Codex integrates and updates `handoff.md`**

Only after Review is `ACCEPT`, Codex merges according to `GIT_WORKFLOW.md`, reruns the full suite on `main`, and updates `handoff.md`. The new `MVP 0 Control Plane` section must contain the literal status `ACCEPTED and integrated`, the observed full integration SHA, the resolved absolute runtime database path, the Skill path, the internal health command, one concrete status-command example using `20260808-009`, the Minor-risk Doctor rule, the verification artifact path, and the statement that MVP 1 planning requires Human confirmation. Do not leave angle-bracket or bracket placeholders in `handoff.md`.

Codex then commits the handoff update as a separate mainline-controlled commit and removes the task Worktree only after confirming it is clean and integrated.

## Design coverage map

| MVP 0 design requirement | Implemented by |
|---|---|
| Canonical repository boundary and safe argv execution | Task 1 |
| Versioned task/event/approval/evidence/agent/review/blocker contracts | Task 2 |
| Lifecycle, pause checkpoint, and validated resume | Task 3 and Task 5 |
| Git-common-dir SQLite and single physical writer | Task 4 |
| Monotonic events and task state | Task 5 |
| Action-level Human approval, nonce, SHA binding, atomic consumption | Task 6 |
| Recoverable Git/SQLite operation protocol | Task 7 |
| Accepted Minor Worktree risk inspection and fail-closed repair | Task 8 |
| Agent progress, blockers, reviews, evidence hashes, distilled artifacts | Task 9 |
| Deterministic internal interface and zero-CLI Human experience | Task 10 and Task 11 |
| Full-suite verification, independent Review, evidence, handoff | Task 12 |
| MVP 1/2/3, GitHub, cloud remain excluded | Scope section and Task 11 guide |

## Final plan verification checklist

- [ ] Every MVP 0 design requirement maps to a task above.
- [ ] Runtime state is shared through the Git common directory and never committed.
- [ ] Codex is the only authority; Orchestrator is the only SQLite writer; each Worktree has one code writer.
- [ ] Pause requires a safe checkpoint before `PAUSED` and validates `resume_state` before continuing.
- [ ] Approval nonce consumption and `PREPARED` operation creation are one SQLite transaction.
- [ ] Every control-plane Git mutation has a postcondition verifier and fail-closed reconciliation.
- [ ] Minor means only the accepted Worktree creation residue risk.
- [ ] Doctor never deletes unknown directories, advanced branches, dirty files, or unverified metadata.
- [ ] All subprocesses use argv arrays; no shell string execution exists in Python.
- [ ] Human can operate MVP 0 entirely through Codex natural language.
- [ ] MVP 1, MVP 2, MVP 3, GitHub Remote, and cloud services remain out of scope.
