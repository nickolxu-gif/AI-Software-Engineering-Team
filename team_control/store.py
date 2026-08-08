import fcntl
import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import validate_record
from .errors import ApprovalError, ContractError, TeamControlError
from .git_context import canonical_under
from .state_machine import next_state


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


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_ttl_minutes(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 1
        or value > 1440
    ):
        raise ContractError("ttl_minutes must be a finite number from 1 to 1440")
    return value


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

    def create_task(self, record):
        validate_record("task", record)
        with self.mutation() as connection:
            now = utc_now()
            connection.execute(
                """INSERT INTO tasks (
                       dispatch_id, schema_version, title, objective, risk_level,
                       state, resume_state, task_base_sha, current_head_sha,
                       owner, agent, slug, branch, worktree_path, created_at,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL,
                             NULL, ?, ?)""",
                (
                    record["dispatch_id"], record["schema_version"],
                    record["title"], record["objective"], record["risk_level"],
                    record["state"], record["task_base_sha"],
                    record["task_base_sha"], record["owner"], now, now,
                ),
            )
            payload_json = json.dumps(record, sort_keys=True)
            event = {
                "schema_version": 1,
                "dispatch_id": record["dispatch_id"],
                "sequence": 1,
                "event_type": "TASK_CREATED",
                "created_at": now,
            }
            validate_record("event", event)
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    event["dispatch_id"], event["sequence"], event["event_type"],
                    payload_json, event["created_at"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?",
                (record["dispatch_id"],),
            ).fetchone()
            task = dict(row)
        return task

    def get_task(self, dispatch_id):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _event_from_row(row):
        event = {
            "schema_version": 1,
            "dispatch_id": row["dispatch_id"],
            "sequence": row["sequence"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        validate_record("event", event)
        return event

    def list_events(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE dispatch_id = ? ORDER BY sequence",
                (dispatch_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    @staticmethod
    def _approval_from_row(row):
        if row is None:
            return None
        approval = {
            "schema_version": 1,
            "approval_id": row["approval_id"],
            "dispatch_id": row["dispatch_id"],
            "action": row["action"],
            "target_sha": row["target_sha"],
            "request_hash": row["request_hash"],
            "nonce_hash": row["nonce_hash"],
            "expires_at": row["expires_at"],
            "consumed_at": row["consumed_at"],
            "idempotency_key": row["idempotency_key"],
        }
        validate_record("approval", approval)
        return approval

    def create_approval(
        self,
        dispatch_id,
        action,
        target_sha,
        request_hash,
        nonce,
        ttl_minutes,
        idempotency_key,
    ):
        if not isinstance(nonce, str) or not nonce.strip():
            raise ContractError("approval nonce must be a non-empty string")
        validate_ttl_minutes(ttl_minutes)
        approval_id = str(uuid.uuid4())
        nonce_hash = sha256_text(nonce)
        with self.mutation() as connection:
            task = connection.execute(
                "SELECT current_head_sha FROM tasks WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if task is None:
                raise KeyError(dispatch_id)
            if task["current_head_sha"] != target_sha:
                raise ApprovalError("approval target SHA does not match task head")
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
            ).isoformat()
            connection.execute(
                """INSERT INTO approvals (
                       approval_id, dispatch_id, action, target_sha,
                       request_hash, nonce_hash, expires_at, consumed_at,
                       status, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'PENDING', ?)""",
                (
                    approval_id,
                    dispatch_id,
                    action,
                    target_sha,
                    request_hash,
                    nonce_hash,
                    expires_at,
                    idempotency_key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            approval = self._approval_from_row(row)
        return approval

    def get_approval(self, approval_id):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        return self._approval_from_row(row)

    def pending_approvals(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM approvals
                   WHERE dispatch_id = ? AND status = 'PENDING'
                   ORDER BY expires_at, approval_id""",
                (dispatch_id,),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def consume_approval(self, approval_id, nonce, actual_sha):
        if not isinstance(nonce, str) or not nonce.strip():
            raise ApprovalError("approval nonce must be a non-empty string")
        if not isinstance(actual_sha, str):
            raise ApprovalError("approval actual SHA must be a string")
        supplied_nonce_hash = sha256_text(nonce)
        with self.mutation() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "PENDING"
                or row["consumed_at"] is not None
            ):
                raise ApprovalError("approval is missing or already consumed")
            if not hmac.compare_digest(row["nonce_hash"], supplied_nonce_hash):
                raise ApprovalError("approval nonce mismatch")
            try:
                expires_at = datetime.fromisoformat(row["expires_at"])
                if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                    raise ValueError("timezone is missing")
            except (TypeError, ValueError) as error:
                raise ApprovalError("approval expiry is invalid") from error
            now_datetime = datetime.now(timezone.utc)
            if expires_at <= now_datetime:
                raise ApprovalError("approval expired")
            if row["target_sha"] != actual_sha:
                raise ApprovalError("approval target SHA drifted")

            now = now_datetime.isoformat()
            operation_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO operations (
                       operation_id, dispatch_id, action, request_hash,
                       target_sha, phase, result_json, idempotency_key,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'PREPARED', NULL, ?, ?, ?)""",
                (
                    operation_id,
                    row["dispatch_id"],
                    row["action"],
                    row["request_hash"],
                    row["target_sha"],
                    row["idempotency_key"],
                    now,
                    now,
                ),
            )
            cursor = connection.execute(
                """UPDATE approvals
                   SET status = 'CONSUMED', consumed_at = ?
                   WHERE approval_id = ? AND status = 'PENDING'
                     AND consumed_at IS NULL""",
                (now, approval_id),
            )
            if cursor.rowcount != 1:
                raise ApprovalError("approval is missing or already consumed")
            operation = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            result = dict(operation)
        return result

    def status_snapshot(self, dispatch_id):
        with self.read_connection() as connection:
            connection.execute("BEGIN")
            task_row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            event_rows = connection.execute(
                "SELECT * FROM events WHERE dispatch_id = ? ORDER BY sequence",
                (dispatch_id,),
            ).fetchall()
            approval_rows = connection.execute(
                """SELECT * FROM approvals
                   WHERE dispatch_id = ? AND status = 'PENDING'
                   ORDER BY expires_at, approval_id""",
                (dispatch_id,),
            ).fetchall()
            task = dict(task_row) if task_row is not None else None
            events = [self._event_from_row(row) for row in event_rows]
            approvals = [self._approval_from_row(row) for row in approval_rows]
        return task, events, approvals

    def transition(self, dispatch_id, target, reason):
        with self.mutation() as connection:
            now = utc_now()
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(dispatch_id)

            target_state, resume_state = next_state(
                row["state"], target, row["resume_state"]
            )
            sequence = connection.execute(
                """SELECT COALESCE(MAX(sequence), 0) + 1
                     FROM events WHERE dispatch_id = ?""",
                (dispatch_id,),
            ).fetchone()[0]
            connection.execute(
                """UPDATE tasks
                   SET state = ?, resume_state = ?, updated_at = ?
                   WHERE dispatch_id = ?""",
                (target_state, resume_state, now, dispatch_id),
            )
            payload_json = json.dumps(
                {"from": row["state"], "to": target_state, "reason": reason},
                sort_keys=True,
            )
            event = {
                "schema_version": 1,
                "dispatch_id": dispatch_id,
                "sequence": sequence,
                "event_type": "STATE_CHANGED",
                "created_at": now,
            }
            validate_record("event", event)
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    event["dispatch_id"], event["sequence"], event["event_type"],
                    payload_json, event["created_at"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            task = dict(row)
        return task

    def attach_worktree(self, dispatch_id, agent, slug, branch, path):
        with self.mutation() as connection:
            now = utc_now()
            cursor = connection.execute(
                """UPDATE tasks
                   SET agent = ?, slug = ?, branch = ?, worktree_path = ?,
                       updated_at = ?
                   WHERE dispatch_id = ?""",
                (agent, slug, branch, str(path), now, dispatch_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(dispatch_id)
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            task = dict(row)
        return task
