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
    def _non_symlink_directory(raw_path, label):
        if isinstance(raw_path, bool) or not isinstance(raw_path, (str, Path)):
            raise BoundaryError("project %s must be a local path" % label)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise BoundaryError("project %s must be an absolute path" % label)
        try:
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise BoundaryError("project %s must not be a symbolic link" % label)
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise BoundaryError("project %s is unavailable" % label) from error
        if not resolved.is_dir():
            raise BoundaryError("project %s must be a directory" % label)
        return resolved

    @classmethod
    def _discover_target(cls, raw_root):
        supplied_root = cls._non_symlink_directory(raw_root, "root")
        context = RepoContext.discover(supplied_root)
        root = cls._non_symlink_directory(context.root, "repository root")
        common_dir = cls._non_symlink_directory(context.common_dir, "common directory")
        if root != context.root or common_dir != context.common_dir:
            raise BoundaryError("project repository identity changed during discovery")
        return context

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
        initial = self._discover_target(raw_root)
        before_persist = self._discover_target(raw_root)
        if (
            initial.root != before_persist.root
            or initial.common_dir != before_persist.common_dir
        ):
            raise BoundaryError("project repository identity changed before registration")
        entry = self.store.create_project_registry_entry(
            str(uuid.uuid4()),
            display_name,
            str(before_persist.root),
            str(before_persist.common_dir),
        )
        return self.safe_summary(entry)

    def retire(self, project_id):
        return self.safe_summary(self.store.retire_project_registry_entry(project_id))
