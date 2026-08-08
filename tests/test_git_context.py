import tempfile
import unittest
from pathlib import Path

from team_control.errors import BoundaryError, GitStateError
from team_control.git_context import RepoContext, canonical_under, run_argv, validate_component
from tests.helpers import make_repo, run


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

    def test_discovers_shared_git_common_directory_from_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            linked = Path(tmp) / "linked"
            run(["git", "worktree", "add", "-b", "linked-test", str(linked)], repo)

            for candidate in (linked, linked / "scripts"):
                with self.subTest(candidate=candidate):
                    context = RepoContext.discover(candidate)
                    self.assertEqual(context.root, linked.resolve())
                    self.assertEqual(context.common_dir, (repo / ".git").resolve())

    def test_rejects_path_outside_registered_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with self.assertRaises(BoundaryError):
                canonical_under(root, root.parent / "escape")

    def test_rejects_symlink_escape_from_registered_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            root = root.resolve()
            escape = root / "escape"
            escape.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(BoundaryError):
                canonical_under(root, escape / "payload")

    def test_rejects_shell_metacharacters_in_component(self):
        for value in ("bad/id", "../bad", "bad name", "x;touch-pwned"):
            with self.subTest(value=value):
                with self.assertRaises(BoundaryError):
                    validate_component(value, "dispatch-id")

    def test_run_argv_wraps_subprocess_start_oserror(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_cwd = Path(tmp) / "missing"

            with self.assertRaises(GitStateError) as caught:
                run_argv(["git", "status"], missing_cwd)

            self.assertIsInstance(caught.exception.__cause__, OSError)


if __name__ == "__main__":
    unittest.main()
