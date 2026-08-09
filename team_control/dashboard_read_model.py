import hashlib
import sqlite3
from contextlib import contextmanager
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

ALLOWED_GIT_COMMANDS = frozenset(
    {
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--git-common-dir"),
        ("rev-parse", "HEAD"),
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
            "--ignore-submodules=all",
        ),
        ("worktree", "list", "--porcelain"),
        ("remote",),
    }
)

REQUIRED_SCHEMA = {
    "tasks": {
        "dispatch_id",
        "title",
        "objective",
        "risk_level",
        "state",
        "current_head_sha",
    },
    "events": {
        "dispatch_id",
        "sequence",
        "event_type",
        "payload_json",
        "created_at",
    },
    "approvals": {
        "approval_id",
        "dispatch_id",
        "action",
        "target_sha",
        "status",
    },
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
                try:
                    self._validate_schema(connection)
                    yield connection
                finally:
                    if connection.in_transaction:
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

    def project(self):
        return {"head_sha": self.source_head_sha()}
