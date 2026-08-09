import json
import sys
import tempfile
import unittest
from pathlib import Path

from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(repo, *args, check=True):
    return run(
        [
            sys.executable,
            "-m",
            "team_control",
            "--repo",
            str(repo),
            *args,
        ],
        PROJECT_ROOT,
        check=check,
    )


def json_line(test, result, stream="stdout"):
    value = getattr(result, stream)
    test.assertTrue(value.endswith("\n"), value)
    test.assertEqual(value.count("\n"), 1, value)
    return json.loads(value)


class Mvp0EndToEndTests(unittest.TestCase):
    def test_task_pause_resume_review_accept_integrate_release_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            control = ControlPlane(context, store)

            task = control.create_task(
                "20260808-009", "E2E", "Complete lifecycle", "L1"
            )
            self.assertEqual(task["state"], "PLANNED")

            expected_states = [
                "DISPATCHED",
                "IN_PROGRESS",
                "PAUSE_REQUESTED",
                "PAUSED",
                "IN_PROGRESS",
                "REVIEWING",
                "ACCEPTED",
                "INTEGRATED",
                "RELEASED",
                "CLOSED",
            ]
            observed_states = []
            for target in expected_states:
                task = control.transition(
                    "20260808-009", target, "e2e transition to %s" % target
                )
                observed_states.append(task["state"])

            self.assertEqual(observed_states, expected_states)
            self.assertEqual(task["state"], "CLOSED")
            self.assertIsNone(task["resume_state"])

            events = store.list_events("20260808-009")
            self.assertEqual(
                [event["sequence"] for event in events],
                list(range(1, len(events) + 1)),
            )
            self.assertEqual(events[0]["event_type"], "TASK_CREATED")
            self.assertEqual(
                [event["payload"]["to"] for event in events[1:]],
                expected_states,
            )

            status = control.status("20260808-009")
            self.assertEqual(status["task"]["state"], "CLOSED")
            self.assertEqual(status["effective_state"], "CLOSED")
            self.assertFalse(status["head_drift"])

    def test_real_cli_vertical_flow_and_doctor_preserves_unknown_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo with spaces")

            initialized = run_cli(repo, "init")
            self.assertEqual(json_line(self, initialized)["status"], "initialized")

            start_args = (
                "start",
                "--dispatch-id",
                "20260808-010",
                "--title",
                "CLI vertical",
                "--objective",
                "Exercise the MVP 0 command boundary",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                "vertical",
            )
            started = json_line(self, run_cli(repo, *start_args))
            self.assertEqual(started["state"], "DISPATCHED")
            self.assertTrue(Path(started["worktree_path"]).is_dir())

            status = json_line(
                self,
                run_cli(
                    repo, "status", "--dispatch-id", "20260808-010"
                ),
            )
            self.assertEqual(status["task"]["state"], "DISPATCHED")

            for target in (
                "IN_PROGRESS",
                "PAUSE_REQUESTED",
                "PAUSED",
                "IN_PROGRESS",
            ):
                transitioned = json_line(
                    self,
                    run_cli(
                        repo,
                        "transition",
                        "--dispatch-id",
                        "20260808-010",
                        "--to",
                        target,
                        "--reason",
                        "e2e %s" % target.lower(),
                    ),
                )
                self.assertEqual(transitioned["state"], target)

            doctor_args = (
                "doctor",
                "inspect",
                "--dispatch-id",
                "20260808-010",
                "--agent",
                "codex",
                "--slug",
                "vertical",
                "--base-sha",
                started["task_base_sha"],
            )
            healthy = json_line(self, run_cli(repo, *doctor_args))
            self.assertEqual(healthy["classification"], "HEALTHY")

            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = store.get_task("20260808-010")
            nonce = "mvp0-e2e-approval-nonce-0001"
            approval = control.request_approval(
                "20260808-010",
                "integrate",
                task["current_head_sha"],
                {"scope": "temporary-test-repository"},
                nonce,
                10,
            )
            pending = json_line(
                self,
                run_cli(
                    repo, "approvals", "--dispatch-id", "20260808-010"
                ),
            )["approvals"]
            self.assertEqual([item["status"] for item in pending], ["PENDING"])
            self.assertEqual(pending[0]["approval_id"], approval["approval_id"])

            control.consume_approval(approval["approval_id"], nonce)
            consumed = json_line(
                self,
                run_cli(
                    repo, "approvals", "--dispatch-id", "20260808-010"
                ),
            )["approvals"]
            self.assertEqual([item["status"] for item in consumed], ["CONSUMED"])
            self.assertIsNotNone(consumed[0]["consumed_at"])

            safety_dispatch = "20260808-011"
            safety_slug = "minor-safety"
            safety_path = (
                repo
                / ".worktrees"
                / ("%s-codex-%s" % (safety_dispatch, safety_slug))
            )
            safety_path.mkdir(parents=True)
            unknown = safety_path / "unknown.txt"
            unknown.write_text("preserve me\n", encoding="utf-8")
            base_sha = run(["git", "rev-parse", "main"], repo).stdout.strip()

            blocked_start = run_cli(
                repo,
                "start",
                "--dispatch-id",
                safety_dispatch,
                "--title",
                "Doctor safety",
                "--objective",
                "Preserve unknown residue",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                safety_slug,
                check=False,
            )
            self.assertNotEqual(blocked_start.returncode, 0)
            self.assertEqual(
                json_line(self, blocked_start, "stderr")["error"]["code"],
                "RECONCILIATION_ERROR",
            )

            safety_args = (
                "--dispatch-id",
                safety_dispatch,
                "--agent",
                "codex",
                "--slug",
                safety_slug,
                "--base-sha",
                base_sha,
            )
            inspected = json_line(
                self, run_cli(repo, "doctor", "inspect", *safety_args)
            )
            self.assertEqual(
                inspected["classification"], "BLOCKED_PATH_RESIDUE"
            )

            repair = run_cli(
                repo, "doctor", "repair", *safety_args, check=False
            )
            self.assertNotEqual(repair.returncode, 0)
            self.assertEqual(
                json_line(self, repair, "stderr")["error"]["code"],
                "RECONCILIATION_ERROR",
            )
            self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve me\n")


if __name__ == "__main__":
    unittest.main()
