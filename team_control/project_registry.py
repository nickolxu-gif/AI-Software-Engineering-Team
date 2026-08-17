import hashlib
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contracts import TASK_STATES, validate_project_registry_display_name
from .errors import BoundaryError, GitStateError
from .git_context import RepoContext
from .store import TASK_INTAKE_REQUIRED_SCHEMA_COLUMNS


READONLY_GIT_PREFIX = (
    "git",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "maintenance.auto=false",
)
GIT_TIMEOUT_SECONDS = 2.0
SQLITE_TIMEOUT_SECONDS = 1.0
MAX_TARGET_SNAPSHOT_BYTES = 64 * 1024 * 1024
LOCAL_SNAPSHOT_OVERHEAD_BYTES = 8 * 1024 * 1024

# This is intentionally a historical, read-only compatibility contract.  A
# registered project is not required to contain the central project's registry
# tables, and sampling must never initialize or migrate it.
TARGET_CONTROL_REQUIRED_SCHEMA = {
    "tasks": frozenset((
        "dispatch_id", "title", "objective", "risk_level", "state", "owner",
        "agent", "slug", "branch", "worktree_path", "task_base_sha",
        "current_head_sha", "updated_at",
    )),
    "events": frozenset((
        "dispatch_id", "sequence", "event_type", "payload_json", "created_at",
    )),
    "approvals": frozenset((
        "approval_id", "dispatch_id", "action", "target_sha", "expires_at",
        "consumed_at", "status",
    )),
    "agents": frozenset((
        "dispatch_id", "agent_id", "role", "state", "progress", "report_json",
        "updated_at",
    )),
    "reviews": frozenset((
        "review_id", "dispatch_id", "reviewer", "disposition", "source_sha",
        "report_path", "report_sha256", "created_at",
    )),
    "blockers": frozenset((
        "blocker_id", "dispatch_id", "reason", "owner", "status",
        "resolution_condition", "created_at", "updated_at",
    )),
    "evidence": frozenset((
        "evidence_id", "dispatch_id", "kind", "path", "sha256", "source_sha",
        "created_at",
    )),
    "intents": frozenset((
        "intent_id", "dispatch_id", "action", "target_sha", "status",
        "result_code", "created_at", "updated_at",
    )),
    **TASK_INTAKE_REQUIRED_SCHEMA_COLUMNS,
}


class _UnsupportedTargetSchema(Exception):
    pass


class _MissingTargetDatabase(Exception):
    pass


@dataclass(frozen=True)
class _SnapshotFileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    modified_at_ns: int


