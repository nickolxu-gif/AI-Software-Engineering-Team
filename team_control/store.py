import fcntl
import sqlite3
from contextlib import contextmanager
from pathlib import Path


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
        self.path = Path(path).resolve()
        self.lock_path = Path(lock_path).resolve()

    @classmethod
    def for_repo(cls, context):
        runtime = context.common_dir / "team" / "runtime"
        return cls(runtime / "team.db", runtime / "control-plane.lock")

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

    @contextmanager
    def mutation(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            connection = None
            try:
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
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def read_connection(self):
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()
