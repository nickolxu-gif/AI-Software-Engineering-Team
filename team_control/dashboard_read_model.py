import hashlib
import os
import sqlite3
import stat
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from .errors import BoundaryError, GitStateError, TeamControlError
from .git_context import canonical_under, run_argv


class DashboardError(TeamControlError):
    code = "DASHBOARD_ERROR"


class DashboardInputError(DashboardError):
    code = "INVALID_REQUEST"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


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
SQLITE_BUSY_CODES = frozenset(
    {
        getattr(sqlite3, "SQLITE_BUSY", 5),
        getattr(sqlite3, "SQLITE_LOCKED", 6),
    }
)


def parse_pagination(query, default_limit, maximum_limit):
    def invalid(message, error=None):
        pagination_error = DashboardInputError(
            message,
            code="INVALID_PAGINATION",
        )
        if error is None:
            raise pagination_error
        raise pagination_error from error

    if not isinstance(query, Mapping):
        invalid("pagination must be a mapping")
    unknown = set(query) - {"limit", "offset"}
    if unknown:
        invalid("unknown pagination parameter")
    values = {}
    for name, fallback in (
        ("limit", str(default_limit)),
        ("offset", "0"),
    ):
        supplied = query.get(name, [fallback])
        if (
            not isinstance(supplied, list)
            or len(supplied) != 1
            or not isinstance(supplied[0], str)
        ):
            invalid("pagination parameters must occur exactly once")
        values[name] = supplied[0]
    try:
        limit = int(values["limit"])
        offset = int(values["offset"])
    except (TypeError, ValueError, IndexError) as error:
        invalid("pagination must use integers", error)
    if limit < 0 or limit > maximum_limit or offset < 0 or offset > 10000:
        invalid("pagination is outside the allowed range")
    return limit, offset


class DashboardReadModel:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def _git(self, command, cwd=None, check=True):
        command = tuple(command)
        if command not in ALLOWED_GIT_COMMANDS:
            raise DashboardInputError("Git command is not allowlisted")
        target = self.context.root
        if cwd is not None:
            try:
                requested = Path(cwd).resolve(strict=True)
            except OSError as error:
                raise DashboardInputError("Git cwd is unavailable") from error
            if requested != self.context.root:
                raise DashboardInputError("Git cwd is not registered")
            target = requested
        return run_argv(
            [*READONLY_GIT_PREFIX, *command],
            target,
            check=check,
            env_overrides={
                "PATH": os.defpath,
                "LC_ALL": "C",
                "LANG": "C",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            },
            inherit_env=False,
        )

    def source_head_sha(self):
        return self._git(("rev-parse", "HEAD")).stdout.strip()

    def repository_id(self):
        encoded = str(self.context.common_dir).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validated_file(self, candidate, required, code, readable=True):
        candidate = Path(candidate)
        try:
            info = candidate.lstat()
        except FileNotFoundError as error:
            if not required:
                return None
            raise DashboardUnavailableError(
                "dashboard storage file is unavailable",
                code=code,
            ) from error
        except OSError as error:
            raise DashboardUnavailableError(
                "dashboard storage file is unavailable",
                code=code,
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DashboardUnavailableError(
                "dashboard storage file is not a regular file",
                code=code,
            )
        try:
            canonical_under(self.context.common_dir, candidate)
        except (BoundaryError, OSError) as error:
            raise DashboardUnavailableError(
                "dashboard storage path is invalid",
                code=code,
            ) from error
        if readable and not os.access(candidate, os.R_OK):
            raise DashboardUnavailableError(
                "dashboard storage file is not readable",
                code=code,
            )
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
        )

    def _validate_storage_files(self):
        database = (
            self.context.common_dir / "team" / "runtime" / "team.db"
        )
        database_identity = self._validated_file(
            database,
            required=True,
            code="DATABASE_UNAVAILABLE",
        )
        try:
            store_database = Path(self.store.path).resolve(strict=True)
            lexical_database = database.resolve(strict=True)
        except OSError as error:
            raise DashboardUnavailableError(
                "control database path is unavailable",
                code="DATABASE_UNAVAILABLE",
            ) from error
        if store_database != lexical_database:
            raise DashboardUnavailableError(
                "control store points at an unexpected database",
                code="DATABASE_UNAVAILABLE",
            )
        wal = Path(str(database) + "-wal")
        shm = Path(str(database) + "-shm")
        wal_identity = self._validated_file(
            wal,
            required=False,
            code="WAL_SIDECAR_UNAVAILABLE",
        )
        shm_identity = self._validated_file(
            shm,
            required=False,
            code="WAL_SIDECAR_UNAVAILABLE",
        )
        if wal_identity is not None and wal_identity[3] > 0:
            if shm_identity is None:
                raise DashboardUnavailableError(
                    "active WAL sidecars are unavailable",
                    code="WAL_SIDECAR_UNAVAILABLE",
                )
        return {
            "database": database_identity,
            "wal": wal_identity,
            "shm": shm_identity,
        }

    def _validate_wal_sidecars(self):
        return self._validate_storage_files()

    def _verify_storage_identity(self, expected):
        observed = self._validate_storage_files()
        def identity(value):
            return None if value is None else value[:3]

        if identity(observed["database"]) != identity(expected["database"]):
            raise DashboardUnavailableError(
                "control database changed during snapshot setup",
                code="DATABASE_UNAVAILABLE",
            )
        if (
            identity(observed["wal"]) != identity(expected["wal"])
            or identity(observed["shm"]) != identity(expected["shm"])
        ):
            raise DashboardUnavailableError(
                "WAL sidecars changed during snapshot setup",
                code="WAL_SIDECAR_UNAVAILABLE",
            )

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
        expected_storage = self._validate_storage_files()
        manager = self.store.read_connection()
        connection = None
        entered = False
        try:
            connection = manager.__enter__()
            entered = True
            connection.execute("BEGIN")
            self._validate_schema(connection)
            self._verify_storage_identity(expected_storage)
        except sqlite3.OperationalError as error:
            if entered:
                manager.__exit__(*sys.exc_info())
            error_code = getattr(error, "sqlite_errorcode", None)
            error_text = str(error).lower()
            is_busy = error_code in SQLITE_BUSY_CODES or any(
                marker in error_text for marker in ("busy", "locked")
            )
            unavailable_code = (
                "DATABASE_BUSY"
                if is_busy
                else "DATABASE_UNAVAILABLE"
            )
            raise DashboardUnavailableError(
                "control database is unavailable",
                code=unavailable_code,
            ) from error
        except BaseException:
            if entered:
                manager.__exit__(*sys.exc_info())
            raise
        try:
            yield connection
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                manager.__exit__(None, None, None)

    def health(self):
        with self.snapshot() as connection:
            connection.execute("SELECT 1").fetchone()
        try:
            self.source_head_sha()
        except (BoundaryError, GitStateError, OSError) as error:
            raise DashboardUnavailableError(
                "Git state is unavailable",
                code="GIT_UNAVAILABLE",
            ) from error
        return {
            "status": "HEALTHY",
            "database": "AVAILABLE",
            "schema": "SUPPORTED",
            "git": "AVAILABLE",
            "warnings": [],
        }

    def project(self):
        return {"head_sha": self.source_head_sha()}
