import hashlib
import tempfile
import unittest
from pathlib import Path

from team_control.dashboard_read_model import (
    DashboardInputError,
    DashboardReadModel,
    DashboardUnavailableError,
    parse_pagination,
)
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
