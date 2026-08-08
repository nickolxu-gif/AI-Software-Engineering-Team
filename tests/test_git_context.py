import tempfile
import unittest
from pathlib import Path

from team_control.errors import BoundaryError
from team_control.git_context import RepoContext, canonical_under, validate_component
from tests.helpers import make_repo


class GitContextTests(unittest.TestCase):
    def test_discovers_shared_git_common_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            self.assertEqual(context.root, repo.resolve())
            self.assertEqual(context.common_dir, (repo / ".git").resolve())

    def test_discovers_shared_git_common_directory_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo / "scripts")
            self.assertEqual(context.root, repo.resolve())
            self.assertEqual(context.common_dir, (repo / ".git").resolve())

    def test_rejects_path_outside_registered_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with self.assertRaises(BoundaryError):
                canonical_under(root, root.parent / "escape")

    def test_rejects_shell_metacharacters_in_component(self):
        for value in ("bad/id", "../bad", "bad name", "x;touch-pwned"):
            with self.subTest(value=value):
                with self.assertRaises(BoundaryError):
                    validate_component(value, "dispatch-id")


if __name__ == "__main__":
    unittest.main()
