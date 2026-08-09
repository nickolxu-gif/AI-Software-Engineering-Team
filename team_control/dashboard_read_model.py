import hashlib
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath

from .contracts import RISK_LEVELS, SHA_RE, TASK_STATES
from .errors import BoundaryError, GitStateError, TeamControlError
from .git_context import canonical_under, run_argv, validate_component


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
        "owner",
        "agent",
        "slug",
        "branch",
        "worktree_path",
        "task_base_sha",
        "current_head_sha",
        "updated_at",
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
        "expires_at",
        "consumed_at",
        "status",
    },
    "agents": {
        "dispatch_id", "agent_id", "role", "state", "progress",
        "report_json", "updated_at",
    },
    "reviews": {
        "review_id", "dispatch_id", "reviewer", "disposition", "source_sha",
        "report_path", "report_sha256", "created_at",
    },
    "blockers": {
        "blocker_id", "dispatch_id", "reason", "owner", "status",
        "resolution_condition", "created_at", "updated_at",
    },
    "evidence": {
        "evidence_id", "dispatch_id", "kind", "path", "sha256",
        "source_sha", "created_at",
    },
}
SQLITE_BUSY_CODES = frozenset(
    {
        getattr(sqlite3, "SQLITE_BUSY", 5),
        getattr(sqlite3, "SQLITE_LOCKED", 6),
    }
)
TASK_FIELDS = (
    "dispatch_id",
    "title",
    "objective",
    "risk_level",
    "state",
    "owner",
    "agent",
    "updated_at",
    "current_head_sha",
)
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
ATTENTION_STATES = frozenset(
    {
        "NEEDS_CLARIFICATION",
        "NEEDS_DIRECTION",
        "BLOCKED",
        "PAUSE_REQUESTED",
        "PAUSED",
        "UNKNOWN",
    }
)
MAX_TASK_CANDIDATES = 10101
DETAIL_SUBRESOURCE_LIMIT = 200


