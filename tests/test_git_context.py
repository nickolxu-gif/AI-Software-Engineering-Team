import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_discovery_uses_a_fixed_git_path_and_isolated_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / ".git"
            common.mkdir()
            completed = [
                subprocess.CompletedProcess([], 0, str(root) + "\n", ""),
                subprocess.CompletedProcess([], 0, str(common) + "\n", ""),
            ]
            with mock.patch(
                "team_control.git_context.run_argv", side_effect=completed
            ) as run:
                RepoContext.discover(root)

        for call in run.call_args_list:
            self.assertTrue(Path(call.args[0][0]).is_absolute())
            self.assertEqual(
                call.args[0][1:5],
                ["-c", "core.fsmonitor=false", "-c", "maintenance.auto=false"],
            )
            self.assertFalse(call.kwargs["inherit_env"])
            self.assertEqual(call.kwargs["env_overrides"]["GIT_TERMINAL_PROMPT"], "0")

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

    def test_run_argv_applies_explicit_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"DASHBOARD_TEST_FLAG": "parent"},
        ):
            completed = run_argv(
                [
                    "python3",
                    "-c",
                    "import os; print(os.environ['DASHBOARD_TEST_FLAG'])",
                ],
                Path(tmp),
                env_overrides={"DASHBOARD_TEST_FLAG": "readonly"},
            )
            self.assertEqual(completed.stdout.strip(), "readonly")
            self.assertEqual(os.environ["DASHBOARD_TEST_FLAG"], "parent")

    def test_run_argv_can_start_from_a_clean_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            with mock.patch.dict(
                os.environ,
                {"DASHBOARD_PARENT_ONLY": "secret"},
            ):
                completed = run_argv(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os; "
                            "print(os.environ.get('DASHBOARD_PARENT_ONLY', 'missing')); "
                            "print(os.environ['DASHBOARD_CHILD_ONLY'])"
                        ),
                    ],
                    repo,
                    env_overrides={"DASHBOARD_CHILD_ONLY": "visible"},
                    inherit_env=False,
                )
                self.assertEqual(
                    completed.stdout.splitlines(),
                    ["missing", "visible"],
                )
                self.assertEqual(os.environ["DASHBOARD_PARENT_ONLY"], "secret")

    def test_run_argv_rejects_non_string_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BoundaryError):
                run_argv(
                    ["git", "--version"],
                    Path(tmp),
                    env_overrides={"GIT_OPTIONAL_LOCKS": 0},
                )

    def test_run_argv_rejects_environment_names_containing_equals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BoundaryError):
                run_argv(
                    ["git", "--version"],
                    Path(tmp),
                    env_overrides={"INVALID=NAME": "value"},
                )

    def test_run_argv_rejects_environment_overrides_containing_nul(self):
        with tempfile.TemporaryDirectory() as tmp:
            for overrides in (
                {"INVALID\0NAME": "value"},
                {"INVALID_NAME": "value\0suffix"},
            ):
                with self.subTest(overrides=overrides):
                    with self.assertRaises(BoundaryError):
                        run_argv(
                            ["git", "--version"],
                            Path(tmp),
                            env_overrides=overrides,
                        )


if __name__ == "__main__":
    unittest.main()
