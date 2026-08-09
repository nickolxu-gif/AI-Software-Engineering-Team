import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from team_control.dashboard_read_model import (
    DashboardInputError,
    DashboardReadModel,
    DashboardUnavailableError,
    parse_pagination,
)
from team_control.errors import GitStateError
from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class DashboardReadModelTests(unittest.TestCase):
    def make_model(self, root):
        repo = make_repo(root / "repo")
        context = RepoContext.discover(repo)
        store = ControlStore.for_repo(context)
        store.initialize()
        return repo, store, DashboardReadModel(context, store)

    def test_parse_pagination_applies_defaults_and_caps(self):
        self.assertEqual(parse_pagination({}, 50, 100), (50, 0))
        self.assertEqual(
            parse_pagination({"limit": ["100"], "offset": ["25"]}, 50, 100),
            (100, 25),
        )
        for query in (
            {"limit": ["101"]},
            {"limit": ["-1"]},
            {"offset": ["10001"]},
            {"offset": ["text"]},
        ):
            with self.subTest(query=query):
                with self.assertRaises(DashboardInputError):
                    parse_pagination(query, 50, 100)

    def test_parse_pagination_rejects_ambiguous_shapes_with_stable_code(self):
        invalid_queries = (
            {"limit": ["10", "11"]},
            {"limit": True},
            {"limit": "10"},
            {"limit": [10]},
            {"unknown": ["1"]},
            None,
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises(DashboardInputError) as caught:
                    parse_pagination(query, 50, 100)
                self.assertEqual(caught.exception.code, "INVALID_PAGINATION")

    def test_non_empty_wal_requires_readable_regular_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            wal = Path(str(store.path) + "-wal")
            shm = Path(str(store.path) + "-shm")
            wal.write_bytes(b"active")
            if shm.exists():
                shm.unlink()
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "WAL_SIDECAR_UNAVAILABLE")

    def test_database_missing_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            store.path.unlink()
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "DATABASE_UNAVAILABLE")

    def test_database_and_sidecar_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in ("database", "wal", "shm"):
                with self.subTest(target=target):
                    case_root = root / target
                    case_root.mkdir()
                    repo, store, model = self.make_model(case_root)
                    candidate = (
                        store.path
                        if target == "database"
                        else Path(str(store.path) + "-" + target)
                    )
                    external = root / (target + "-external")
                    external.write_bytes(b"active")
                    if candidate.exists() or candidate.is_symlink():
                        candidate.unlink()
                    candidate.symlink_to(external)
                    expected = (
                        "DATABASE_UNAVAILABLE"
                        if target == "database"
                        else "WAL_SIDECAR_UNAVAILABLE"
                    )
                    with self.assertRaises(DashboardUnavailableError) as caught:
                        model.health()
                    self.assertEqual(caught.exception.code, expected)

    def test_preexisting_database_symlink_is_not_hidden_by_store_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            lexical_database = (
                model.context.common_dir / "team" / "runtime" / "team.db"
            )
            connection = sqlite3.connect(str(lexical_database))
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("PRAGMA journal_mode=DELETE")
            finally:
                connection.close()
            target = lexical_database.with_name("team-target.db")
            lexical_database.replace(target)
            lexical_database.symlink_to(target)
            resolved_store = ControlStore.for_repo(model.context)
            resolved_model = DashboardReadModel(model.context, resolved_store)
            with self.assertRaises(DashboardUnavailableError) as caught:
                resolved_model.health()
            self.assertEqual(caught.exception.code, "DATABASE_UNAVAILABLE")

    def test_active_wal_sidecars_must_be_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            wal = Path(str(store.path) + "-wal")
            shm = Path(str(store.path) + "-shm")
            wal.write_bytes(b"active")
            shm.write_bytes(b"locks")
            real_access = os.access

            def access(candidate, mode):
                if Path(candidate) == wal:
                    return False
                return real_access(candidate, mode)

            with patch(
                "team_control.dashboard_read_model.os.access",
                side_effect=access,
            ):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    model.health()
            self.assertEqual(caught.exception.code, "WAL_SIDECAR_UNAVAILABLE")

    def test_snapshot_reports_real_sqlite_busy_as_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))

            @contextmanager
            def busy_connection():
                error = sqlite3.OperationalError("database is locked")
                raise error
                yield

            with patch.object(store, "read_connection", busy_connection):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    with model.snapshot():
                        pass
            self.assertEqual(caught.exception.code, "DATABASE_BUSY")

    def test_snapshot_does_not_misclassify_caller_sql_errors_as_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with self.assertRaises(sqlite3.OperationalError):
                with model.snapshot() as connection:
                    connection.execute("SELECT missing_column FROM tasks")

    def test_health_checks_git_instead_of_claiming_it_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with patch.object(
                model,
                "source_head_sha",
                side_effect=GitStateError("git failed"),
            ):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    model.health()
            self.assertEqual(caught.exception.code, "GIT_UNAVAILABLE")

    def test_git_observation_ignores_parent_git_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, store, model = self.make_model(root)
            other = make_repo(root / "other")
            expected = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            hostile = {
                "GIT_DIR": str(other / ".git"),
                "GIT_WORK_TREE": str(other),
                "GIT_INDEX_FILE": str(other / ".git" / "index"),
                "PATH": str(root / "missing-bin"),
            }
            with patch.dict(os.environ, hostile):
                self.assertEqual(model.source_head_sha(), expected)
                self.assertEqual(os.environ["GIT_DIR"], hostile["GIT_DIR"])

    def test_git_observation_rejects_unregistered_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, store, model = self.make_model(root)
            other = make_repo(root / "other")
            with self.assertRaises(DashboardInputError):
                model._git(("rev-parse", "HEAD"), cwd=other)

    def test_git_observation_does_not_refresh_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            index = repo / ".git" / "index"
            before_hash = hashlib.sha256(index.read_bytes()).hexdigest()
            before_mtime = index.stat().st_mtime_ns
            observed = model.project()
            self.assertEqual(
                observed["head_sha"],
                run(["git", "rev-parse", "HEAD"], repo).stdout.strip(),
            )
            self.assertEqual(
                hashlib.sha256(index.read_bytes()).hexdigest(),
                before_hash,
            )
            self.assertEqual(index.stat().st_mtime_ns, before_mtime)


if __name__ == "__main__":
    unittest.main()
