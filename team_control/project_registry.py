import stat
import uuid
from pathlib import Path

from .contracts import validate_project_registry_display_name
from .errors import BoundaryError
from .git_context import RepoContext


class ProjectRegistryService:
    """Manage the central allowlist without mutating registered repositories."""

    def __init__(self, context, store):
        self.context = context
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
