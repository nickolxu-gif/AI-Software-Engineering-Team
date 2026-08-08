import fcntl
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import validate_record
from .errors import (
    ApprovalError,
    ContractError,
    ReconciliationError,
    TeamControlError,
)
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
    phase TEXT NOT NULL CHECK (
        phase IN ('PREPARED', 'COMMITTED', 'FAILED', 'BLOCKED')
    ),
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


MIN_APPROVAL_NONCE_LENGTH = 16
OPERATION_PHASES = frozenset(("PREPARED", "COMMITTED", "FAILED", "BLOCKED"))
TERMINAL_OPERATION_PHASES = frozenset(("COMMITTED", "FAILED", "BLOCKED"))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_approval_nonce(value, error_type):
    # Callers must generate approval nonces with a cryptographically secure,
    # high-entropy generator; length is only the enforceable minimum here.
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) < MIN_APPROVAL_NONCE_LENGTH
    ):
        raise error_type(
            "approval nonce must be a non-empty string of at least 16 characters"
        )
    return value


def parse_approval_expiry(value):
    if not isinstance(value, str):
        raise ApprovalError("approval expiry is invalid")
    normalized = value
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        expires_at = datetime.fromisoformat(normalized)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("timezone is missing")
    except ValueError as error:
        raise ApprovalError("approval expiry is invalid") from error
    return expires_at.astimezone(timezone.utc)


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


def _validate_operation_inputs(action, request_hash, target_sha, idempotency_key):
    if (
        not isinstance(action, str)
        or ACTION_RE.fullmatch(action) is None
        or ".." in action
    ):
        raise ReconciliationError("operation action is invalid")
    if not isinstance(request_hash, str) or SHA256_RE.fullmatch(request_hash) is None:
        raise ReconciliationError("operation request hash is invalid")
    if not isinstance(target_sha, str) or GIT_SHA_RE.fullmatch(target_sha) is None:
        raise ReconciliationError("operation target SHA is invalid")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ReconciliationError("operation idempotency key is invalid")


def _validate_json_value(value):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReconciliationError("operation result must be strict JSON")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReconciliationError("operation result keys must be strings")
            _validate_json_value(item)
        return
    raise ReconciliationError("operation result must be strict JSON")


def _dump_operation_result(result):
    if not isinstance(result, dict):
        raise ReconciliationError("operation result must be a JSON object")
    _validate_json_value(result)
    try:
        return json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationError("operation result must be strict JSON") from error


