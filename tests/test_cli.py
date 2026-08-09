import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(repo, *args, cwd=None, check=True):
    return run(
        [
            "python3",
            "-m",
            "team_control",
            "--repo",
            str(repo),
            *args,
        ],
        cwd or PROJECT_ROOT,
        check=check,
    )


def assert_single_json_line(test, result, stream="stdout"):
    value = getattr(result, stream)
    test.assertTrue(value.endswith("\n"), value)
    test.assertEqual(value.count("\n"), 1, value)
    return json.loads(value)


def make_cli_repo(path):
    path.mkdir()
    shutil.copytree(PROJECT_ROOT / "team_control", path / "team_control")
    scripts = path / "scripts"
    scripts.mkdir()
    for name in ("team-control", "worktree-doctor"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, scripts / name)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    (path / ".gitignore").write_text("/.worktrees/\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], path)
    run(["git", "config", "user.name", "MVP0 Test"], path)
    run(["git", "config", "user.email", "mvp0@example.invalid"], path)
    run(["git", "add", "--", "."], path)
    run(["git", "commit", "-m", "test: initialize CLI fixture"], path)
    return path


class CliTests(unittest.TestCase):
    def test_init_start_status_transition_and_approvals_emit_single_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo with spaces")

            initialized = run_cli(repo, "init")
            init_payload = assert_single_json_line(self, initialized)
            self.assertEqual(initialized.stderr, "")
            self.assertEqual(init_payload["status"], "initialized")

            start_args = (
                "start",
                "--dispatch-id",
                "20260808-008",
                "--title",
                "CLI",
                "--objective",
                "Emit JSON",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                "cli-smoke",
            )
            started = run_cli(repo, *start_args)
            started_payload = assert_single_json_line(self, started)
            self.assertEqual(started.stderr, "")
            self.assertEqual(started_payload["state"], "DISPATCHED")
            self.assertTrue(Path(started_payload["worktree_path"]).is_dir())

            repeated = run_cli(repo, *start_args)
            self.assertEqual(
                assert_single_json_line(self, repeated), started_payload
            )

            status = run_cli(
                repo, "status", "--dispatch-id", "20260808-008"
            )
            status_payload = assert_single_json_line(self, status)
            self.assertEqual(status_payload["task"]["state"], "DISPATCHED")

            transitioned = run_cli(
                repo,
                "transition",
                "--dispatch-id",
                "20260808-008",
                "--to",
                "IN_PROGRESS",
                "--reason",
                "implementation began",
            )
            transition_payload = assert_single_json_line(self, transitioned)
            self.assertEqual(transition_payload["state"], "IN_PROGRESS")

            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = store.get_task("20260808-008")
            nonce = "cli-approval-nonce-0001"
            approval = control.request_approval(
                "20260808-008",
                "integrate",
                task["current_head_sha"],
                {},
                nonce,
                10,
            )
            approvals = run_cli(
                repo, "approvals", "--dispatch-id", "20260808-008"
            )
            approvals_payload = assert_single_json_line(self, approvals)
            self.assertEqual(
                [item["approval_id"] for item in approvals_payload["approvals"]],
                [approval["approval_id"]],
            )
            self.assertEqual(approvals_payload["approvals"][0]["status"], "PENDING")
            self.assertIsNone(approvals_payload["approvals"][0]["consumed_at"])
            self.assertEqual(store.pending_approvals("20260808-008"), [approval])

            control.consume_approval(approval["approval_id"], nonce)
            consumed_approvals = run_cli(
                repo, "approvals", "--dispatch-id", "20260808-008"
            )
            consumed_payload = assert_single_json_line(self, consumed_approvals)
            self.assertEqual(
                consumed_payload["approvals"][0]["status"], "CONSUMED"
            )
            self.assertIsNotNone(
                consumed_payload["approvals"][0]["consumed_at"]
            )
            self.assertEqual(store.pending_approvals("20260808-008"), [])

            all_approvals = run_cli(repo, "approvals")
            all_payload = assert_single_json_line(self, all_approvals)
            self.assertEqual(
                [item["approval_id"] for item in all_payload["approvals"]],
                [approval["approval_id"]],
            )
            self.assertEqual(all_payload["approvals"][0]["status"], "CONSUMED")

    def test_doctor_inspect_and_repair_emit_single_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = control.create_task(
                "20260808-009", "Doctor", "Repair safely", "L1"
            )
            branch = "agent/codex/20260808-009-doctor"
            run(["git", "branch", branch, task["task_base_sha"]], repo)
            common = (
                "--dispatch-id",
                "20260808-009",
                "--agent",
                "codex",
                "--slug",
                "doctor",
                "--base-sha",
                task["task_base_sha"],
            )

            inspected = run_cli(repo, "doctor", "inspect", *common)
            self.assertEqual(
                assert_single_json_line(self, inspected)["classification"],
                "REPAIRABLE_BRANCH_ONLY",
            )
            repaired = run_cli(repo, "doctor", "repair", *common)
            repaired_payload = assert_single_json_line(self, repaired)
            self.assertEqual(repaired_payload["classification"], "HEALTHY")
            self.assertTrue(Path(repaired_payload["path"]).is_dir())

    def test_domain_and_argparse_errors_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            bad_component = run_cli(
                repo,
                "start",
                "--dispatch-id",
                "bad/id",
                "--title",
                "Bad",
                "--objective",
                "Reject",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                "bad",
                check=False,
            )
            self.assertNotEqual(bad_component.returncode, 0)
            self.assertEqual(bad_component.stdout, "")
            error = assert_single_json_line(self, bad_component, "stderr")
            self.assertEqual(error["error"]["code"], "BOUNDARY_ERROR")
            self.assertNotIn("Traceback", bad_component.stderr)

            usage = run_cli(repo, "start", check=False)
            self.assertNotEqual(usage.returncode, 0)
            self.assertEqual(usage.stdout, "")
            usage_error = assert_single_json_line(self, usage, "stderr")
            self.assertEqual(usage_error["error"]["code"], "CONTRACT_ERROR")
            self.assertNotIn("usage:", usage.stderr)

    def test_missing_repo_and_missing_initialization_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing repo"
            no_repo = run_cli(missing, "init", check=False)
            self.assertNotEqual(no_repo.returncode, 0)
            self.assertEqual(no_repo.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, no_repo, "stderr")["error"]["code"],
                "BOUNDARY_ERROR",
            )

            repo = make_repo(Path(tmp) / "repo")
            not_initialized = run_cli(
                repo,
                "status",
                "--dispatch-id",
                "20260808-010",
                check=False,
            )
            self.assertNotEqual(not_initialized.returncode, 0)
            self.assertEqual(not_initialized.stdout, "")
            self.assertEqual(
                assert_single_json_line(
                    self, not_initialized, "stderr"
                )["error"]["code"],
                "CONTRACT_ERROR",
            )

    def test_unexpected_error_is_redacted_without_traceback(self):
        from team_control import cli

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(cli, "execute", side_effect=RuntimeError("secret")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main(["--repo", ".", "init"])

        self.assertNotEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_wrappers_locate_repo_from_arbitrary_cwd_and_do_not_inject_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = make_cli_repo(tmp_path / "repo with spaces;safe")
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()
            marker = tmp_path / "PWNED"
            wrapper = repo / "scripts" / "team-control"

            initialized = run([str(wrapper), "init"], elsewhere)
            self.assertEqual(
                assert_single_json_line(self, initialized)["status"],
                "initialized",
            )
            rejected = run(
                [
                    str(wrapper),
                    "start",
                    "--dispatch-id",
                    "bad;touch-PWNED",
                    "--title",
                    "No injection",
                    "--objective",
                    "Quote argv",
                    "--risk",
                    "L1",
                    "--agent",
                    "codex",
                    "--slug",
                    "safe",
                ],
                elsewhere,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(rejected.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, rejected, "stderr")["error"]["code"],
                "BOUNDARY_ERROR",
            )

            started = run(
                [
                    str(wrapper),
                    "start",
                    "--dispatch-id",
                    "20260808-011",
                    "--title",
                    "Wrapper",
                    "--objective",
                    "Run from elsewhere",
                    "--risk",
                    "L1",
                    "--agent",
                    "codex",
                    "--slug",
                    "wrapper",
                ],
                elsewhere,
            )
            task = assert_single_json_line(self, started)
            doctor = run(
                [
                    str(repo / "scripts" / "worktree-doctor"),
                    "inspect",
                    "--dispatch-id",
                    "20260808-011",
                    "--agent",
                    "codex",
                    "--slug",
                    "wrapper",
                    "--base-sha",
                    task["task_base_sha"],
                ],
                elsewhere,
            )
            self.assertEqual(
                assert_single_json_line(self, doctor)["classification"],
                "HEALTHY",
            )


if __name__ == "__main__":
    unittest.main()
