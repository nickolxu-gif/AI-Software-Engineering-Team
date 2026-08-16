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

from .contracts import (
    DISPATCH_RE,
    HASH_RE,
    INTENT_ACTIONS,
    SHA_RE,
    UUID_RE,
    RFC3339_RE,
    validate_project_registry_display_name,
    validate_task_intake_text,
    validate_record,
)
from .errors import (
    ApprovalError,
    BoundaryError,
    CursorStaleError,
    ContractError,
    ReconciliationError,
    SchemaMigrationRequiredError,
    SchemaUnsupportedError,
    TeamControlError,
)
from .git_context import canonical_under, run_argv
from .state_machine import next_state


def _sqlite_authorizer_action(name, fallback, sqlite_module=sqlite3):
    """Read a stable SQLite authorizer action across CPython sqlite3 versions."""
    return getattr(sqlite_module, name, fallback)


SQLITE_ATTACH_ACTION = _sqlite_authorizer_action("SQLITE_ATTACH", 24)
SQLITE_DETACH_ACTION = _sqlite_authorizer_action("SQLITE_DETACH", 25)

PROJECT_REGISTRY_SCHEMA = """CREATE TABLE project_registry (
    project_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    common_dir_path TEXT NOT NULL,
    root_device INTEGER NOT NULL,
    root_inode INTEGER NOT NULL,
    root_mode INTEGER NOT NULL,
    common_dir_device INTEGER NOT NULL,
    common_dir_inode INTEGER NOT NULL,
    common_dir_mode INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
)"""
PROJECT_REGISTRY_LEGACY_SCHEMA = """CREATE TABLE project_registry (
    project_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL UNIQUE,
    common_dir_path TEXT NOT NULL UNIQUE,
    root_device INTEGER NOT NULL,
    root_inode INTEGER NOT NULL,
    root_mode INTEGER NOT NULL,
    common_dir_device INTEGER NOT NULL,
    common_dir_inode INTEGER NOT NULL,
    common_dir_mode INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
)"""
PROJECT_REGISTRY_MIGRATED_SCHEMA = """CREATE TABLE project_registry_migrated (
    project_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    common_dir_path TEXT NOT NULL,
    root_device INTEGER NOT NULL,
    root_inode INTEGER NOT NULL,
    root_mode INTEGER NOT NULL,
    common_dir_device INTEGER NOT NULL,
    common_dir_inode INTEGER NOT NULL,
    common_dir_mode INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
)"""
PROJECT_REGISTRY_INDEXES = {
    "project_registry_active_display_name": (
        "CREATE UNIQUE INDEX project_registry_active_display_name "
        "ON project_registry(display_name) WHERE status = 'ACTIVE'"
    ),
    "project_registry_active_root_path": (
        "CREATE UNIQUE INDEX project_registry_active_root_path "
        "ON project_registry(root_path) WHERE status = 'ACTIVE'"
    ),
    "project_registry_active_common_dir_path": (
        "CREATE UNIQUE INDEX project_registry_active_common_dir_path "
        "ON project_registry(common_dir_path) WHERE status = 'ACTIVE'"
    ),
    "project_registry_active_root_identity": (
        "CREATE UNIQUE INDEX project_registry_active_root_identity "
        "ON project_registry(root_device, root_inode, root_mode) "
        "WHERE status = 'ACTIVE'"
    ),
    "project_registry_active_common_dir_identity": (
        "CREATE UNIQUE INDEX project_registry_active_common_dir_identity "
        "ON project_registry(common_dir_device, common_dir_inode, common_dir_mode) "
        "WHERE status = 'ACTIVE'"
    ),
}
PROJECT_REGISTRY_EVENTS_SCHEMA = """CREATE TABLE project_registry_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_registry(project_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('PROJECT_REGISTERED', 'PROJECT_RETIRED')
    ),
    created_at TEXT NOT NULL
)"""
PROJECT_REGISTRY_EVENTS_MIGRATED_SCHEMA = """CREATE TABLE project_registry_events_migrated (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_registry_migrated(project_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('PROJECT_REGISTERED', 'PROJECT_RETIRED')
    ),
    created_at TEXT NOT NULL
)"""

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
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
    action TEXT NOT NULL,
    target_sha TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    confirmation_hash TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'APPLIED', 'REJECTED', 'BLOCKED')
    ),
    result_code TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_intake_requests (
    intake_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    context TEXT,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'ACKNOWLEDGED')),
    result_code TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_intake_handlings (
    intake_id TEXT PRIMARY KEY REFERENCES task_intake_requests(intake_id),
    dispatch_id TEXT NOT NULL UNIQUE REFERENCES tasks(dispatch_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('DISPATCHED', 'BLOCKED')),
    handled_at TEXT NOT NULL
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
    report_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
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
""" + PROJECT_REGISTRY_SCHEMA.replace(
    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
) + ";\n" + PROJECT_REGISTRY_EVENTS_SCHEMA.replace(
    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
) + ";\n" + "\n".join(
    "CREATE UNIQUE INDEX IF NOT EXISTS %s%s;" % (
        name, statement.split(name, 1)[1]
    )
    for name, statement in PROJECT_REGISTRY_INDEXES.items()
) + "\n"


class StoreBusyError(TeamControlError):
    code = "STORE_BUSY"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_timestamp(value):
    normalized = value
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    if normalized[10] == "t":
        normalized = normalized[:10] + "T" + normalized[11:]
    leap_second = normalized[17:19] == "60"
    if leap_second:
        normalized = normalized[:17] + "59" + normalized[19:]
    parsed = datetime.fromisoformat(normalized)
    if leap_second:
        parsed += timedelta(seconds=1)
    return parsed.astimezone(timezone.utc).isoformat()


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


MIN_APPROVAL_NONCE_LENGTH = 16
OPERATION_PHASES = frozenset(("PREPARED", "COMMITTED", "FAILED", "BLOCKED"))
TERMINAL_OPERATION_PHASES = frozenset(("COMMITTED", "FAILED", "BLOCKED"))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
INTENT_STATUSES = frozenset(("PENDING", "APPLIED", "REJECTED", "BLOCKED"))
TERMINAL_INTENT_STATUSES = INTENT_STATUSES - {"PENDING"}
INTENT_RESULT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_PENDING_INTENT_BATCH = 25
MAX_TASK_INTAKE_RECORDS = 100
MAX_TASK_INTAKE_LIST_OFFSET = 10000
MAX_PROJECT_REGISTRY_ENTRIES = 20
PROJECT_REGISTRY_STATUSES = frozenset(("ACTIVE", "RETIRED"))
TASK_INTAKE_STATUSES = frozenset(("PENDING", "ACKNOWLEDGED"))
TASK_INTAKE_LEGACY_SCHEMA = """CREATE TABLE task_intake_requests (
    intake_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    context TEXT,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING')),
    result_code TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
TASK_INTAKE_CURRENT_SCHEMA = TASK_INTAKE_LEGACY_SCHEMA.replace(
    "status IN ('PENDING')", "status IN ('PENDING', 'ACKNOWLEDGED')"
)
TASK_INTAKE_HANDLING_SCHEMA = """CREATE TABLE task_intake_handlings (
    intake_id TEXT PRIMARY KEY REFERENCES task_intake_requests(intake_id),
    dispatch_id TEXT NOT NULL UNIQUE REFERENCES tasks(dispatch_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('DISPATCHED', 'BLOCKED')),
    handled_at TEXT NOT NULL
)"""
TASK_INTAKE_REQUIRED_SCHEMA_COLUMNS = {
    "task_intake_requests": frozenset((
        "intake_id", "title", "objective", "context", "request_hash",
        "status", "result_code", "idempotency_key", "created_at", "updated_at",
    )),
    "task_intake_handlings": frozenset((
        "intake_id", "dispatch_id", "disposition", "handled_at",
    )),
}
REQUIRED_SCHEMA_COLUMNS = {
    "tasks": frozenset((
        "dispatch_id", "schema_version", "title", "objective", "risk_level",
        "state", "resume_state", "task_base_sha", "current_head_sha", "owner",
        "agent", "slug", "branch", "worktree_path", "created_at", "updated_at",
    )),
    "events": frozenset((
        "dispatch_id", "sequence", "event_type", "payload_json", "created_at",
    )),
    "approvals": frozenset((
        "approval_id", "dispatch_id", "action", "target_sha", "request_hash",
        "nonce_hash", "expires_at", "consumed_at", "status", "idempotency_key",
    )),
    "operations": frozenset((
        "operation_id", "dispatch_id", "action", "request_hash", "target_sha",
        "phase", "result_json", "idempotency_key", "created_at", "updated_at",
    )),
    "intents": frozenset((
        "intent_id", "dispatch_id", "action", "target_sha", "request_hash",
        "confirmation_hash", "status", "result_code", "idempotency_key",
        "created_at", "updated_at",
    )),
    **TASK_INTAKE_REQUIRED_SCHEMA_COLUMNS,
    "evidence": frozenset((
        "evidence_id", "dispatch_id", "kind", "path", "sha256", "source_sha",
        "created_at",
    )),
    "agents": frozenset((
        "dispatch_id", "agent_id", "role", "model", "state", "progress",
        "report_json", "updated_at",
    )),
    "reviews": frozenset((
        "review_id", "dispatch_id", "reviewer", "disposition", "source_sha",
        "report_path", "report_sha256", "created_at",
    )),
    "blockers": frozenset((
        "blocker_id", "dispatch_id", "reason", "owner", "status",
        "resolution_condition", "created_at", "updated_at",
    )),
    "project_registry": frozenset((
        "project_id", "display_name", "root_path", "common_dir_path",
        "root_device", "root_inode", "root_mode", "common_dir_device",
        "common_dir_inode", "common_dir_mode",
        "status", "created_at", "updated_at", "retired_at",
    )),
    "project_registry_events": frozenset((
        "event_id", "project_id", "event_type", "created_at",
    )),
}


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
        self,
        dispatch_id,
        action,
        request_hash,
        target_sha,
        idempotency_key,
        result=None,
    ):
        return self._store._prepare_operation_durable(
            dispatch_id,
            action,
            request_hash,
            target_sha,
            idempotency_key,
            result=result,
        )

    def operation_for_idempotency(self, idempotency_key):
        return self._store._operation_for_idempotency(idempotency_key)

    def get_task(self, dispatch_id):
        return self._store.get_task(dispatch_id)

    def transition(self, dispatch_id, target, reason):
        return self._store._transition_durable(dispatch_id, target, reason)

    def transition_to_resume_state(self, dispatch_id, reason):
        return self._store._transition_to_resume_state_durable(
            dispatch_id, reason
        )

    def finish_intent(self, intent_id, status, result_code, event_type=None):
        return self._store._finish_intent_durable(
            intent_id, status, result_code, event_type=event_type
        )

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
        with self._control_lock():
            connection = self._connect()
            try:
                journal_mode = connection.execute(
                    "PRAGMA journal_mode = WAL"
                ).fetchone()[0]
                if journal_mode.lower() != "wal":
                    raise ReconciliationError(
                        "control store requires WAL journal mode"
                    )
            finally:
                connection.close()
            with self._transaction() as connection:
                for statement in SCHEMA.split(";"):
                    statement = statement.strip()
                    if statement:
                        connection.execute(statement)
                self._migrate_reviews_schema(connection)
                self._migrate_task_intake_schema(connection)
                self._migrate_project_registry_schema(connection)
                self._validate_task_intake_handling_schema(connection)
                self._validate_project_registry_schema(connection)

    def _migrate_reviews_schema(self, connection):
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(reviews)")
        }
        if "report_sha256" not in columns:
            connection.execute(
                "ALTER TABLE reviews ADD COLUMN report_sha256 TEXT"
            )
            columns = {
                row["name"]: row
                for row in connection.execute("PRAGMA table_info(reviews)")
            }

        needs_rebuild = (
            not columns["report_sha256"]["notnull"]
            or not columns["report_path"]["notnull"]
        )
        if not needs_rebuild:
            return
        rows = connection.execute("SELECT * FROM reviews").fetchall()
        if rows:
            if self._common_dir is None:
                raise ReconciliationError(
                    "review migration requires a repository-bound store"
                )
            from .evidence import _read_task_regular_file

            for row in rows:
                task = self._task_row_or_error(connection, row["dispatch_id"])
                report_path = row["report_path"]
                digest = "0" * 64
                if not isinstance(report_path, str) or not report_path:
                    report_path = (
                        "artifacts/dispatches/%s/legacy-missing-review-%s"
                        % (row["dispatch_id"], row["review_id"])
                    )
                else:
                    try:
                        _, contents = _read_task_regular_file(
                            self._common_dir, task, report_path
                        )
                    except TeamControlError:
                        pass
                    else:
                        digest = hashlib.sha256(contents).hexdigest()
                connection.execute(
                    """UPDATE reviews
                       SET report_path = ?, report_sha256 = ?
                       WHERE review_id = ?""",
                    (report_path, digest, row["review_id"]),
                )

        residue = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'reviews_migrated'"""
        ).fetchone()
        if residue is not None:
            raise ReconciliationError("review migration residue is present")
        connection.execute(
            """CREATE TABLE reviews_migrated (
                   review_id TEXT PRIMARY KEY,
                   dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
                   reviewer TEXT NOT NULL,
                   disposition TEXT NOT NULL,
                   source_sha TEXT NOT NULL,
                   report_path TEXT NOT NULL,
                   report_sha256 TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            """INSERT INTO reviews_migrated (
                   review_id, dispatch_id, reviewer, disposition, source_sha,
                   report_path, report_sha256, created_at
               )
               SELECT review_id, dispatch_id, reviewer, disposition, source_sha,
                      report_path, report_sha256, created_at
               FROM reviews"""
        )
        connection.execute("DROP TABLE reviews")
        connection.execute("ALTER TABLE reviews_migrated RENAME TO reviews")

    def _migrate_task_intake_schema(self, connection):
        schema = connection.execute(
            """SELECT sql FROM sqlite_master
               WHERE type = 'table' AND name = 'task_intake_requests'"""
        ).fetchone()
        if schema is None:
            raise ReconciliationError("task intake schema is missing after initialization")
        residue = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'task_intake_requests_migrated'"""
        ).fetchone()
        if residue is not None:
            raise ReconciliationError("task intake migration residue is present")
        self._validate_task_intake_schema_objects(connection)
        normalized = self._normalized_schema_sql(schema["sql"])
        if normalized == self._normalized_schema_sql(TASK_INTAKE_CURRENT_SCHEMA):
            return
        if normalized != self._normalized_schema_sql(TASK_INTAKE_LEGACY_SCHEMA):
            raise SchemaUnsupportedError(
                "task intake schema is not a supported legacy version"
            )
        rows = connection.execute(
            "SELECT * FROM task_intake_requests"
        ).fetchall()
        if any(row["status"] != "PENDING" for row in rows):
            raise ReconciliationError("legacy task intake status is unsupported")
        connection.execute(
            """CREATE TABLE task_intake_requests_migrated (
                   intake_id TEXT PRIMARY KEY,
                   title TEXT NOT NULL,
                   objective TEXT NOT NULL,
                   context TEXT,
                   request_hash TEXT NOT NULL,
                   status TEXT NOT NULL CHECK (
                       status IN ('PENDING', 'ACKNOWLEDGED')
                   ),
                   result_code TEXT NOT NULL,
                   idempotency_key TEXT NOT NULL UNIQUE,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            """INSERT INTO task_intake_requests_migrated (
                   intake_id, title, objective, context, request_hash, status,
                   result_code, idempotency_key, created_at, updated_at
               ) SELECT intake_id, title, objective, context, request_hash, status,
                        result_code, idempotency_key, created_at, updated_at
                 FROM task_intake_requests"""
        )
        connection.execute("DROP TABLE task_intake_requests")
        connection.execute(
            "ALTER TABLE task_intake_requests_migrated RENAME TO task_intake_requests"
        )

    def _validate_task_intake_handling_schema(self, connection):
        schema = connection.execute(
            """SELECT sql FROM sqlite_master
               WHERE type = 'table' AND name = 'task_intake_handlings'"""
        ).fetchone()
        if schema is None:
            raise ReconciliationError(
                "task intake handling schema is missing after initialization"
            )
        if self._normalized_schema_sql(schema["sql"]) != self._normalized_schema_sql(
            TASK_INTAKE_HANDLING_SCHEMA
        ):
            raise SchemaUnsupportedError(
                "task intake handling schema is unsupported"
            )

    def _migrate_project_registry_schema(self, connection):
        schemas = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """SELECT name, sql FROM sqlite_master
                   WHERE type = 'table' AND name IN (?, ?)""",
                ("project_registry", "project_registry_events"),
            )
        }
        if set(schemas) != {"project_registry", "project_registry_events"}:
            raise ReconciliationError(
                "project registry schema is missing after initialization"
            )
        normalized_registry = self._normalized_schema_sql(
            schemas["project_registry"]
        )
        if normalized_registry == self._normalized_schema_sql(
            PROJECT_REGISTRY_SCHEMA
        ):
            return
        if (
            normalized_registry
            != self._normalized_schema_sql(PROJECT_REGISTRY_LEGACY_SCHEMA)
            or self._normalized_schema_sql(schemas["project_registry_events"])
            != self._normalized_schema_sql(PROJECT_REGISTRY_EVENTS_SCHEMA)
        ):
            raise SchemaUnsupportedError(
                "project registry schema is not a supported legacy version"
            )
        persistent_triggers = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'trigger'
                 AND tbl_name IN ('project_registry', 'project_registry_events')"""
        ).fetchone()
        if persistent_triggers is not None:
            raise SchemaUnsupportedError(
                "project registry schema has unsupported objects"
            )
        indexes = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """SELECT name, sql FROM sqlite_master
                   WHERE type = 'index' AND sql IS NOT NULL
                     AND tbl_name IN ('project_registry', 'project_registry_events')"""
            )
        }
        if set(indexes) != set(PROJECT_REGISTRY_INDEXES) or any(
            self._normalized_schema_sql(indexes[name])
            != self._normalized_schema_sql(expected)
            for name, expected in PROJECT_REGISTRY_INDEXES.items()
        ):
            raise SchemaUnsupportedError(
                "project registry schema has unsupported objects"
            )
        orphan_event = connection.execute(
            """SELECT 1
               FROM project_registry_events AS event
               LEFT JOIN project_registry AS project
                 ON project.project_id = event.project_id
               WHERE project.project_id IS NULL
               LIMIT 1"""
        ).fetchone()
        if orphan_event is not None:
            raise SchemaUnsupportedError(
                "project registry legacy events contain an orphan project"
            )
        connection.execute(PROJECT_REGISTRY_MIGRATED_SCHEMA)
        connection.execute(PROJECT_REGISTRY_EVENTS_MIGRATED_SCHEMA)
        migrated_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(project_registry_events_migrated)"
        ).fetchall()
        if {
            (row["table"], row["from"], row["to"])
            for row in migrated_foreign_keys
        } != {("project_registry_migrated", "project_id", "project_id")}:
            raise ReconciliationError(
                "project registry event migration foreign key is invalid"
            )
        columns = (
            "project_id", "display_name", "root_path", "common_dir_path",
            "root_device", "root_inode", "root_mode", "common_dir_device",
            "common_dir_inode", "common_dir_mode", "status", "created_at",
            "updated_at", "retired_at",
        )
        column_list = ", ".join(columns)
        connection.execute(
            "INSERT INTO project_registry_migrated (%s) "
            "SELECT %s FROM project_registry" % (column_list, column_list)
        )
        connection.execute(
            """INSERT INTO project_registry_events_migrated (
                   event_id, project_id, event_type, created_at
               ) SELECT event_id, project_id, event_type, created_at
               FROM project_registry_events"""
        )
        connection.execute("DROP TABLE project_registry_events")
        connection.execute("DROP TABLE project_registry")
        connection.execute(
            "ALTER TABLE project_registry_migrated RENAME TO project_registry"
        )
        connection.execute(
            "ALTER TABLE project_registry_events_migrated "
            "RENAME TO project_registry_events"
        )
        for statement in PROJECT_REGISTRY_INDEXES.values():
            connection.execute(statement)

    def _validate_project_registry_schema(self, connection):
        expected_schemas = {
            "project_registry": PROJECT_REGISTRY_SCHEMA,
            "project_registry_events": PROJECT_REGISTRY_EVENTS_SCHEMA,
        }
        rows = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """SELECT name, sql FROM sqlite_master
                   WHERE type = 'table' AND name IN (?, ?)""",
                tuple(expected_schemas),
            )
        }
        if set(rows) != set(expected_schemas):
            raise ReconciliationError(
                "project registry schema is missing after initialization"
            )
        if (
            self._normalized_schema_sql(rows["project_registry"])
            == self._normalized_schema_sql(PROJECT_REGISTRY_LEGACY_SCHEMA)
            and self._normalized_schema_sql(rows["project_registry_events"])
            == self._normalized_schema_sql(PROJECT_REGISTRY_EVENTS_SCHEMA)
        ):
            legacy_indexes = {
                row["name"]: row["sql"]
                for row in connection.execute(
                """SELECT name, sql FROM sqlite_master
                   WHERE type = 'index' AND sql IS NOT NULL
                     AND tbl_name IN ('project_registry', 'project_registry_events')"""
                )
            }
            legacy_triggers = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'trigger'
                     AND tbl_name IN ('project_registry', 'project_registry_events')"""
            ).fetchone()
            if legacy_triggers is not None:
                raise SchemaUnsupportedError(
                    "project registry schema has unsupported objects"
                )
            if legacy_indexes:
                if (
                    set(legacy_indexes) != set(PROJECT_REGISTRY_INDEXES)
                    or any(
                        self._normalized_schema_sql(legacy_indexes[name])
                        != self._normalized_schema_sql(expected)
                        for name, expected in PROJECT_REGISTRY_INDEXES.items()
                    )
                ):
                    raise SchemaUnsupportedError(
                        "project registry schema has unsupported objects"
                    )
            raise SchemaMigrationRequiredError(
                "project registry schema migration is required; run init"
            )
        for table, expected_schema in expected_schemas.items():
            if self._normalized_schema_sql(rows[table]) != self._normalized_schema_sql(
                expected_schema
            ):
                raise SchemaUnsupportedError(
                    "project registry schema is unsupported"
                )
        self._validate_project_registry_schema_objects(connection)

    @staticmethod
    def _validate_project_registry_schema_objects(connection):
        indexes = {
            row["name"]: row["sql"]
            for row in connection.execute(
            """SELECT name, sql FROM sqlite_master
               WHERE type = 'index' AND sql IS NOT NULL
                 AND tbl_name IN ('project_registry', 'project_registry_events')"""
            )
        }
        persistent_triggers = connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'trigger'
                 AND tbl_name IN ('project_registry', 'project_registry_events')"""
        ).fetchall()
        if set(indexes) != set(PROJECT_REGISTRY_INDEXES) or persistent_triggers:
            raise SchemaUnsupportedError(
                "project registry schema has unsupported objects"
            )
        for name, expected in PROJECT_REGISTRY_INDEXES.items():
            if (
                ControlStore._normalized_schema_sql(indexes[name])
                != ControlStore._normalized_schema_sql(expected)
            ):
                raise SchemaUnsupportedError(
                    "project registry schema has unsupported objects"
                )

    @staticmethod
    def _validate_task_intake_schema_objects(connection):
        extra_indexes = connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'index' AND sql IS NOT NULL
                 AND tbl_name IN ('task_intake_requests', 'task_intake_handlings')"""
        ).fetchall()
        persistent_triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL"
        ).fetchall()
        temporary_triggers = connection.execute(
            "SELECT name FROM sqlite_temp_master WHERE type = 'trigger'"
        ).fetchall()
        if extra_indexes or persistent_triggers or temporary_triggers:
            raise SchemaUnsupportedError(
                "task intake schema has unsupported objects"
            )

    @staticmethod
    def _normalized_schema_sql(value):
        normalized = re.sub(r"\s+", " ", value).replace('"', "").strip()
        normalized = re.sub(r"\(\s+", "(", normalized)
        normalized = re.sub(r"\s+\)", ")", normalized)
        return re.sub(r"\s*,\s*", ",", normalized)

    @staticmethod
    def _deny_database_attachment(action, argument1, argument2, database, source):
        if action in (SQLITE_ATTACH_ACTION, SQLITE_DETACH_ACTION):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @classmethod
    def _configure_connection(cls, connection):
        connection.row_factory = sqlite3.Row
        connection.set_authorizer(cls._deny_database_attachment)
        return connection

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            self._configure_connection(connection)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except BaseException:
            connection.close()
            raise

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

    def observe_status(self, dispatch_id, observer):
        """Observe one SQLite snapshot and repository HEAD under one lock."""
        with self._control_lock():
            snapshot = self.status_snapshot(dispatch_id)
            return observer(snapshot)

    @contextmanager
    def controlled_operation(self):
        """Hold the repository control lock across durable Git operation phases."""
        with self._control_lock():
            yield _ControlledOperationSession(self)

    @contextmanager
    def read_connection(self):
        self._validate_repo_paths()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            self._configure_connection(connection)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 2000")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def read_snapshot(self):
        """Provide one SQLite read-only snapshot and always release it by rollback."""
        with self.read_connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise
            connection.rollback()

    def require_schema_compatible(self):
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)

    def _require_schema_compatible_in_connection(self, connection):
        objects = {
            row["name"]: row["type"]
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE name IN (%s)"
                % ", ".join("?" for _ in REQUIRED_SCHEMA_COLUMNS),
                tuple(REQUIRED_SCHEMA_COLUMNS),
            )
        }
        columns = {
            table: {
                row["name"]
                for row in connection.execute("PRAGMA table_info(%s)" % table)
            }
            for table, object_type in objects.items()
            if object_type == "table"
        }
        missing = sorted(set(REQUIRED_SCHEMA_COLUMNS) - set(objects))
        if missing:
            raise SchemaMigrationRequiredError(
                "control database is missing required tables: %s; run init"
                % ", ".join(missing)
            )
        non_tables = sorted(
            table for table, object_type in objects.items() if object_type != "table"
        )
        if non_tables:
            raise SchemaUnsupportedError(
                "control database has required objects that are not tables: %s"
                % ", ".join(non_tables)
            )
        incomplete = {
            table: sorted(required - columns[table])
            for table, required in REQUIRED_SCHEMA_COLUMNS.items()
            if not required.issubset(columns[table])
        }
        if incomplete:
            details = "; ".join(
                "%s(%s)" % (table, ", ".join(missing_columns))
                for table, missing_columns in sorted(incomplete.items())
            )
            raise SchemaUnsupportedError(
                "control database is missing required columns: %s" % details
            )
        schemas = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?)",
                ("task_intake_requests", "task_intake_handlings"),
            )
        }
        request_schema = self._normalized_schema_sql(
            schemas["task_intake_requests"]
        )
        if request_schema == self._normalized_schema_sql(TASK_INTAKE_LEGACY_SCHEMA):
            raise SchemaMigrationRequiredError(
                "task intake schema requires initialization"
            )
        if request_schema != self._normalized_schema_sql(TASK_INTAKE_CURRENT_SCHEMA):
            raise SchemaUnsupportedError("task intake schema is unsupported")
        if self._normalized_schema_sql(schemas["task_intake_handlings"]) != (
            self._normalized_schema_sql(TASK_INTAKE_HANDLING_SCHEMA)
        ):
            raise SchemaUnsupportedError("task intake handling schema is unsupported")
        self._validate_task_intake_schema_objects(connection)
        self._validate_project_registry_schema(connection)

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

    def create_or_get_write_task(
        self, record, agent, slug, branch, worktree_path
    ):
        """Atomically reserve one stable write-task identity per dispatch."""
        validate_record("task", record)
        requested_path = str(worktree_path)
        with self.mutation() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?",
                (record["dispatch_id"],),
            ).fetchone()
            if row is None:
                now = utc_now()
                connection.execute(
                    """INSERT INTO tasks (
                           dispatch_id, schema_version, title, objective,
                           risk_level, state, resume_state, task_base_sha,
                           current_head_sha, owner, agent, slug, branch,
                           worktree_path, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                                 NULL, ?, ?)""",
                    (
                        record["dispatch_id"], record["schema_version"],
                        record["title"], record["objective"],
                        record["risk_level"], record["state"],
                        record["task_base_sha"], record["task_base_sha"],
                        record["owner"], agent, slug, branch, now, now,
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
                        event["dispatch_id"], event["sequence"],
                        event["event_type"], payload_json, event["created_at"],
                    ),
                )
            else:
                requested = {
                    "schema_version": record["schema_version"],
                    "title": record["title"],
                    "objective": record["objective"],
                    "risk_level": record["risk_level"],
                    "task_base_sha": record["task_base_sha"],
                    "current_head_sha": record["task_base_sha"],
                    "owner": record["owner"],
                }
                metadata_matches = all(
                    row[field] == value for field, value in requested.items()
                )
                stored_identity = tuple(
                    row[field]
                    for field in ("agent", "slug", "branch", "worktree_path")
                )
                identity_matches = stored_identity in {
                    (None, None, None, None),
                    (agent, slug, branch, None),
                    (agent, slug, branch, requested_path),
                }
                if (
                    not metadata_matches
                    or not identity_matches
                    or row["state"] not in ("PLANNED", "DISPATCHED")
                ):
                    raise ReconciliationError(
                        "existing write task does not match the start request"
                    )
                if stored_identity == (None, None, None, None):
                    connection.execute(
                        """UPDATE tasks
                           SET agent = ?, slug = ?, branch = ?, updated_at = ?
                           WHERE dispatch_id = ?""",
                        (agent, slug, branch, utc_now(), record["dispatch_id"]),
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
    def _project_registry_entry_from_row(row):
        if row is None:
            return None
        if row["status"] not in PROJECT_REGISTRY_STATUSES:
            raise ReconciliationError("stored project registry status is invalid")
        return {
            "project_id": row["project_id"],
            "display_name": row["display_name"],
            "root_path": row["root_path"],
            "common_dir_path": row["common_dir_path"],
            "root_device": row["root_device"],
            "root_inode": row["root_inode"],
            "root_mode": row["root_mode"],
            "common_dir_device": row["common_dir_device"],
            "common_dir_inode": row["common_dir_inode"],
            "common_dir_mode": row["common_dir_mode"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "retired_at": row["retired_at"],
        }

    @staticmethod
    def _validate_project_registry_project_id(project_id):
        if not isinstance(project_id, str) or UUID_RE.fullmatch(project_id) is None:
            raise ContractError("project registry project_id is invalid")
        return project_id

    @classmethod
    def _validate_project_registry_entry_inputs(
        cls, project_id, display_name, root_path, common_dir_path,
        root_device, root_inode, root_mode, common_dir_device,
        common_dir_inode, common_dir_mode,
    ):
        cls._validate_project_registry_project_id(project_id)
        validate_project_registry_display_name(display_name)
        for value, label in ((root_path, "root_path"),
                             (common_dir_path, "common_dir_path")):
            if (
                not isinstance(value, str)
                or not value
                or "\0" in value
                or not Path(value).is_absolute()
            ):
                raise ContractError("project registry %s is invalid" % label)
        for value, label in (
            (root_device, "root_device"),
            (root_inode, "root_inode"),
            (root_mode, "root_mode"),
            (common_dir_device, "common_dir_device"),
            (common_dir_inode, "common_dir_inode"),
            (common_dir_mode, "common_dir_mode"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError("project registry %s is invalid" % label)

    @staticmethod
    def _validate_project_registry_limit(limit):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_PROJECT_REGISTRY_ENTRIES
        ):
            raise ContractError(
                "project registry limit must be an integer from 1 to %d"
                % MAX_PROJECT_REGISTRY_ENTRIES
            )
        return limit

    @staticmethod
    def _validate_project_registry_cursor(cursor, identifier_key):
        if (
            type(cursor) is not dict
            or set(cursor) != {"created_at", identifier_key}
            or not isinstance(cursor["created_at"], str)
            or len(cursor["created_at"]) > 64
            or RFC3339_RE.fullmatch(cursor["created_at"]) is None
            or not isinstance(cursor[identifier_key], str)
            or UUID_RE.fullmatch(cursor[identifier_key]) is None
        ):
            raise ContractError("project registry cursor is invalid")
        try:
            normalize_timestamp(cursor["created_at"])
        except (IndexError, ValueError):
            raise ContractError("project registry cursor is invalid")
        return cursor["created_at"], str(uuid.UUID(cursor[identifier_key]))

    @staticmethod
    def _require_project_registry_cursor_anchor(
        connection, table, identifier_column, cursor_values, filter_column=None,
        filter_value=None,
    ):
        query = "SELECT 1 FROM %s WHERE created_at = ? AND %s = ?" % (
            table, identifier_column,
        )
        parameters = list(cursor_values)
        if filter_column is not None:
            query += " AND %s = ?" % filter_column
            parameters.append(filter_value)
        if connection.execute(query, tuple(parameters)).fetchone() is None:
            raise CursorStaleError("project registry cursor is stale; restart paging")

    def create_project_registry_entry(
        self, project_id, display_name, root_path, common_dir_path,
        root_device, root_inode, root_mode, common_dir_device,
        common_dir_inode, common_dir_mode,
    ):
        display_name = validate_project_registry_display_name(display_name)
        self._validate_project_registry_entry_inputs(
            project_id, display_name, root_path, common_dir_path,
            root_device, root_inode, root_mode, common_dir_device,
            common_dir_inode, common_dir_mode,
        )
        created_at = utc_now()
        try:
            with self.mutation() as connection:
                self._require_schema_compatible_in_connection(connection)
                active_count = connection.execute(
                    "SELECT COUNT(*) FROM project_registry WHERE status = 'ACTIVE'"
                ).fetchone()[0]
                if active_count >= MAX_PROJECT_REGISTRY_ENTRIES:
                    raise ContractError(
                        "project registry has reached its active project limit"
                    )
                connection.execute(
                    """INSERT INTO project_registry (
                           project_id, display_name, root_path, common_dir_path,
                           root_device, root_inode, root_mode,
                           common_dir_device, common_dir_inode, common_dir_mode,
                           status, created_at, updated_at, retired_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, NULL)""",
                    (
                        project_id, display_name, root_path, common_dir_path,
                        root_device, root_inode, root_mode,
                        common_dir_device, common_dir_inode, common_dir_mode,
                        created_at, created_at,
                    ),
                )
                connection.execute(
                    """INSERT INTO project_registry_events (
                           event_id, project_id, event_type, created_at
                       ) VALUES (?, ?, 'PROJECT_REGISTERED', ?)""",
                    (str(uuid.uuid4()), project_id, created_at),
                )
                row = connection.execute(
                    "SELECT * FROM project_registry WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise ContractError(
                "project registry entry conflicts with an existing project"
            ) from error
        return self._project_registry_entry_from_row(row)

    def get_project_registry_entry(self, project_id):
        self._validate_project_registry_project_id(project_id)
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_id = ?", (project_id,)
            ).fetchone()
        return self._project_registry_entry_from_row(row)

    def list_project_registry_entries(
        self, status=None, limit=MAX_PROJECT_REGISTRY_ENTRIES
    ):
        return self.list_project_registry_entries_page(status=status, limit=limit)[
            "entries"
        ]

    def list_project_registry_entries_page(
        self, status=None, limit=MAX_PROJECT_REGISTRY_ENTRIES, cursor=None
    ):
        if status is not None and status not in PROJECT_REGISTRY_STATUSES:
            raise ContractError("project registry status is invalid")
        self._validate_project_registry_limit(limit)
        query = "SELECT * FROM project_registry"
        parameters = []
        conditions = []
        cursor_values = None
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        if cursor is not None:
            created_at, project_id = self._validate_project_registry_cursor(
                cursor, "project_id"
            )
            cursor_values = (created_at, project_id)
            conditions.append(
                "(created_at > ? OR (created_at = ? AND project_id > ?))"
            )
            parameters.extend((created_at, created_at, project_id))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at, project_id LIMIT ?"
        parameters.append(limit + 1)
        with self.read_snapshot() as connection:
            self._require_schema_compatible_in_connection(connection)
            if cursor_values is not None:
                self._require_project_registry_cursor_anchor(
                    connection, "project_registry", "project_id", cursor_values,
                    "status" if status is not None else None, status,
                )
            rows = connection.execute(query, tuple(parameters)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more:
            next_cursor = {
                "created_at": rows[-1]["created_at"],
                "project_id": rows[-1]["project_id"],
            }
        return {
            "entries": [self._project_registry_entry_from_row(row) for row in rows],
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def retire_project_registry_entry(self, project_id):
        self._validate_project_registry_project_id(project_id)
        retired_at = utc_now()
        with self.mutation() as connection:
            self._require_schema_compatible_in_connection(connection)
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_id = ?", (project_id,)
            ).fetchone()
            entry = self._project_registry_entry_from_row(row)
            if entry is None:
                raise ContractError("project registry entry was not found")
            if entry["status"] != "ACTIVE":
                raise ContractError("project registry entry is already retired")
            connection.execute(
                """UPDATE project_registry
                   SET status = 'RETIRED', updated_at = ?, retired_at = ?
                   WHERE project_id = ?""",
                (retired_at, retired_at, project_id),
            )
            connection.execute(
                """INSERT INTO project_registry_events (
                       event_id, project_id, event_type, created_at
                   ) VALUES (?, ?, 'PROJECT_RETIRED', ?)""",
                (str(uuid.uuid4()), project_id, retired_at),
            )
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_id = ?", (project_id,)
            ).fetchone()
        return self._project_registry_entry_from_row(row)

    def list_project_registry_events(self, project_id=None, limit=20, cursor=None):
        if project_id is not None:
            self._validate_project_registry_project_id(project_id)
        self._validate_project_registry_limit(limit)
        query = (
            "SELECT event_id, project_id, event_type, created_at "
            "FROM project_registry_events"
        )
        conditions = []
        parameters = []
        cursor_values = None
        if project_id is not None:
            conditions.append("project_id = ?")
            parameters.append(project_id)
        if cursor is not None:
            created_at, event_id = self._validate_project_registry_cursor(
                cursor, "event_id"
            )
            cursor_values = (created_at, event_id)
            conditions.append(
                "(created_at > ? OR (created_at = ? AND event_id > ?))"
            )
            parameters.extend((created_at, created_at, event_id))
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at, event_id LIMIT ?"
        parameters.append(limit + 1)
        with self.read_snapshot() as connection:
            self._require_schema_compatible_in_connection(connection)
            if cursor_values is not None:
                self._require_project_registry_cursor_anchor(
                    connection, "project_registry_events", "event_id", cursor_values,
                    "project_id" if project_id is not None else None, project_id,
                )
            rows = connection.execute(query, tuple(parameters)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [
            {
                "event_type": row["event_type"],
                "project_id": row["project_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        next_cursor = None
        if has_more:
            next_cursor = {
                "created_at": rows[-1]["created_at"],
                "event_id": rows[-1]["event_id"],
            }
        return {
            "events": events,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _intent_from_row(row):
        if row is None:
            return None
        if row["status"] not in INTENT_STATUSES:
            raise ReconciliationError("stored intent status is invalid")
        return {
            "intent_id": row["intent_id"],
            "dispatch_id": row["dispatch_id"],
            "action": row["action"],
            "target_sha": row["target_sha"],
            "request_hash": row["request_hash"],
            "confirmation_hash": row["confirmation_hash"],
            "status": row["status"],
            "result_code": row["result_code"],
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _validate_intent_fields(
        dispatch_id, action, target_sha, request_hash, confirmation_hash,
        idempotency_key,
    ):
        if not isinstance(dispatch_id, str) or not dispatch_id:
            raise ContractError("intent dispatch_id is invalid")
        if action not in INTENT_ACTIONS:
            raise ContractError("intent action is invalid")
        if not isinstance(target_sha, str) or SHA_RE.fullmatch(target_sha) is None:
            raise ContractError("intent target SHA is invalid")
        if not isinstance(request_hash, str) or HASH_RE.fullmatch(request_hash) is None:
            raise ContractError("intent request hash is invalid")
        if confirmation_hash is not None and (
            not isinstance(confirmation_hash, str)
            or HASH_RE.fullmatch(confirmation_hash) is None
        ):
            raise ContractError("intent confirmation hash is invalid")
        if not isinstance(idempotency_key, str) or UUID_RE.fullmatch(idempotency_key) is None:
            raise ContractError("intent idempotency key is invalid")

    @staticmethod
    def _next_event_sequence(connection, dispatch_id):
        return connection.execute(
            """SELECT COALESCE(MAX(sequence), 0) + 1
                 FROM events WHERE dispatch_id = ?""",
            (dispatch_id,),
        ).fetchone()[0]

    def create_intent(
        self, dispatch_id, action, target_sha, request_hash, confirmation_hash,
        idempotency_key,
    ):
        self._validate_intent_fields(
            dispatch_id, action, target_sha, request_hash, confirmation_hash,
            idempotency_key,
        )
        with self.mutation() as connection:
            existing = connection.execute(
                "SELECT * FROM intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                identity = {
                    "dispatch_id": dispatch_id,
                    "action": action,
                    "target_sha": target_sha,
                    "request_hash": request_hash,
                    "confirmation_hash": confirmation_hash,
                }
                if all(existing[field] == value for field, value in identity.items()):
                    return self._intent_from_row(existing)
                raise ReconciliationError(
                    "intent idempotency key was used for another request"
                )
            task = connection.execute(
                "SELECT 1 FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            if task is None:
                raise KeyError(dispatch_id)
            now = utc_now()
            intent_id = str(uuid.uuid4())
            try:
                connection.execute(
                    """INSERT INTO intents (
                           intent_id, dispatch_id, action, target_sha, request_hash,
                           confirmation_hash, status, result_code, idempotency_key,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', 'PENDING', ?, ?, ?)""",
                    (
                        intent_id, dispatch_id, action, target_sha, request_hash,
                        confirmation_hash, idempotency_key, now, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = connection.execute(
                    "SELECT * FROM intents WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                identity = {
                    "dispatch_id": dispatch_id,
                    "action": action,
                    "target_sha": target_sha,
                    "request_hash": request_hash,
                    "confirmation_hash": confirmation_hash,
                }
                if existing is not None and all(
                    existing[field] == value for field, value in identity.items()
                ):
                    return self._intent_from_row(existing)
                raise ReconciliationError(
                    "intent idempotency key was used for another request"
                ) from error
            event = {
                "schema_version": 1,
                "dispatch_id": dispatch_id,
                "sequence": self._next_event_sequence(connection, dispatch_id),
                "event_type": "INTENT_SUBMITTED",
                "created_at": now,
            }
            validate_record("event", event)
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    event["dispatch_id"], event["sequence"], event["event_type"],
                    json.dumps(
                        {
                            "intent_id": intent_id,
                            "action": action,
                            "target_sha": target_sha,
                            "request_hash": request_hash,
                            "status": "PENDING",
                        },
                        sort_keys=True,
                    ),
                    event["created_at"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return self._intent_from_row(row)

    def _finish_intent_durable(
        self, intent_id, status, result_code, event_type=None
    ):
        if status not in TERMINAL_INTENT_STATUSES:
            raise ContractError("intent terminal status is invalid")
        if (
            not isinstance(result_code, str)
            or INTENT_RESULT_CODE_RE.fullmatch(result_code) is None
        ):
            raise ContractError("intent result code is invalid")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = self._intent_from_row(row)
            if current["status"] != "PENDING":
                if (
                    current["status"] == status
                    and current["result_code"] == result_code
                ):
                    return current
                raise ReconciliationError("intent already has a terminal result")
            now = utc_now()
            cursor = connection.execute(
                """UPDATE intents
                   SET status = ?, result_code = ?, updated_at = ?
                   WHERE intent_id = ? AND status = 'PENDING'""",
                (status, result_code, now, intent_id),
            )
            if cursor.rowcount != 1:
                raise ReconciliationError("intent terminal update lost its guard")
            if event_type is None:
                event_type = "INTENT_%s" % status
            if not isinstance(event_type, str) or not event_type:
                raise ContractError("intent terminal event type is invalid")
            event = {
                "schema_version": 1,
                "dispatch_id": current["dispatch_id"],
                "sequence": self._next_event_sequence(
                    connection, current["dispatch_id"]
                ),
                "event_type": event_type,
                "created_at": now,
            }
            validate_record("event", event)
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    event["dispatch_id"], event["sequence"], event["event_type"],
                    json.dumps(
                        {
                            "intent_id": intent_id,
                            "status": status,
                            "result_code": result_code,
                        },
                        sort_keys=True,
                    ),
                    event["created_at"],
                ),
            )
            updated = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return self._intent_from_row(updated)

    def finish_intent(self, intent_id, status, result_code, event_type=None):
        with self._control_lock():
            return self._finish_intent_durable(
                intent_id, status, result_code, event_type=event_type
            )

    def get_intent(self, intent_id):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return self._intent_from_row(row)

    def intent_for_idempotency(self, idempotency_key):
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._intent_from_row(row)

    def list_intents(self, dispatch_id=None):
        with self.read_connection() as connection:
            if dispatch_id is None:
                rows = connection.execute(
                    "SELECT * FROM intents ORDER BY created_at, intent_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM intents WHERE dispatch_id = ?
                       ORDER BY created_at, intent_id""",
                    (dispatch_id,),
                ).fetchall()
        return [self._intent_from_row(row) for row in rows]

    def list_pending_intents(self, limit):
        if type(limit) is not int or not 1 <= limit <= MAX_PENDING_INTENT_BATCH:
            raise ContractError("pending intent limit must be an integer from 1 to 25")
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM intents WHERE status = 'PENDING'
                   ORDER BY created_at, intent_id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._intent_from_row(row) for row in rows]

    @staticmethod
    def _task_intake_from_row(row):
        if row is None:
            return None
        if row["status"] not in TASK_INTAKE_STATUSES:
            raise ReconciliationError("stored task intake status is invalid")
        return {
            "intake_id": row["intake_id"],
            "title": row["title"],
            "objective": row["objective"],
            "context": row["context"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "result_code": row["result_code"],
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _validate_task_intake_fields(
        title, objective, context, request_hash, idempotency_key,
    ):
        for value, label, maximum in (
            (title, "task intake title", 120),
            (objective, "task intake objective", 2000),
        ):
            validate_task_intake_text(value, label, maximum)
        validate_task_intake_text(
            context, "task intake context", 2000, allow_none=True,
        )
        if not isinstance(request_hash, str) or HASH_RE.fullmatch(request_hash) is None:
            raise ContractError("task intake request hash is invalid")
        if not isinstance(idempotency_key, str) or UUID_RE.fullmatch(idempotency_key) is None:
            raise ContractError("task intake idempotency key is invalid")

    def create_task_intake(
        self, title, objective, context, request_hash, idempotency_key,
    ):
        self._validate_task_intake_fields(
            title, objective, context, request_hash, idempotency_key,
        )
        with self.mutation() as connection:
            self._require_schema_compatible_in_connection(connection)
            existing = connection.execute(
                "SELECT * FROM task_intake_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            identity = {
                "title": title,
                "objective": objective,
                "context": context,
                "request_hash": request_hash,
            }
            if existing is not None:
                if all(existing[field] == value for field, value in identity.items()):
                    return self._task_intake_from_row(existing)
                raise ReconciliationError(
                    "task intake idempotency key was used for another request"
                )
            intake_count = connection.execute(
                "SELECT COUNT(*) FROM task_intake_requests WHERE status = 'PENDING'"
            ).fetchone()[0]
            if intake_count >= MAX_TASK_INTAKE_RECORDS:
                raise ContractError("task intake inbox capacity is reached")
            now = utc_now()
            intake_id = str(uuid.uuid4())
            try:
                connection.execute(
                    """INSERT INTO task_intake_requests (
                           intake_id, title, objective, context, request_hash,
                           status, result_code, idempotency_key, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'PENDING', 'PENDING', ?, ?, ?)""",
                    (
                        intake_id, title, objective, context, request_hash,
                        idempotency_key, now, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = connection.execute(
                    "SELECT * FROM task_intake_requests WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None and all(
                    existing[field] == value for field, value in identity.items()
                ):
                    return self._task_intake_from_row(existing)
                raise ReconciliationError(
                    "task intake idempotency key was used for another request"
                ) from error
            row = connection.execute(
                "SELECT * FROM task_intake_requests WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            return self._task_intake_from_row(row)

    @staticmethod
    def _validate_task_intake_pagination(limit, offset, label):
        if type(limit) is not int or not 1 <= limit <= MAX_PENDING_INTENT_BATCH:
            raise ContractError("%s limit must be an integer from 1 to 25" % label)
        if type(offset) is not int or not 0 <= offset <= MAX_TASK_INTAKE_LIST_OFFSET:
            raise ContractError("%s offset must be an integer from 0 to 10000" % label)

    def list_pending_task_intakes(self, limit, offset=0):
        self._validate_task_intake_pagination(limit, offset, "pending task intake")
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)
            rows = connection.execute(
                """SELECT * FROM task_intake_requests WHERE status = 'PENDING'
                   ORDER BY created_at, intake_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [self._task_intake_from_row(row) for row in rows]

    def get_task_intake(self, intake_id):
        if not isinstance(intake_id, str) or UUID_RE.fullmatch(intake_id) is None:
            raise ContractError("task intake ID is invalid")
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)
            row = connection.execute(
                "SELECT * FROM task_intake_requests WHERE intake_id = ?", (intake_id,)
            ).fetchone()
        return self._task_intake_from_row(row)

    def list_task_intakes(self, limit, offset=0):
        self._validate_task_intake_pagination(limit, offset, "task intake")
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)
            rows = connection.execute(
                """SELECT * FROM task_intake_requests
                   ORDER BY created_at, intake_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [self._task_intake_from_row(row) for row in rows]

    def get_task_intake_handling(self, intake_id):
        if not isinstance(intake_id, str) or UUID_RE.fullmatch(intake_id) is None:
            raise ContractError("task intake ID is invalid")
        with self.read_connection() as connection:
            self._require_schema_compatible_in_connection(connection)
            row = connection.execute(
                "SELECT * FROM task_intake_handlings WHERE intake_id = ?", (intake_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def acknowledge_task_intake(self, intake_id, dispatch_id, disposition="DISPATCHED"):
        if not isinstance(intake_id, str) or UUID_RE.fullmatch(intake_id) is None:
            raise ContractError("task intake ID is invalid")
        if not isinstance(dispatch_id, str) or DISPATCH_RE.fullmatch(dispatch_id) is None:
            raise ContractError("task intake handling dispatch ID is invalid")
        if disposition not in ("DISPATCHED", "BLOCKED"):
            raise ContractError("task intake handling disposition is invalid")
        with self._control_lock():
            with self._transaction() as connection:
                self._require_schema_compatible_in_connection(connection)
                row = connection.execute(
                    "SELECT * FROM task_intake_requests WHERE intake_id = ?",
                    (intake_id,),
                ).fetchone()
                current = self._task_intake_from_row(row)
                if current is None:
                    raise KeyError(intake_id)
                if current["status"] == "ACKNOWLEDGED":
                    handling = connection.execute(
                        "SELECT * FROM task_intake_handlings WHERE intake_id = ?", (intake_id,)
                    ).fetchone()
                    if (
                        handling is None
                        or handling["dispatch_id"] != dispatch_id
                        or handling["disposition"] != disposition
                    ):
                        raise ContractError("task intake is already handled by another record")
                    return current
                task = connection.execute(
                    "SELECT dispatch_id FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
                ).fetchone()
                if task is None:
                    raise ContractError("task intake handling requires an existing dispatch")
                if disposition == "BLOCKED":
                    blocker = connection.execute(
                        """SELECT 1 FROM blockers
                           WHERE dispatch_id = ? AND status = 'OPEN' LIMIT 1""",
                        (dispatch_id,),
                    ).fetchone()
                    if blocker is None:
                        raise ContractError("blocked task intake requires an open blocker")
                now = utc_now()
                existing_dispatch = connection.execute(
                    "SELECT intake_id FROM task_intake_handlings WHERE dispatch_id = ?",
                    (dispatch_id,),
                ).fetchone()
                if existing_dispatch is not None:
                    raise ContractError("task intake dispatch is already bound")
                try:
                    connection.execute(
                        """INSERT INTO task_intake_handlings (
                               intake_id, dispatch_id, disposition, handled_at
                           ) VALUES (?, ?, ?, ?)""",
                        (intake_id, dispatch_id, disposition, now),
                    )
                except sqlite3.IntegrityError as error:
                    raise ContractError(
                        "task intake dispatch is already bound"
                    ) from error
                cursor = connection.execute(
                    """UPDATE task_intake_requests
                       SET status = 'ACKNOWLEDGED', result_code = ?,
                           updated_at = ?
                       WHERE intake_id = ? AND status = 'PENDING'""",
                    (disposition, now, intake_id),
                )
                if cursor.rowcount != 1:
                    raise ReconciliationError("task intake acknowledgement lost its guard")
                updated = connection.execute(
                    "SELECT * FROM task_intake_requests WHERE intake_id = ?",
                    (intake_id,),
                ).fetchone()
                return self._task_intake_from_row(updated)

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
        result=None,
    ):
        result_json = None if result is None else _dump_operation_result(result)
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
               ) VALUES (?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?, ?)""",
            (
                operation_id,
                dispatch_id,
                action,
                request_hash,
                target_sha,
                result_json,
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
        self,
        dispatch_id,
        action,
        request_hash,
        target_sha,
        idempotency_key,
        result=None,
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
                result=result,
            )

    def prepare_operation(
        self,
        dispatch_id,
        action,
        request_hash,
        target_sha,
        idempotency_key,
        result=None,
    ):
        with self._control_lock():
            return self._prepare_operation_durable(
                dispatch_id,
                action,
                request_hash,
                target_sha,
                idempotency_key,
                result=result,
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

    def operation_for_idempotency(self, idempotency_key):
        return self._operation_for_idempotency(idempotency_key)

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
            "status": row["status"],
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

    def list_approvals(self, dispatch_id=None):
        with self.read_connection() as connection:
            if dispatch_id is None:
                rows = connection.execute(
                    "SELECT * FROM approvals ORDER BY expires_at, approval_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM approvals
                       WHERE dispatch_id = ?
                       ORDER BY expires_at, approval_id""",
                    (dispatch_id,),
                ).fetchall()
        return [self._approval_from_row(row) for row in rows]

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
            agent_rows = connection.execute(
                "SELECT * FROM agents WHERE dispatch_id = ? ORDER BY agent_id",
                (dispatch_id,),
            ).fetchall()
            blocker_rows = connection.execute(
                """SELECT * FROM blockers
                   WHERE dispatch_id = ? ORDER BY created_at, blocker_id""",
                (dispatch_id,),
            ).fetchall()
            review_rows = connection.execute(
                """SELECT * FROM reviews
                   WHERE dispatch_id = ? ORDER BY created_at, review_id""",
                (dispatch_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """SELECT * FROM evidence
                   WHERE dispatch_id = ? ORDER BY created_at, evidence_id""",
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
            agents = [self._agent_status_from_row(row) for row in agent_rows]
            blockers = [self._blocker_from_row(row) for row in blocker_rows]
            reviews = [self._review_from_row(row) for row in review_rows]
            evidence = [self._evidence_from_row(row) for row in evidence_rows]
        return (
            task, events, approvals, agents, blockers, reviews, evidence
        )

    def _transition_in_transaction(self, connection, dispatch_id, target, reason):
        now = utc_now()
        row = connection.execute(
            "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise KeyError(dispatch_id)

        target_state, resume_state = next_state(
            row["state"], target, row["resume_state"]
        )
        sequence = self._next_event_sequence(connection, dispatch_id)
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
        return dict(row)

    def _transition_durable(self, dispatch_id, target, reason):
        with self._transaction() as connection:
            return self._transition_in_transaction(
                connection, dispatch_id, target, reason
            )

    def _transition_to_resume_state_durable(self, dispatch_id, reason):
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT resume_state FROM tasks WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise KeyError(dispatch_id)
            return self._transition_in_transaction(
                connection, dispatch_id, row["resume_state"], reason
            )

    def transition(self, dispatch_id, target, reason):
        with self.mutation() as connection:
            return self._transition_in_transaction(
                connection, dispatch_id, target, reason
            )

    def reserve_worktree_identity(self, dispatch_id, agent, slug, branch):
        with self.mutation() as connection:
            now = utc_now()
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(dispatch_id)
            stored_identity = tuple(
                row[field] for field in ("agent", "slug", "branch")
            )
            identity = (agent, slug, branch)
            if (
                stored_identity != (None, None, None)
                and stored_identity != identity
            ):
                raise ReconciliationError(
                    "worktree identity is already reserved"
                )
            connection.execute(
                """UPDATE tasks
                   SET agent = ?, slug = ?, branch = ?, updated_at = ?
                   WHERE dispatch_id = ?""",
                (agent, slug, branch, now, dispatch_id),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            task = dict(row)
        return task

    def attach_worktree(self, dispatch_id, agent, slug, branch, path):
        with self.mutation() as connection:
            now = utc_now()
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(dispatch_id)
            identity = (agent, slug, branch, str(path))
            stored_identity = tuple(
                row[field]
                for field in ("agent", "slug", "branch", "worktree_path")
            )
            allowed_identities = {
                (None, None, None, None),
                (agent, slug, branch, None),
                identity,
            }
            if stored_identity not in allowed_identities:
                raise ReconciliationError(
                    "worktree identity is already attached"
                )
            connection.execute(
                """UPDATE tasks
                   SET agent = ?, slug = ?, branch = ?, worktree_path = ?,
                       updated_at = ?
                   WHERE dispatch_id = ?""",
                (agent, slug, branch, str(path), now, dispatch_id),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            task = dict(row)
        return task

    @staticmethod
    def _task_row_or_error(connection, dispatch_id):
        row = connection.execute(
            "SELECT * FROM tasks WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise ReconciliationError("task is missing: %s" % dispatch_id)
        return row

    def _validate_source_commit(
        self, task, source_sha, target_sha=None, require_current=False
    ):
        if self._common_dir is None:
            raise ReconciliationError(
                "source verification requires a repository-bound store"
            )
        if not isinstance(source_sha, str) or GIT_SHA_RE.fullmatch(source_sha) is None:
            raise ReconciliationError(
                "source SHA must be a 40- or 64-character commit SHA"
            )
        target = target_sha or task["current_head_sha"]
        if not isinstance(target, str) or GIT_SHA_RE.fullmatch(target) is None:
            raise ReconciliationError("task current HEAD is invalid")
        repository_root = self._common_dir.parent
        source_check = run_argv(
            ["git", "cat-file", "-e", "%s^{commit}" % source_sha],
            repository_root,
            check=False,
        )
        if source_check.returncode != 0:
            raise ReconciliationError("source SHA is not an existing Git commit")
        target_check = run_argv(
            ["git", "cat-file", "-e", "%s^{commit}" % target],
            repository_root,
            check=False,
        )
        if target_check.returncode != 0:
            raise ReconciliationError("task current HEAD is not an existing commit")
        ancestry = run_argv(
            ["git", "merge-base", "--is-ancestor", source_sha, target],
            repository_root,
            check=False,
        )
        if ancestry.returncode != 0:
            raise ReconciliationError(
                "source SHA is not bound to the task current HEAD"
            )
        if require_current and source_sha != target:
            raise ReconciliationError("ACCEPT review must target current head")

    def review_stale_reasons(self, task, review, observed_head_sha):
        reasons = []
        try:
            self._validate_source_commit(
                task,
                review["source_sha"],
                target_sha=observed_head_sha,
                require_current=review["disposition"] == "ACCEPT",
            )
        except TeamControlError as error:
            reasons.append(str(error))
        try:
            from .evidence import _read_task_regular_file

            relative, contents = _read_task_regular_file(
                self._common_dir, task, review["report_path"]
            )
            if relative.as_posix() != review["report_path"]:
                reasons.append("review report path is not canonical")
            elif hashlib.sha256(contents).hexdigest() != review["report_sha256"]:
                reasons.append("review report hash no longer matches file")
        except TeamControlError as error:
            reasons.append(str(error))
        return reasons

    def evidence_stale_reasons(self, task, record, observed_head_sha):
        reasons = []
        try:
            self._validate_source_commit(
                task, record["source_sha"], target_sha=observed_head_sha
            )
        except TeamControlError as error:
            reasons.append(str(error))
        try:
            from .evidence import _read_task_regular_file

            relative, contents = _read_task_regular_file(
                self._common_dir, task, record["path"]
            )
            if relative.as_posix() != record["path"]:
                reasons.append("evidence path is not canonical")
            elif hashlib.sha256(contents).hexdigest() != record["sha256"]:
                reasons.append("evidence hash no longer matches file")
        except TeamControlError as error:
            reasons.append(str(error))
        return reasons

    @staticmethod
    def _agent_status_from_row(row):
        try:
            record = json.loads(row["report_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReconciliationError("stored agent report is invalid") from error
        if not isinstance(record, dict):
            raise ReconciliationError("stored agent report is not an object")
        expected = {
            "dispatch_id": row["dispatch_id"],
            "agent_id": row["agent_id"],
            "role": row["role"],
            "model": row["model"],
            "state": row["state"],
            "progress": row["progress"],
            "updated_at": row["updated_at"],
        }
        if any(record.get(field) != value for field, value in expected.items()):
            raise ReconciliationError("stored agent report does not match its index")
        validate_record("agent_status", record)
        return record

    def upsert_agent_status(self, record):
        validate_record("agent_status", record)
        normalized = dict(record)
        normalized.setdefault("progress", 0)
        normalized["updated_at"] = normalize_timestamp(normalized["updated_at"])
        validate_record("agent_status", normalized)
        report_json = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        with self.mutation() as connection:
            self._task_row_or_error(connection, normalized["dispatch_id"])
            existing_row = connection.execute(
                """SELECT * FROM agents
                   WHERE dispatch_id = ? AND agent_id = ?""",
                (normalized["dispatch_id"], normalized["agent_id"]),
            ).fetchone()
            if existing_row is not None:
                existing = self._agent_status_from_row(existing_row)
                existing_at = normalize_timestamp(existing["updated_at"])
                incoming_at = normalized["updated_at"]
                if incoming_at < existing_at:
                    raise ReconciliationError("agent status is stale")
                if incoming_at == existing_at:
                    if normalized == existing:
                        return existing
                    raise ReconciliationError(
                        "agent status conflict at same timestamp"
                    )
                connection.execute(
                    """UPDATE agents
                       SET role = ?, model = ?, state = ?, progress = ?,
                           report_json = ?, updated_at = ?
                       WHERE dispatch_id = ? AND agent_id = ?""",
                    (
                        normalized["role"], normalized.get("model"),
                        normalized["state"], normalized["progress"],
                        report_json, normalized["updated_at"],
                        normalized["dispatch_id"], normalized["agent_id"],
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO agents (
                           dispatch_id, agent_id, role, model, state, progress,
                           report_json, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        normalized["dispatch_id"], normalized["agent_id"],
                        normalized["role"], normalized.get("model"),
                        normalized["state"], normalized["progress"], report_json,
                        normalized["updated_at"],
                    ),
                )
        return normalized

    def list_agent_status(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM agents WHERE dispatch_id = ? ORDER BY agent_id",
                (dispatch_id,),
            ).fetchall()
        return [self._agent_status_from_row(row) for row in rows]

    @staticmethod
    def _blocker_from_row(row):
        record = {
            "schema_version": 1,
            "blocker_id": row["blocker_id"],
            "dispatch_id": row["dispatch_id"],
            "reason": row["reason"],
            "owner": row["owner"],
            "status": row["status"],
            "resolution_condition": row["resolution_condition"],
            "created_at": row["created_at"],
            "resolved_at": (
                row["updated_at"] if row["status"] == "RESOLVED" else None
            ),
        }
        validate_record("blocker", record)
        return record

    def add_blocker(self, dispatch_id, reason, owner, resolution_condition):
        with self.mutation() as connection:
            self._task_row_or_error(connection, dispatch_id)
            now = utc_now()
            record = {
                "schema_version": 1,
                "blocker_id": str(uuid.uuid4()),
                "dispatch_id": dispatch_id,
                "reason": reason,
                "owner": owner,
                "status": "OPEN",
                "resolution_condition": resolution_condition,
                "created_at": now,
                "resolved_at": None,
            }
            validate_record("blocker", record)
            connection.execute(
                """INSERT INTO blockers (
                       blocker_id, dispatch_id, reason, owner, status,
                       resolution_condition, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["blocker_id"], record["dispatch_id"],
                    record["reason"], record["owner"], record["status"],
                    record["resolution_condition"], record["created_at"], now,
                ),
            )
        return record

    def resolve_blocker(self, dispatch_id, blocker_id):
        with self.mutation() as connection:
            self._task_row_or_error(connection, dispatch_id)
            row = connection.execute(
                """SELECT * FROM blockers
                   WHERE dispatch_id = ? AND blocker_id = ?""",
                (dispatch_id, blocker_id),
            ).fetchone()
            if row is None:
                raise ReconciliationError(
                    "blocker is missing: %s" % blocker_id
                )
            if row["status"] == "RESOLVED":
                return self._blocker_from_row(row)
            if row["status"] != "OPEN":
                raise ReconciliationError("blocker status cannot be resolved")
            resolved_at = utc_now()
            cursor = connection.execute(
                """UPDATE blockers SET status = 'RESOLVED', updated_at = ?
                   WHERE dispatch_id = ? AND blocker_id = ? AND status = 'OPEN'""",
                (resolved_at, dispatch_id, blocker_id),
            )
            if cursor.rowcount != 1:
                raise ReconciliationError("blocker resolution conflict")
            row = connection.execute(
                "SELECT * FROM blockers WHERE blocker_id = ?", (blocker_id,)
            ).fetchone()
            return self._blocker_from_row(row)

    def list_blockers(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM blockers
                   WHERE dispatch_id = ? ORDER BY created_at, blocker_id""",
                (dispatch_id,),
            ).fetchall()
        return [self._blocker_from_row(row) for row in rows]

    @staticmethod
    def _review_from_row(row):
        record = {
            "schema_version": 1,
            "review_id": row["review_id"],
            "dispatch_id": row["dispatch_id"],
            "reviewer": row["reviewer"],
            "disposition": row["disposition"],
            "source_sha": row["source_sha"],
            "report_path": row["report_path"],
            "report_sha256": row["report_sha256"],
            "created_at": row["created_at"],
        }
        validate_record("review", record)
        return record

    def add_review(
        self, dispatch_id, reviewer, disposition, source_sha, report_path
    ):
        with self.mutation() as connection:
            task = self._task_row_or_error(connection, dispatch_id)
            if self._common_dir is None:
                raise ReconciliationError(
                    "review verification requires a repository-bound store"
                )
            if not isinstance(report_path, (str, Path)):
                raise ContractError("report_path must be a string or path")
            record = {
                "schema_version": 1,
                "review_id": str(uuid.uuid4()),
                "dispatch_id": dispatch_id,
                "reviewer": reviewer,
                "disposition": disposition,
                "source_sha": source_sha,
                "report_path": str(report_path),
                "report_sha256": "0" * 64,
                "created_at": utc_now(),
            }
            validate_record("review", record)
            from .evidence import _read_task_regular_file

            relative, contents = _read_task_regular_file(
                self._common_dir, task, report_path
            )
            record["report_path"] = relative.as_posix()
            record["report_sha256"] = hashlib.sha256(contents).hexdigest()
            validate_record("review", record)
            self._validate_source_commit(
                task,
                record["source_sha"],
                require_current=record["disposition"] == "ACCEPT",
            )
            connection.execute(
                """INSERT INTO reviews (
                       review_id, dispatch_id, reviewer, disposition,
                       source_sha, report_path, report_sha256, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["review_id"], record["dispatch_id"],
                    record["reviewer"], record["disposition"],
                    record["source_sha"], record["report_path"],
                    record["report_sha256"], record["created_at"],
                ),
            )
        return record

    def list_reviews(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM reviews
                   WHERE dispatch_id = ? ORDER BY created_at, review_id""",
                (dispatch_id,),
            ).fetchall()
        return [self._review_from_row(row) for row in rows]

    @staticmethod
    def _evidence_from_row(row):
        record = {
            "schema_version": 1,
            "evidence_id": row["evidence_id"],
            "dispatch_id": row["dispatch_id"],
            "kind": row["kind"],
            "path": row["path"],
            "sha256": row["sha256"],
            "source_sha": row["source_sha"],
            "created_at": row["created_at"],
        }
        validate_record("evidence", record)
        return record

    def add_evidence(self, record):
        validate_record("evidence", record)
        if self._common_dir is None:
            raise ReconciliationError(
                "evidence verification requires a repository-bound store"
            )
        with self.mutation() as connection:
            task = self._task_row_or_error(connection, record["dispatch_id"])
            from .evidence import _read_task_regular_file

            relative, contents = _read_task_regular_file(
                self._common_dir, task, record["path"]
            )
            if record["path"] != relative.as_posix():
                raise BoundaryError("evidence path must be repository-relative")
            if hashlib.sha256(contents).hexdigest() != record["sha256"]:
                raise ReconciliationError(
                    "evidence hash does not match file: %s" % record["path"]
                )
            self._validate_source_commit(task, record["source_sha"])
            connection.execute(
                """INSERT INTO evidence (
                       evidence_id, dispatch_id, kind, path, sha256,
                       source_sha, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["evidence_id"], record["dispatch_id"],
                    record["kind"], record["path"], record["sha256"],
                    record.get("source_sha"), record["created_at"],
                ),
            )
        return dict(record)

    def list_evidence(self, dispatch_id):
        with self.read_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM evidence
                   WHERE dispatch_id = ? ORDER BY created_at, evidence_id""",
                (dispatch_id,),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]