class ProjectSnapshotReader:
    """Read a single registered project without changing its repository or DB."""

    def __init__(self, entry):
        self.entry = entry

    @staticmethod
    def _sampled_at():
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _public_card(cls, entry, sampled_at=None):
        return {
            "project_id": entry["project_id"],
            "display_name": entry["display_name"],
            "registry_status": entry["status"],
            "sampled_at": sampled_at or cls._sampled_at(),
            "head_sha": "HEAD_UNAVAILABLE",
            "control_status": "UNAVAILABLE",
            "task_counts": {state: 0 for state in sorted(TASK_STATES)},
            "latest_task_updated_at": None,
        }

    @staticmethod
    def _identity(path):
        candidate = Path(path)
        metadata_before = candidate.lstat()
        resolved = candidate.resolve(strict=True)
        metadata_after = candidate.lstat()
        resolved_metadata = resolved.lstat()
        metadata_records = (metadata_before, metadata_after, resolved_metadata)
        if (
            any(stat.S_ISLNK(metadata.st_mode) for metadata in metadata_records)
            or not all(stat.S_ISDIR(metadata.st_mode) for metadata in metadata_records)
        ):
            raise OSError("registered directory identity is unavailable")
        identities = {
            (metadata.st_dev, metadata.st_ino, metadata.st_mode)
            for metadata in metadata_records
        }
        if len(identities) != 1:
            raise OSError("registered directory changed during identity capture")
        return resolved, identities.pop()

    @staticmethod
    def _stored_identity(entry, name):
        return (
            entry["%s_device" % name],
            entry["%s_inode" % name],
            entry["%s_mode" % name],
        )

    def _registered_identity_snapshot(self):
        try:
            root, root_identity = self._identity(self.entry["root_path"])
            common_dir, common_dir_identity = self._identity(
                self.entry["common_dir_path"]
            )
        except (OSError, TypeError, ValueError):
            return False
        matches_registered_entry = (
            str(root) == self.entry["root_path"]
            and str(common_dir) == self.entry["common_dir_path"]
            and root_identity == self._stored_identity(self.entry, "root")
            and common_dir_identity == self._stored_identity(self.entry, "common_dir")
        )
        if not matches_registered_entry:
            return False
        return str(root), root_identity, str(common_dir), common_dir_identity

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

    def _registered_git_dir(self):
        completed = subprocess.run(
            [
                *READONLY_GIT_PREFIX,
                "-C", self.entry["root_path"],
                "rev-parse", "--absolute-git-dir", "--git-common-dir",
            ],
            cwd=self.entry["root_path"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            env=self._git_environment(),
        )
        paths = completed.stdout.splitlines()
        if completed.returncode != 0 or len(paths) != 2:
            raise GitStateError("registered repository metadata is unavailable")
        root = Path(self.entry["root_path"])
        try:
            git_dir = Path(paths[0])
            common_dir = Path(paths[1])
            if not git_dir.is_absolute():
                git_dir = root / git_dir
            if not common_dir.is_absolute():
                common_dir = root / common_dir
            git_dir, _git_dir_identity = self._identity(git_dir)
            common_dir, _common_dir_identity = self._identity(common_dir)
            git_dir.relative_to(common_dir)
        except (OSError, TypeError, ValueError):
            raise GitStateError("registered repository metadata is unavailable")
        if str(common_dir) != self.entry["common_dir_path"]:
            raise GitStateError("registered repository metadata is unavailable")
        return str(git_dir)

    def _head_sha(self):
        git_dir = self._registered_git_dir()
        completed = subprocess.run(
            [
                *READONLY_GIT_PREFIX,
                "--git-dir", git_dir,
                "--work-tree", self.entry["root_path"],
                "rev-parse", "HEAD",
            ],
            cwd=self.entry["root_path"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            env=self._git_environment(),
        )
        value = completed.stdout.strip()
        if completed.returncode != 0 or len(value) not in (40, 64):
            raise GitStateError("registered repository head is unavailable")
        if any(character not in "0123456789abcdef" for character in value):
            raise GitStateError("registered repository head is unavailable")
        return value

    @staticmethod
    def _readonly_authorizer(action, arg1, _arg2, _database, _source):
        if (
            action == getattr(sqlite3, "SQLITE_PRAGMA", 19)
            and arg1 == "table_info"
        ):
            return sqlite3.SQLITE_OK
        denied_actions = {
            getattr(sqlite3, "SQLITE_ATTACH", 24),
            getattr(sqlite3, "SQLITE_DETACH", 25),
            getattr(sqlite3, "SQLITE_INSERT", 18),
            getattr(sqlite3, "SQLITE_UPDATE", 23),
            getattr(sqlite3, "SQLITE_DELETE", 9),
            getattr(sqlite3, "SQLITE_ALTER_TABLE", 26),
            getattr(sqlite3, "SQLITE_CREATE_INDEX", 1),
            getattr(sqlite3, "SQLITE_CREATE_TABLE", 2),
            getattr(sqlite3, "SQLITE_CREATE_TRIGGER", 7),
            getattr(sqlite3, "SQLITE_CREATE_VIEW", 8),
            getattr(sqlite3, "SQLITE_DROP_INDEX", 10),
            getattr(sqlite3, "SQLITE_DROP_TABLE", 11),
            getattr(sqlite3, "SQLITE_DROP_TRIGGER", 16),
            getattr(sqlite3, "SQLITE_DROP_VIEW", 17),
            getattr(sqlite3, "SQLITE_PRAGMA", 19),
            getattr(sqlite3, "SQLITE_REINDEX", 27),
            getattr(sqlite3, "SQLITE_ANALYZE", 28),
            getattr(sqlite3, "SQLITE_CREATE_VTABLE", 29),
            getattr(sqlite3, "SQLITE_DROP_VTABLE", 30),
            getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", 3),
            getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", 4),
            getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", 5),
            getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", 6),
            getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", 12),
            getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", 13),
            getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", 14),
            getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", 15),
        }
        return sqlite3.SQLITE_DENY if action in denied_actions else sqlite3.SQLITE_OK

    @staticmethod
    def _regular_database(common_dir):
        common_dir = Path(common_dir)
        team_dir = common_dir / "team"
        runtime_dir = team_dir / "runtime"
        database = runtime_dir / "team.db"
        try:
            for directory in (common_dir, team_dir, runtime_dir):
                metadata = directory.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISDIR(metadata.st_mode)
                ):
                    raise OSError("target database path is unavailable")
        except FileNotFoundError as error:
            raise OSError("target database parent is unavailable") from error
        try:
            metadata_before = database.lstat()
        except FileNotFoundError as error:
            raise _MissingTargetDatabase() from error
        try:
            resolved = database.resolve(strict=True)
        except FileNotFoundError as error:
            raise OSError("target database is unavailable") from error
        metadata_after = database.lstat()
        resolved_metadata = resolved.lstat()
        file_metadata = (metadata_before, metadata_after, resolved_metadata)
        if (
            any(stat.S_ISLNK(metadata.st_mode) for metadata in file_metadata)
            or not all(stat.S_ISREG(metadata.st_mode) for metadata in file_metadata)
            or resolved != database
            or len({
                (metadata.st_dev, metadata.st_ino, metadata.st_mode)
                for metadata in file_metadata
            }) != 1
        ):
            raise OSError("target database is unavailable")
        return resolved, (
            resolved_metadata.st_dev,
            resolved_metadata.st_ino,
            resolved_metadata.st_mode,
        )

    @classmethod
    def _database_identity_snapshot(cls, common_dir):
        try:
            database, identity = cls._regular_database(common_dir)
        except _MissingTargetDatabase:
            return ("MISSING",)
        except OSError:
            return ("UNAVAILABLE",)
        return ("PRESENT", str(database), identity)

    @staticmethod
    def _validate_target_schema(connection):
        for table, required_columns in TARGET_CONTROL_REQUIRED_SCHEMA.items():
            object_row = connection.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()
            if object_row is None or object_row[0] != "table":
                raise _UnsupportedTargetSchema()
            columns = {
                row[1]
                # table is a fixed module-private schema key, never target input.
                for row in connection.execute("PRAGMA table_info(%s)" % table)
            }
            if not required_columns.issubset(columns):
                raise _UnsupportedTargetSchema()

    @staticmethod
    def _safe_timestamp(value):
        if not isinstance(value, str) or not 20 <= len(value) <= 64:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.isoformat()

    @classmethod
    def _task_summary(cls, connection):
        counts = {state: 0 for state in sorted(TASK_STATES)}
        placeholders = ", ".join("?" for _ in counts)
        rows = connection.execute(
            "SELECT state, COUNT(*) FROM tasks WHERE state IN (%s) GROUP BY state"
            % placeholders,
            tuple(counts),
        ).fetchall()
        for state, count in rows:
            counts[state] = count
        latest = connection.execute(
            "SELECT MAX(updated_at) FROM tasks WHERE state IN (%s)" % placeholders,
            tuple(counts),
        ).fetchone()[0]
        return counts, cls._safe_timestamp(latest)

    def _read_control_summary(self, database):
        try:
            with self._local_database_snapshot(database) as local_database:
                connection = sqlite3.connect(
                    str(local_database), timeout=SQLITE_TIMEOUT_SECONDS
                )
                try:
                    connection.execute("PRAGMA query_only = ON")
                    connection.execute(
                        "PRAGMA busy_timeout = %d" % int(SQLITE_TIMEOUT_SECONDS * 1000)
                    )
                    connection.set_authorizer(self._readonly_authorizer)
                    self._validate_target_schema(connection)
                    counts, latest = self._task_summary(connection)
                    return "HEALTHY", counts, latest
                finally:
                    connection.close()
        except _UnsupportedTargetSchema:
            return "UNSUPPORTED", None, None
        except sqlite3.OperationalError:
            return "UNAVAILABLE", None, None
        except sqlite3.DatabaseError:
            return "UNSUPPORTED", None, None
        except OSError:
            return "UNAVAILABLE", None, None

    @staticmethod
    def _snapshot_file_identity_from_metadata(metadata):
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError("target database snapshot input is unavailable")
        return _SnapshotFileIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
            size=metadata.st_size,
            modified_at_ns=metadata.st_mtime_ns,
        )

    @classmethod
    def _snapshot_file_identity(cls, path):
        try:
            metadata = Path(path).lstat()
        except FileNotFoundError:
            return None
        return cls._snapshot_file_identity_from_metadata(metadata)

    @staticmethod
    def _snapshot_digest(descriptor, expected_size):
        digest = hashlib.sha256()
        offset = 0
        while offset < expected_size:
            chunk = os.pread(
                descriptor, min(1024 * 1024, expected_size - offset), offset
            )
            if not chunk:
                break
            digest.update(chunk)
            offset += len(chunk)
        return offset, digest.digest()

    @classmethod
    def _copy_snapshot_file(cls, source, destination, expected_identity, remaining_bytes):
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        nonblock_flag = getattr(os, "O_NONBLOCK", None)
        if (
            not isinstance(nofollow_flag, int)
            or nofollow_flag == 0
            or not isinstance(nonblock_flag, int)
            or nonblock_flag == 0
            or not callable(getattr(os, "pread", None))
        ):
            raise OSError("safe snapshot capture is unsupported on this platform")
        if expected_identity.size > remaining_bytes:
            raise OSError("target database snapshot exceeds the size budget")
        open_flags = os.O_RDONLY | nofollow_flag | nonblock_flag
        descriptor = os.open(str(source), open_flags)
        try:
            opened_identity = cls._snapshot_file_identity_from_metadata(
                os.fstat(descriptor)
            )
            if opened_identity != expected_identity:
                raise OSError("target database changed during snapshot capture")
            digest_size, digest_before = cls._snapshot_digest(
                descriptor, expected_identity.size
            )
            if digest_size != expected_identity.size:
                raise OSError("target database changed during snapshot capture")
            copied_bytes = 0
            copied_digest = hashlib.sha256()
            with (
                os.fdopen(descriptor, "rb", buffering=0, closefd=False) as input_handle,
                Path(destination).open("xb") as output_handle,
            ):
                while copied_bytes < expected_identity.size:
                    chunk = input_handle.read(
                        min(1024 * 1024, expected_identity.size - copied_bytes)
                    )
                    if not chunk:
                        break
                    copied_bytes += len(chunk)
                    output_handle.write(chunk)
                    copied_digest.update(chunk)
            if (
                copied_bytes != expected_identity.size
                or cls._snapshot_file_identity_from_metadata(os.fstat(descriptor))
                != expected_identity
                or copied_digest.digest() != digest_before
                or cls._snapshot_digest(descriptor, expected_identity.size)
                != (digest_size, digest_before)
            ):
                raise OSError("target database changed during snapshot capture")
            return copied_bytes
        finally:
            os.close(descriptor)

    @classmethod
    @contextmanager
    def _local_database_snapshot(cls, database):
        database = Path(database)
        sources = (
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-journal"),
        )
        before = tuple(cls._snapshot_file_identity(source) for source in sources)
        if before[0] is None:
            raise OSError("target database snapshot input is unavailable")
        total_bytes = sum(identity.size for identity in before if identity is not None)
        if total_bytes > MAX_TARGET_SNAPSHOT_BYTES:
            raise OSError("target database snapshot exceeds the size budget")
        with tempfile.TemporaryDirectory(prefix="team-project-snapshot-") as directory:
            required_free_bytes = (
                MAX_TARGET_SNAPSHOT_BYTES + LOCAL_SNAPSHOT_OVERHEAD_BYTES
            )
            if shutil.disk_usage(directory).free < required_free_bytes:
                raise OSError("target database snapshot has insufficient local space")
            local_sources = tuple(Path(directory) / source.name for source in sources)
            remaining_bytes = MAX_TARGET_SNAPSHOT_BYTES
            for source, local_source, identity in zip(sources, local_sources, before):
                if identity is not None:
                    remaining_bytes -= cls._copy_snapshot_file(
                        source, local_source, identity, remaining_bytes
                    )
            after = tuple(cls._snapshot_file_identity(source) for source in sources)
            if after != before:
                raise OSError("target database changed during snapshot capture")
            yield local_sources[0]

    def snapshot(self):
        card = self._public_card(self.entry)
        registered_before = self._registered_identity_snapshot()
        if registered_before is False:
            card["control_status"] = "IDENTITY_MISMATCH"
            return card
        database_before = self._database_identity_snapshot(registered_before[2])
        if database_before[0] == "MISSING":
            control_status, counts, latest = "UNINITIALIZED", None, None
        elif database_before[0] == "UNAVAILABLE":
            control_status, counts, latest = "UNAVAILABLE", None, None
        else:
            control_status, counts, latest = self._read_control_summary(
                Path(database_before[1])
            )
        card["control_status"] = control_status
        if counts is not None:
            card["task_counts"] = counts
            card["latest_task_updated_at"] = latest
        try:
            card["head_sha"] = self._head_sha()
        except GitStateError:
            card = self._public_card(self.entry, sampled_at=card["sampled_at"])
            card["control_status"] = "IDENTITY_MISMATCH"
            return card
        except (OSError, subprocess.TimeoutExpired):
            pass
        registered_after = self._registered_identity_snapshot()
        database_after = (
            self._database_identity_snapshot(registered_after[2])
            if registered_after is not False
            else None
        )
        if registered_after != registered_before or database_after != database_before:
            card = self._public_card(self.entry, sampled_at=card["sampled_at"])
            card["control_status"] = "IDENTITY_MISMATCH"
        return card