def _load_operation_result(value):
    if value is None:
        return None

    def reject_constant(constant):
        raise ValueError("invalid JSON constant: %s" % constant)

    try:
        result = json.loads(value, parse_constant=reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReconciliationError("stored operation result is invalid") from error
    if not isinstance(result, dict):
        raise ReconciliationError("stored operation result is not an object")
    _validate_json_value(result)
    return result


class _ControlledOperationSession:
    def __init__(self, store):
        self._store = store

    def prepare_operation(
        self, dispatch_id, action, request_hash, target_sha, idempotency_key
    ):
        return self._store._prepare_operation_durable(
            dispatch_id, action, request_hash, target_sha, idempotency_key
        )

    def operation_for_idempotency(self, idempotency_key):
        return self._store._operation_for_idempotency(idempotency_key)

    def get_task(self, dispatch_id):
        return self._store.get_task(dispatch_id)

    def prepared_operations(self):
        return self._store.prepared_operations()

    def finish_operation(self, operation_id, phase, result):
        return self._store._finish_operation_durable(operation_id, phase, result)


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
    def _control_lock(self):
        self._validate_repo_paths()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock_file:
            lock_acquired = False
            try:
                self._acquire_lock(lock_file)
                lock_acquired = True
                yield
            finally:
                if lock_acquired:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def mutation(self):
        with self._control_lock():
            with self._transaction() as connection:
                yield connection

    @contextmanager
    def controlled_operation(self):
        """Hold the repository control lock across durable Git operation phases."""
        with self._control_lock():
            yield _ControlledOperationSession(self)

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
    def _operation_from_row(row):
        if row is None:
            return None
        phase = row["phase"]
        if phase not in OPERATION_PHASES:
            raise ReconciliationError("stored operation phase is invalid")
        return {
            "operation_id": row["operation_id"],
            "dispatch_id": row["dispatch_id"],
            "action": row["action"],
            "request_hash": row["request_hash"],
            "target_sha": row["target_sha"],
            "phase": phase,
            "result": _load_operation_result(row["result_json"]),
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _prepare_operation_in_transaction(
        connection,
        dispatch_id,
        action,
        request_hash,
        target_sha,
        idempotency_key,
    ):
        task = connection.execute(
            "SELECT 1 FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        if task is None:
            raise KeyError(dispatch_id)

        existing = connection.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            requested = {
                "dispatch_id": dispatch_id,
                "action": action,
                "request_hash": request_hash,
                "target_sha": target_sha,
            }
            if all(existing[field] == value for field, value in requested.items()):
                return ControlStore._operation_from_row(existing)
            raise ReconciliationError(
                "operation idempotency key was used for another request"
            )

        operation_id = str(uuid.uuid4())
        now = utc_now()
        connection.execute(
            """INSERT INTO operations (
                   operation_id, dispatch_id, action, request_hash,
                   target_sha, phase, result_json, idempotency_key,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, 'PREPARED', NULL, ?, ?, ?)""",
            (
                operation_id,
                dispatch_id,
                action,
                request_hash,
                target_sha,
                idempotency_key,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        return ControlStore._operation_from_row(row)

    def _prepare_operation_durable(
        self, dispatch_id, action, request_hash, target_sha, idempotency_key
    ):
        _validate_operation_inputs(
            action, request_hash, target_sha, idempotency_key
        )
        with self._transaction() as connection:
            return self._prepare_operation_in_transaction(
                connection,
                dispatch_id,
                action,
                request_hash,
                target_sha,
                idempotency_key,
            )

    def prepare_operation(
        self, dispatch_id, action, request_hash, target_sha, idempotency_key
    ):
        with self._control_lock():
            return self._prepare_operation_durable(
                dispatch_id,
                action,
                request_hash,
                target_sha,
                idempotency_key,
            )

    def get_operation(self, operation_id):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        return self._operation_from_row(row)

    def _operation_for_idempotency(self, idempotency_key):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._operation_from_row(row)

    def _finish_operation_durable(self, operation_id, phase, result):
        if phase not in TERMINAL_OPERATION_PHASES:
            raise ReconciliationError("invalid terminal operation phase")
        result_json = _dump_operation_result(result)
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE operations
                   SET phase = ?, result_json = ?, updated_at = ?
                   WHERE operation_id = ? AND phase = 'PREPARED'""",
                (phase, result_json, utc_now(), operation_id),
            )
            if cursor.rowcount != 1:
                raise ReconciliationError(
                    "operation is missing or is not PREPARED"
                )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return self._operation_from_row(row)

    def finish_operation(self, operation_id, phase, result):
        with self._control_lock():
            return self._finish_operation_durable(operation_id, phase, result)

    def complete_operation_callback(self, operation_id):
        with self._control_lock():
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                operation = self._operation_from_row(row)
                if operation is None:
                    raise ReconciliationError("operation is missing")
                if operation["phase"] != "COMMITTED":
                    raise ReconciliationError(
                        "callback completion requires a COMMITTED operation"
                    )
                if operation["result"].get("verified") is not True:
                    raise ReconciliationError(
                        "callback completion requires verified true"
                    )
                callback_status = operation["result"].get("callback_status")
                if callback_status == "COMPLETED":
                    return operation
                if callback_status != "PENDING":
                    raise ReconciliationError(
                        "operation callback is not PENDING"
                    )

                completed_result = dict(operation["result"])
                completed_result["callback_status"] = "COMPLETED"
                completed_json = _dump_operation_result(completed_result)
                cursor = connection.execute(
                    """UPDATE operations
                       SET result_json = ?, updated_at = ?
                       WHERE operation_id = ? AND phase = 'COMMITTED'
                         AND result_json = ?""",
                    (
                        completed_json,
                        utc_now(),
                        operation_id,
                        row["result_json"],
                    ),
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        "SELECT * FROM operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
                    current_operation = self._operation_from_row(current)
                    if (
                        current_operation is not None
                        and current_operation["phase"] == "COMMITTED"
                        and current_operation["result"].get("callback_status")
                        == "COMPLETED"
                    ):
                        return current_operation
                    raise ReconciliationError(
                        "operation callback completion lost its guard"
                    )
                completed = connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                return self._operation_from_row(completed)

    def prepared_operations(self):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM operations
                   WHERE phase = 'PREPARED'
                   ORDER BY created_at, operation_id"""
            ).fetchall()
        return [self._operation_from_row(row) for row in rows]

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
        validate_approval_nonce(nonce, ContractError)
        validate_ttl_minutes(ttl_minutes)
        approval_id = str(uuid.uuid4())
        nonce_hash = sha256_text(nonce)
        with self.mutation() as connection:
            existing = connection.execute(
                "SELECT * FROM approvals WHERE nonce_hash = ?",
                (nonce_hash,),
            ).fetchone()
            if existing is not None:
                identity = (
                    "dispatch_id",
                    "action",
                    "target_sha",
                    "request_hash",
                )
                requested = {
                    "dispatch_id": dispatch_id,
                    "action": action,
                    "target_sha": target_sha,
                    "request_hash": request_hash,
                }
                if all(existing[field] == requested[field] for field in identity):
                    return self._approval_from_row(existing)
                raise ApprovalError("approval nonce was already used for another request")
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

    def approval_task_snapshot(self, approval_id):
        with self.read_connection() as connection:
            connection.execute("BEGIN")
            approval = connection.execute(
                "SELECT dispatch_id FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if approval is None:
                raise ApprovalError("approval is missing")
            task = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?",
                (approval["dispatch_id"],),
            ).fetchone()
            if task is None:
                raise ApprovalError("approval task is missing")
            result = dict(task)
        return result

    def pending_approvals(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM approvals
                   WHERE dispatch_id = ? AND status = 'PENDING'
                     AND consumed_at IS NULL
                   ORDER BY expires_at, approval_id""",
                (dispatch_id,),
            ).fetchall()
        now = datetime.now(timezone.utc)
        return [
            self._approval_from_row(row)
            for row in rows
            if parse_approval_expiry(row["expires_at"]) > now
        ]

    def consume_approval(self, approval_id, nonce, head_observer):
        if not callable(head_observer):
            raise ApprovalError("approval HEAD observer must be callable")
        with self.mutation() as connection:
            validate_approval_nonce(nonce, ApprovalError)
            supplied_nonce_hash = sha256_text(nonce)
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
            expires_at = parse_approval_expiry(row["expires_at"])

            task = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?",
                (row["dispatch_id"],),
            ).fetchone()
            if task is None:
                raise ApprovalError("approval task is missing")

            # Every Task7-controlled Git mutation must hold this same mutation
            # lock. Non-cooperating external Git processes require Task7
            # precondition and postcondition checks around execution as well.
            observed_head = head_observer(dict(task))
            if not isinstance(observed_head, str):
                raise ApprovalError("approval observed HEAD must be a string")
            now_datetime = datetime.now(timezone.utc)
            if expires_at <= now_datetime:
                raise ApprovalError("approval expired")
            if not (
                row["target_sha"]
                == task["current_head_sha"]
                == observed_head
            ):
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
            # Task6's approval API keeps its established PREPARED row shape.
            # Task7's operation APIs expose the decoded public operation shape.
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
                     AND consumed_at IS NULL
                   ORDER BY expires_at, approval_id""",
                (dispatch_id,),
            ).fetchall()
            task = dict(task_row) if task_row is not None else None
            events = [self._event_from_row(row) for row in event_rows]
            now = datetime.now(timezone.utc)
            approvals = [
                self._approval_from_row(row)
                for row in approval_rows
                if parse_approval_expiry(row["expires_at"]) > now
            ]
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