def _page(rows, limit, offset):
    selected = rows[offset : offset + limit + 1]
    return {
        "items": selected[:limit],
        "limit": limit,
        "offset": offset,
        "has_more": len(selected) > limit,
    }


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

    @staticmethod
    def _git_environment():
        return {
            "PATH": os.defpath,
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }

    def _execute_git(self, command, target, check=True):
        command = tuple(command)
        if command not in ALLOWED_GIT_COMMANDS:
            raise DashboardInputError("Git command is not allowlisted")
        return run_argv(
            [*READONLY_GIT_PREFIX, *command],
            target,
            check=check,
            env_overrides=self._git_environment(),
            inherit_env=False,
        )

    def _git(self, command, cwd=None, check=True):
        target = self.context.root
        if cwd is not None:
            try:
                requested = Path(cwd).resolve(strict=True)
            except OSError as error:
                raise DashboardInputError("Git cwd is unavailable") from error
            if requested != self.context.root:
                raise DashboardInputError("Git cwd is not registered")
            target = requested
        return self._execute_git(command, target, check=check)

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
        wal_changed = identity(observed["wal"]) != identity(expected["wal"])
        reader_created_empty_wal = (
            expected["wal"] is None
            and observed["wal"] is not None
            and observed["wal"][3] == 0
        )
        shm_changed = identity(observed["shm"]) != identity(expected["shm"])
        reader_created_shm = (
            expected["shm"] is None and observed["shm"] is not None
        )
        if (
            (wal_changed and not reader_created_empty_wal)
            or (shm_changed and not reader_created_shm)
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
        head = self.source_head_sha()
        branch = self._git(
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            check=False,
        ).stdout.strip()
        dirty = bool(
            self._git(
                (
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=no",
                    "--ignore-submodules=all",
                )
            ).stdout.strip()
        )
        remotes = [
            line
            for line in self._git(("remote",)).stdout.splitlines()
            if line
        ]
        worktrees = self._git(("worktree", "list", "--porcelain")).stdout
        worktree_heads = self._parse_worktree_heads(worktrees)
        with self.snapshot() as connection:
            counts = self._counts(connection)
            attention = self._attention_items(
                connection,
                limit=20,
                worktree_heads=worktree_heads,
            )
        return {
            "repository_name": self.context.common_dir.parent.name,
            "branch": branch or "DETACHED",
            "head_sha": head,
            "remote_configured": bool(remotes),
            "worktree_count": sum(
                line.startswith("worktree ")
                for line in worktrees.splitlines()
            ),
            "health": "ATTENTION" if dirty or attention else "HEALTHY",
            "counts": counts,
            "attention_items": attention,
        }

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_timestamp(value):
        if not isinstance(value, str):
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _counts(self, connection):
        now = self._now().isoformat()
        row = connection.execute(
            """SELECT
                   SUM(CASE WHEN state != 'CLOSED' THEN 1 ELSE 0 END)
                       AS active_tasks,
                   SUM(CASE WHEN state = 'BLOCKED' OR EXISTS (
                       SELECT 1 FROM blockers b
                       WHERE b.dispatch_id = tasks.dispatch_id
                         AND b.status = 'OPEN'
                   ) THEN 1 ELSE 0 END) AS blocked_tasks
               FROM tasks"""
        ).fetchone()
        pending = connection.execute(
            """SELECT COUNT(*) FROM approvals
               WHERE status = 'PENDING' AND consumed_at IS NULL
                 AND julianday(expires_at) > julianday(?)""",
            (now,),
        ).fetchone()[0]
        stale_reviews = connection.execute(
            """SELECT COUNT(*) FROM reviews r JOIN tasks t
                   ON t.dispatch_id = r.dispatch_id
               WHERE r.source_sha != t.current_head_sha"""
        ).fetchone()[0]
        stale_evidence = connection.execute(
            """SELECT COUNT(*) FROM evidence e JOIN tasks t
                   ON t.dispatch_id = e.dispatch_id
               WHERE e.source_sha IS NULL
                  OR e.source_sha != t.current_head_sha"""
        ).fetchone()[0]
        return {
            "active_tasks": row["active_tasks"] or 0,
            "blocked_tasks": row["blocked_tasks"] or 0,
            "pending_approvals": pending,
            "stale_reviews": stale_reviews,
            "stale_evidence": stale_evidence,
        }

    @staticmethod
    def _filter_error(message):
        raise DashboardInputError(message, code="INVALID_FILTER")

    def _validate_task_filters(self, state, risk, attention, search):
        if state is not None and state not in TASK_STATES:
            self._filter_error("unknown task state")
        if risk is not None and risk not in RISK_LEVELS:
            self._filter_error("unknown risk level")
        if attention is None or isinstance(attention, bool):
            attention_value = attention
        elif attention == "true":
            attention_value = True
        elif attention == "false":
            attention_value = False
        else:
            self._filter_error("attention must be true or false")
        if search is not None and (
            not isinstance(search, str) or len(search) > 200
        ):
            self._filter_error("search is invalid")
        return attention_value

    def _task_rows(self, connection, state=None, risk=None, search=None):
        clauses = []
        parameters = []
        if state is not None:
            clauses.append("t.state = ?")
            parameters.append(state)
        if risk is not None:
            clauses.append("t.risk_level = ?")
            parameters.append(risk)
        if search:
            escaped = (
                search.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            clauses.append(
                "(LOWER(t.title) LIKE ? ESCAPE '\\' "
                "OR LOWER(t.objective) LIKE ? ESCAPE '\\' "
                "OR LOWER(t.dispatch_id) LIKE ? ESCAPE '\\')"
            )
            pattern = "%" + escaped + "%"
            parameters.extend((pattern, pattern, pattern))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(MAX_TASK_CANDIDATES)
        return connection.execute(
            """SELECT t.*,
                      EXISTS (
                          SELECT 1 FROM approvals a
                          WHERE a.dispatch_id = t.dispatch_id
                            AND a.status = 'PENDING'
                            AND a.consumed_at IS NULL
                            AND julianday(a.expires_at) > julianday(?)
                      ) AS has_pending_approval,
                      EXISTS (
                          SELECT 1 FROM blockers b
                          WHERE b.dispatch_id = t.dispatch_id
                            AND b.status = 'OPEN'
                      ) AS has_open_blocker,
                      EXISTS (
                          SELECT 1 FROM reviews r
                          WHERE r.dispatch_id = t.dispatch_id
                            AND r.source_sha != t.current_head_sha
                      ) AS has_stale_review,
                      EXISTS (
                          SELECT 1 FROM evidence e
                          WHERE e.dispatch_id = t.dispatch_id
                            AND (e.source_sha IS NULL
                                 OR e.source_sha != t.current_head_sha)
                      ) AS has_stale_evidence
               FROM tasks t"""
            + where
            + """ ORDER BY
                   CASE
                     WHEN has_pending_approval THEN 0
                     WHEN has_open_blocker
                          OR t.state IN (
                              'NEEDS_CLARIFICATION', 'NEEDS_DIRECTION',
                              'BLOCKED', 'PAUSE_REQUESTED', 'PAUSED', 'UNKNOWN'
                          ) THEN 1
                     WHEN has_stale_review OR has_stale_evidence THEN 2
                     WHEN t.worktree_path IS NOT NULL THEN 3
                     ELSE 4
                   END,
                   CASE t.risk_level WHEN 'L3' THEN 0 WHEN 'L2' THEN 1 ELSE 2 END,
                   t.updated_at DESC, t.dispatch_id LIMIT ?""",
            (self._now().isoformat(), *parameters),
        ).fetchall()

    @staticmethod
    def _parse_worktree_heads(output):
        heads = {}
        path = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line.startswith("HEAD ") and path is not None:
                head = line[len("HEAD ") :]
                if SHA_RE.fullmatch(head):
                    try:
                        resolved = Path(path).resolve(strict=True)
                    except (OSError, RuntimeError):
                        pass
                    else:
                        heads[resolved] = head
            elif not line:
                path = None
        return heads

    def _worktree_heads(self):
        output = self._git(("worktree", "list", "--porcelain")).stdout
        return self._parse_worktree_heads(output)

    def _registered_worktree_head(self, row, worktree_heads):
        worktree_path = row["worktree_path"]
        if not worktree_path:
            return None, True
        try:
            for field in ("dispatch_id", "agent", "slug"):
                value = row[field]
                if not isinstance(value, str):
                    return None, False
                validate_component(value, "task-" + field)
            expected_branch = "agent/%s/%s-%s" % (
                row["agent"],
                row["dispatch_id"],
                row["slug"],
            )
            if row["branch"] != expected_branch:
                return None, False
            lexical_root = self.context.common_dir.parent / ".worktrees"
            root_info = lexical_root.lstat()
            if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(
                root_info.st_mode
            ):
                return None, False
            worktree_root = lexical_root.resolve(strict=True)
            expected = worktree_root / (
                "%s-%s-%s"
                % (row["dispatch_id"], row["agent"], row["slug"])
            )
            stored = Path(worktree_path)
            if not stored.is_absolute():
                return None, False
            stored_info = stored.lstat()
            if stat.S_ISLNK(stored_info.st_mode) or not stat.S_ISDIR(
                stored_info.st_mode
            ):
                return None, False
            resolved = canonical_under(worktree_root, stored)
            if resolved != expected.resolve(strict=True):
                return None, False
            return worktree_heads.get(resolved), resolved in worktree_heads
        except (
            BoundaryError,
            GitStateError,
            OSError,
            RuntimeError,
            TypeError,
        ):
            return None, False

    def _task_item(self, row, worktree_heads, observed_head=None):
        reasons = []
        if row["has_pending_approval"]:
            reasons.append("PENDING_APPROVAL")
        if row["has_open_blocker"]:
            reasons.append("OPEN_BLOCKER")
        if row["state"] in ATTENTION_STATES:
            reasons.append("STATE_" + row["state"])
        if row["has_stale_review"]:
            reasons.append("STALE_REVIEW")
        if row["has_stale_evidence"]:
            reasons.append("STALE_EVIDENCE")
        if observed_head is None:
            actual_head, head_available = self._registered_worktree_head(
                row,
                worktree_heads,
            )
        else:
            actual_head, head_available = observed_head
        worktree_lifecycle_complete = row["state"] == "CLOSED"
        if not head_available and not worktree_lifecycle_complete:
            reasons.append("WORKTREE_UNAVAILABLE")
        if (
            actual_head is not None
            and actual_head != row["current_head_sha"]
            and not worktree_lifecycle_complete
        ):
            reasons.append("HEAD_DRIFT")
        item = {field: row[field] for field in TASK_FIELDS}
        item["effective_state"] = (
            "NEEDS_HUMAN_APPROVAL"
            if row["has_pending_approval"]
            else row["state"]
        )
        item["attention_reasons"] = reasons
        return item

    def _attention_sort_key(self, item):
        reason_order = {
            "PENDING_APPROVAL": 0,
            "OPEN_BLOCKER": 1,
            "STATE_BLOCKED": 1,
            "STATE_NEEDS_CLARIFICATION": 2,
            "STATE_NEEDS_DIRECTION": 2,
            "STATE_UNKNOWN": 2,
            "STATE_PAUSE_REQUESTED": 3,
            "STATE_PAUSED": 3,
            "STALE_REVIEW": 4,
            "STALE_EVIDENCE": 4,
            "HEAD_DRIFT": 5,
            "WORKTREE_UNAVAILABLE": 0,
        }
        priority = min(
            (reason_order.get(reason, 9) for reason in item["attention_reasons"]),
            default=9,
        )
        risk_order = {"L3": 0, "L2": 1, "L1": 2}
        human_order = 0 if "PENDING_APPROVAL" in item["attention_reasons"] else 1
        timestamp = self._parse_timestamp(item["updated_at"]).timestamp()
        return (
            priority,
            risk_order.get(item["risk_level"], 3),
            human_order,
            -timestamp,
            item["dispatch_id"],
        )

    def _attention_items(self, connection, limit, worktree_heads):
        items = [
            self._task_item(row, worktree_heads)
            for row in self._task_rows(connection)
        ]
        items = [item for item in items if item["attention_reasons"]]
        items.sort(key=self._attention_sort_key)
        return items[:limit]

    def tasks(self, query, state=None, risk=None, attention=None, search=None):
        limit, offset = parse_pagination(query, 50, 100)
        attention_value = self._validate_task_filters(
            state,
            risk,
            attention,
            search,
        )
        worktree_heads = self._worktree_heads()
        with self.snapshot() as connection:
            items = [
                self._task_item(row, worktree_heads)
                for row in self._task_rows(
                    connection,
                    state=state,
                    risk=risk,
                    search=search,
                )
            ]
        if attention_value is not None:
            items = [
                item
                for item in items
                if bool(item["attention_reasons"]) is attention_value
            ]
        items.sort(key=self._attention_sort_key)
        return _page(items, limit, offset)

    @staticmethod
    def _task_row(connection, dispatch_id):
        row = connection.execute(
            "SELECT * FROM tasks WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            raise DashboardNotFoundError("task was not found")
        return row

    @staticmethod
    def _bounded_detail_rows(rows, label):
        if len(rows) > DETAIL_SUBRESOURCE_LIMIT:
            raise DashboardUnavailableError(
                "%s exceeds the dashboard detail limit" % label,
                code="DETAIL_LIMIT_EXCEEDED",
            )
        return rows

    @staticmethod
    def _event_item(row):
        event_type = row["event_type"]
        details = {}
        if event_type == "STATE_CHANGED":
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                for key in ("from", "to", "reason"):
                    value = payload.get(key)
                    if isinstance(value, str):
                        details[key] = value
        return {
            "sequence": row["sequence"],
            "event_type": event_type,
            "created_at": row["created_at"],
            "summary": EVENT_SUMMARIES.get(event_type, "未识别事件"),
            "details": details,
        }

    def task(self, dispatch_id):
        worktree_heads = self._worktree_heads()
        with self.snapshot() as connection:
            row = self._task_row(connection, dispatch_id)
            query_row = connection.execute(
                """SELECT t.*,
                          EXISTS (
                              SELECT 1 FROM approvals a
                              WHERE a.dispatch_id = t.dispatch_id
                                AND a.status = 'PENDING'
                                AND a.consumed_at IS NULL
                                AND julianday(a.expires_at) > julianday(?)
                          ) AS has_pending_approval,
                          EXISTS (
                              SELECT 1 FROM blockers b
                              WHERE b.dispatch_id = t.dispatch_id
                                AND b.status = 'OPEN'
                          ) AS has_open_blocker,
                          EXISTS (
                              SELECT 1 FROM reviews r
                              WHERE r.dispatch_id = t.dispatch_id
                                AND r.source_sha != t.current_head_sha
                          ) AS has_stale_review,
                          EXISTS (
                              SELECT 1 FROM evidence e
                              WHERE e.dispatch_id = t.dispatch_id
                                AND (e.source_sha IS NULL
                                     OR e.source_sha != t.current_head_sha)
                          ) AS has_stale_evidence
                   FROM tasks t WHERE t.dispatch_id = ?""",
                (self._now().isoformat(), dispatch_id),
            ).fetchone()
            observed_head = self._registered_worktree_head(
                row,
                worktree_heads,
            )
            actual_head, head_available = observed_head
            item = self._task_item(
                query_row,
                worktree_heads,
                observed_head=observed_head,
            )
            item.update(
                {
                    "task_base_sha": row["task_base_sha"],
                    "branch": row["branch"],
                    "worktree_path": row["worktree_path"],
                }
            )
            agent_rows = self._bounded_detail_rows(
                connection.execute(
                    """SELECT agent_id, role, state, progress, updated_at
                       FROM agents WHERE dispatch_id = ?
                       ORDER BY updated_at DESC, agent_id LIMIT ?""",
                    (dispatch_id, DETAIL_SUBRESOURCE_LIMIT + 1),
                ).fetchall(),
                "agents",
            )
            agents = [
                {
                    "agent_id": agent["agent_id"],
                    "role": agent["role"],
                    "state": agent["state"],
                    "progress": agent["progress"],
                    "updated_at": agent["updated_at"],
                }
                for agent in agent_rows
            ]
            blocker_rows = self._bounded_detail_rows(
                connection.execute(
                    """SELECT blocker_id, reason, owner, status,
                              resolution_condition, created_at, updated_at
                       FROM blockers WHERE dispatch_id = ?
                       ORDER BY created_at, blocker_id LIMIT ?""",
                    (dispatch_id, DETAIL_SUBRESOURCE_LIMIT + 1),
                ).fetchall(),
                "blockers",
            )
            blockers = [
                {
                    "blocker_id": blocker["blocker_id"],
                    "reason": blocker["reason"],
                    "owner": blocker["owner"],
                    "status": blocker["status"],
                    "resolution_condition": blocker["resolution_condition"],
                    "created_at": blocker["created_at"],
                    "resolved_at": (
                        blocker["updated_at"]
                        if blocker["status"] == "RESOLVED"
                        else None
                    ),
                }
                for blocker in blocker_rows
            ]
            review_rows = self._bounded_detail_rows(
                connection.execute(
                    """SELECT review_id, reviewer, disposition, source_sha,
                              created_at
                       FROM reviews WHERE dispatch_id = ?
                       ORDER BY created_at DESC, review_id LIMIT ?""",
                    (dispatch_id, DETAIL_SUBRESOURCE_LIMIT + 1),
                ).fetchall(),
                "reviews",
            )
            reviews = [
                {
                    "review_id": review["review_id"],
                    "reviewer": review["reviewer"],
                    "disposition": review["disposition"],
                    "source_sha": review["source_sha"],
                    "created_at": review["created_at"],
                    "stale": (
                        review["source_sha"] != row["current_head_sha"]
                        or (
                            actual_head is not None
                            and review["source_sha"] != actual_head
                        )
                    ),
                }
                for review in review_rows
            ]
            pending_count = connection.execute(
                """SELECT COUNT(*) FROM approvals
                   WHERE dispatch_id = ? AND status = 'PENDING'
                     AND consumed_at IS NULL
                     AND julianday(expires_at) > julianday(?)""",
                (dispatch_id, self._now().isoformat()),
            ).fetchone()[0]
            evidence_count = connection.execute(
                "SELECT COUNT(*) FROM evidence WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()[0]
            latest = connection.execute(
                """SELECT sequence, event_type, payload_json, created_at
                   FROM events WHERE dispatch_id = ?
                   ORDER BY sequence DESC LIMIT 1""",
                (dispatch_id,),
            ).fetchone()
        valid_acceptance = any(
            review["disposition"] == "ACCEPT"
            and not review["stale"]
            and head_available
            and actual_head is not None
            for review in reviews
        )
        return {
            "task": item,
            "actual_head_sha": actual_head,
            "head_drift": (
                actual_head is not None
                and actual_head != row["current_head_sha"]
            ),
            "valid_acceptance": valid_acceptance,
            "agents": agents,
            "blockers": blockers,
            "reviews": reviews,
            "pending_approval_count": pending_count,
            "evidence_count": evidence_count,
            "latest_event": self._event_item(latest) if latest else None,
        }

    def events(self, dispatch_id, query):
        limit, offset = parse_pagination(query, 100, 200)
        with self.snapshot() as connection:
            self._task_row(connection, dispatch_id)
            rows = connection.execute(
                """SELECT sequence, event_type, payload_json, created_at
                   FROM events WHERE dispatch_id = ?
                   ORDER BY sequence DESC LIMIT ?""",
                (dispatch_id, offset + limit + 1),
            ).fetchall()
            items = [self._event_item(row) for row in rows]
        return _page(items, limit, offset)

    def _relative_evidence_path(self, value):
        if not isinstance(value, str) or "\\" in value:
            raise DashboardUnavailableError(
                "stored evidence path is invalid",
                code="INVALID_STORED_PATH",
            )
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in ("", ".", "..") for part in relative.parts)
            or relative.as_posix() != value
        ):
            raise DashboardUnavailableError(
                "stored evidence path is invalid",
                code="INVALID_STORED_PATH",
            )
        return value

    def evidence(self, dispatch_id, query):
        limit, offset = parse_pagination(query, 50, 100)
        with self.snapshot() as connection:
            task_row = self._task_row(connection, dispatch_id)
            rows = connection.execute(
                """SELECT evidence_id, kind, path, sha256, source_sha,
                          created_at
                   FROM evidence WHERE dispatch_id = ?
                   ORDER BY created_at DESC, evidence_id LIMIT ?""",
                (dispatch_id, offset + limit + 1),
            ).fetchall()
            items = [
                {
                    "evidence_id": row["evidence_id"],
                    "kind": row["kind"],
                    "relative_path": self._relative_evidence_path(row["path"]),
                    "sha256": row["sha256"],
                    "source_sha": row["source_sha"],
                    "created_at": row["created_at"],
                    "stale": row["source_sha"] != task_row["current_head_sha"],
                }
                for row in rows
            ]
        return _page(items, limit, offset)

    def approvals(self, query):
        limit, offset = parse_pagination(query, 50, 100)
        with self.snapshot() as connection:
            rows = connection.execute(
                """SELECT approval_id, dispatch_id, action, target_sha,
                          expires_at, consumed_at, status
                   FROM approvals
                   ORDER BY CASE WHEN status = 'PENDING' THEN 0 ELSE 1 END,
                            expires_at, approval_id LIMIT ?""",
                (offset + limit + 1,),
            ).fetchall()
            items = [
                {
                    "approval_id": row["approval_id"],
                    "dispatch_id": row["dispatch_id"],
                    "action": row["action"],
                    "target_sha": row["target_sha"],
                    "expires_at": row["expires_at"],
                    "consumed_at": row["consumed_at"],
                    "status": row["status"],
                }
                for row in rows
            ]
        return _page(items, limit, offset)
