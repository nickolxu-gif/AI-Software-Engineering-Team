import fcntl
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import TeamControlError
from .git_context import canonical_under


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


class StoreBusyError(TeamControlError):
    code = "STORE_BUSY"


class ControlStore:
    def __init__(self, path, lock_path, lock_timeout=5.0, lock_poll_interval=0.05):
        self.path = Path(path).resolve()
        self.lock_path = Path(lock_path).resolve()
        self.lock_timeout = self._positive_interval(lock_timeout, "lock_timeout")
        self.lock_poll_interval = self._positive_interval(
            lock_poll_interval, "lock_poll_interval"
        )
        self._common_dir = None

    @staticmethod
    def _positive_interval(value, name):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("%s must be a positive finite number" % name)
        return float(value)

    @classmethod
    def for_repo(cls, context):
        common_dir = Path(context.common_dir).resolve(strict=True)
        runtime = canonical_under(common_dir, common_dir / "team" / "runtime")
        path = canonical_under(common_dir, runtime / "team.db")
        lock_path = canonical_under(common_dir, runtime / "control-plane.lock")
        store = cls(path, lock_path)
        store._common_dir = common_dir
        return store

    def _validate_repo_paths(self):
        if self._common_dir is None:
            return
        canonical_under(self._common_dir, self.path.parent)
        canonical_under(self._common_dir, self.path)
        canonical_under(self._common_dir, self.lock_path)

    def initialize(self):
        with self.mutation() as connection:
            for statement in SCHEMA.split(";"):
                statement = statement.strip()
                if statement:
                    connection.execute(statement)

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _acquire_lock(self, lock_file):
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fcntl.flock(
                    lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StoreBusyError("control store is busy")
                time.sleep(min(self.lock_poll_interval, remaining))

    @contextmanager
    def mutation(self):
        self._validate_repo_paths()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock_file:
            connection = None
            lock_acquired = False
            try:
                self._acquire_lock(lock_file)
                lock_acquired = True
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                if connection is not None:
                    connection.rollback()
                raise
            finally:
                try:
                    if connection is not None:
                        connection.close()
                finally:
                    if lock_acquired:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def read_connection(self):
        self._validate_repo_paths()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()