class ProjectRegistryService:
    """Manage the central allowlist without mutating registered repositories."""

    def __init__(self, context, store):
        self.store = store

    @staticmethod
    def _directory_identity(raw_path, label):
        if isinstance(raw_path, bool) or not isinstance(raw_path, (str, Path)):
            raise BoundaryError("project %s must be a local path" % label)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise BoundaryError("project %s must be an absolute path" % label)
        try:
            pre_resolve_metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            post_resolve_metadata = candidate.lstat()
            resolved_metadata = resolved.lstat()
        except OSError as error:
            raise BoundaryError("project %s is unavailable" % label) from error
        metadata_records = (
            pre_resolve_metadata,
            post_resolve_metadata,
            resolved_metadata,
        )
        if any(stat.S_ISLNK(metadata.st_mode) for metadata in metadata_records):
            raise BoundaryError("project %s must not be a symbolic link" % label)
        if not all(stat.S_ISDIR(metadata.st_mode) for metadata in metadata_records):
            raise BoundaryError("project %s must be a directory" % label)
        identities = {
            (metadata.st_dev, metadata.st_ino, metadata.st_mode)
            for metadata in metadata_records
        }
        if len(identities) != 1:
            raise BoundaryError("project %s changed during identity capture" % label)
        return resolved, (
            resolved_metadata.st_dev,
            resolved_metadata.st_ino,
            resolved_metadata.st_mode,
        )

    @classmethod
    def _non_symlink_directory(cls, raw_path, label):
        return cls._directory_identity(raw_path, label)[0]

    @classmethod
    def _discover_target(cls, raw_root):
        supplied_root = cls._non_symlink_directory(raw_root, "root")
        context = RepoContext.discover(supplied_root)
        root = cls._non_symlink_directory(context.root, "repository root")
        common_dir = cls._non_symlink_directory(context.common_dir, "common directory")
        if root != context.root or common_dir != context.common_dir:
            raise BoundaryError("project repository identity changed during discovery")
        return context

    @classmethod
    def _capture_target(cls, raw_root):
        context = cls._discover_target(raw_root)
        root, root_identity = cls._directory_identity(
            context.root, "repository root"
        )
        common_dir, common_dir_identity = cls._directory_identity(
            context.common_dir, "common directory"
        )
        if root != context.root or common_dir != context.common_dir:
            raise BoundaryError("project repository identity changed during capture")
        return context, {
            "root_device": root_identity[0],
            "root_inode": root_identity[1],
            "root_mode": root_identity[2],
            "common_dir_device": common_dir_identity[0],
            "common_dir_inode": common_dir_identity[1],
            "common_dir_mode": common_dir_identity[2],
        }

    @staticmethod
    def safe_summary(entry):
        return {
            "project_id": entry["project_id"],
            "display_name": entry["display_name"],
            "status": entry["status"],
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
        }

    def register(self, display_name, raw_root):
        display_name = validate_project_registry_display_name(display_name)
        initial, initial_identity = self._capture_target(raw_root)
        before_persist, before_persist_identity = self._capture_target(raw_root)
        if (
            initial.root != before_persist.root
            or initial.common_dir != before_persist.common_dir
            or initial_identity != before_persist_identity
        ):
            raise BoundaryError("project repository identity changed before registration")
        entry = self.store.create_project_registry_entry(
            str(uuid.uuid4()),
            display_name,
            str(before_persist.root),
            str(before_persist.common_dir),
            **before_persist_identity
        )
        return self.safe_summary(entry)

    def retire(self, project_id):
        return self.safe_summary(self.store.retire_project_registry_entry(project_id))
