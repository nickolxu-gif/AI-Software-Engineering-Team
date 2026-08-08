import math
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from team_control.errors import GitStateError, ReconciliationError
from team_control.git_context import RepoContext
from team_control.operations import OperationCoordinator
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.dispatch_id = "20260808-005"
        self.head = run(
            ["git", "rev-parse", "HEAD"], self.repo
        ).stdout.strip()
        self.store.create_task(
            {
                "schema_version": 1,
                "dispatch_id": self.dispatch_id,
                "title": "Operations",
                "objective": "Recover controlled Git operations",
                "risk_level": "L2",
                "state": "PLANNED",
                "task_base_sha": self.head,
                "owner": "Codex",
            }
        )
        self.ops = OperationCoordinator(self.context, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, suffix="1", action="verify-head"):
        return self.store.prepare_operation(
            self.dispatch_id,
            action,
            suffix[0] * 64,
            self.head,
            "operation-%s" % suffix,
        )

    def operation_rows(self):
        with self.store.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM operations ORDER BY operation_id"
                ).fetchall()
            ]

    def test_prepare_is_idempotent_and_conflicting_reuse_fails_closed(self):
        first = self.prepare()
        retry = self.prepare()

        self.assertEqual(retry, first)
        self.assertEqual(len(self.operation_rows()), 1)
        with self.assertRaises(ReconciliationError):
            self.store.prepare_operation(
                self.dispatch_id,
                "different-action",
                "b" * 64,
                self.head,
                first["idempotency_key"],
            )
        with self.assertRaises(KeyError):
            self.store.prepare_operation(
                "missing-task",
                "verify-head",
                "c" * 64,
                self.head,
                "missing-task-operation",
            )
        self.assertEqual(len(self.operation_rows()), 1)

    def test_concurrent_prepare_creates_exactly_one_operation(self):
        barrier = threading.Barrier(3)
        results = []
        result_lock = threading.Lock()

        def prepare_once():
            barrier.wait()
            result = self.prepare()
            with result_lock:
                results.append(result)

        threads = [threading.Thread(target=prepare_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertEqual(
            {item["operation_id"] for item in results},
            {self.operation_rows()[0]["operation_id"]},
        )
        self.assertEqual(len(self.operation_rows()), 1)

    def test_public_operation_decodes_result_and_hides_internal_json(self):
        operation = self.prepare()
        finished = self.store.finish_operation(
            operation["operation_id"],
            "COMMITTED",
            {"verified": True, "details": {"head": self.head}},
        )

        self.assertNotIn("result_json", finished)
        self.assertEqual(
            finished["result"],
            {"verified": True, "details": {"head": self.head}},
        )
        self.assertEqual(self.store.get_operation(operation["operation_id"]), finished)

    def test_finish_is_guarded_terminal_only_and_uses_strict_json(self):
        operation = self.prepare()
        for phase in ("PREPARED", "UNKNOWN", None):
            with self.subTest(phase=phase):
                with self.assertRaises(ReconciliationError):
                    self.store.finish_operation(
                        operation["operation_id"], phase, {"verified": None}
                    )
        for result in (
            {"verified": True, "value": math.nan},
            {"verified": True, "value": math.inf},
            {"verified": True, "value": object()},
            {"verified": True, 1: "non-string-key"},
        ):
            with self.subTest(result=result):
                with self.assertRaises(ReconciliationError):
                    self.store.finish_operation(
                        operation["operation_id"], "COMMITTED", result
                    )
                self.assertEqual(
                    self.store.get_operation(operation["operation_id"])["phase"],
                    "PREPARED",
                )

        finished = self.store.finish_operation(
            operation["operation_id"], "FAILED", {"verified": False}
        )
        self.assertEqual(finished["phase"], "FAILED")
        with self.assertRaises(ReconciliationError):
            self.store.finish_operation(
                operation["operation_id"], "COMMITTED", {"verified": True}
            )
        with self.assertRaises(ReconciliationError):
            self.store.finish_operation(
                str(uuid.uuid4()), "BLOCKED", {"verified": None}
            )

    def test_reconcile_maps_strict_verifier_tristate(self):
        cases = ((True, "COMMITTED"), (False, "FAILED"), (None, "BLOCKED"))
        for index, (verified, expected_phase) in enumerate(cases, start=1):
            with self.subTest(verified=verified):
                operation = self.prepare(str(index))
                result = self.ops.reconcile_one(
                    operation["operation_id"],
                    lambda ignored, value=verified: {
                        "verified": value,
                        "reason": "observed postcondition",
                    },
                )
                self.assertEqual(result["phase"], expected_phase)
                self.assertIs(result["result"]["verified"], verified)

    def test_invalid_or_raising_verifier_leaves_operation_prepared(self):
        operation = self.prepare()
        invalid_results = ([], {}, {"verified": 1}, {"verified": "true"})
        for verifier_result in invalid_results:
            with self.subTest(verifier_result=verifier_result):
                with self.assertRaises(ReconciliationError):
                    self.ops.reconcile_one(
                        operation["operation_id"],
                        lambda ignored, value=verifier_result: value,
                    )
                self.assertEqual(
                    self.store.get_operation(operation["operation_id"])["phase"],
                    "PREPARED",
                )

        def raises(ignored):
            raise RuntimeError("verifier unavailable")

        with self.assertRaisesRegex(RuntimeError, "verifier unavailable"):
            self.ops.reconcile_one(operation["operation_id"], raises)
        self.assertEqual(
            self.store.get_operation(operation["operation_id"])["phase"],
            "PREPARED",
        )

    def test_reconcile_all_blocks_operations_without_a_verifier(self):
        verified = self.prepare("1", action="known")
        unknown = self.prepare("2", action="unknown")

        results = self.ops.reconcile_all(
            {"known": lambda ignored: {"verified": True}}
        )

        by_id = {item["operation_id"]: item for item in results}
        self.assertEqual(by_id[verified["operation_id"]]["phase"], "COMMITTED")
        blocked = by_id[unknown["operation_id"]]
        self.assertEqual(blocked["phase"], "BLOCKED")
        self.assertEqual(blocked["result"]["reason"], "no verifier")

    def test_execute_rejects_invalid_argv_before_preparing(self):
        invalid = (None, [], (), "git status", ["git", 1], ["git", ""])
        for argv in invalid:
            with self.subTest(argv=argv):
                with self.assertRaises(ReconciliationError):
                    self.ops.execute_git(
                        self.dispatch_id,
                        "verify-head",
                        "a" * 64,
                        self.head,
                        "invalid-argv-%s" % len(self.operation_rows()),
                        argv,
                        lambda ignored: {"verified": True},
                    )
                self.assertEqual(self.operation_rows(), [])

    def test_execute_reads_trusted_head_under_lock_and_rejects_drift(self):
        (self.repo / "drift.txt").write_text("drift\n", encoding="utf-8")
        run(["git", "add", "--", "drift.txt"], self.repo)
        run(["git", "commit", "-m", "test: drift head"], self.repo)

        with self.assertRaises(GitStateError):
            self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "drifted-operation",
                ["git", "status", "--short"],
                lambda ignored: {"verified": True},
            )

        self.assertEqual(self.operation_rows(), [])

    def test_execute_commits_prepared_before_command_and_crash_leaves_it(self):
        observed_phases = []

        def run_or_crash(argv, cwd, check=True):
            if argv == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(stdout=self.head + "\n", stderr="", returncode=0)
            with self.store.read_connection() as connection:
                rows = connection.execute(
                    "SELECT phase FROM operations"
                ).fetchall()
            observed_phases.extend(row["phase"] for row in rows)
            raise GitStateError("simulated process crash")

        with mock.patch("team_control.operations.run_argv", side_effect=run_or_crash):
            with self.assertRaisesRegex(GitStateError, "simulated process crash"):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "crash-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )

        self.assertEqual(observed_phases, ["PREPARED"])
        self.assertEqual(self.store.prepared_operations()[0]["phase"], "PREPARED")

    def test_execute_and_approval_head_observation_share_control_lock(self):
        approval = self.store.create_approval(
            self.dispatch_id,
            "integrate",
            self.head,
            "b" * 64,
            "operation-lock-approval-nonce",
            10,
            "approval-lock-operation",
        )
        command_started = threading.Event()
        release_command = threading.Event()
        approval_observed = threading.Event()
        errors = []

        def controlled_run(argv, cwd, check=True):
            if argv == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(stdout=self.head + "\n", stderr="", returncode=0)
            command_started.set()
            if not release_command.wait(5.0):
                raise AssertionError("command was not released")
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        def execute():
            try:
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "locked-execution",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )
            except BaseException as error:
                errors.append(error)

        def consume():
            try:
                self.store.consume_approval(
                    approval["approval_id"],
                    "operation-lock-approval-nonce",
                    lambda ignored: approval_observed.set() or self.head,
                )
            except BaseException as error:
                errors.append(error)

        with mock.patch("team_control.operations.run_argv", side_effect=controlled_run):
            execute_thread = threading.Thread(target=execute)
            consume_thread = threading.Thread(target=consume)
            execute_thread.start()
            self.assertTrue(command_started.wait(5.0))
            consume_thread.start()
            self.assertFalse(approval_observed.wait(0.2))
            release_command.set()
            execute_thread.join(5.0)
            consume_thread.join(5.0)

        self.assertFalse(execute_thread.is_alive())
        self.assertFalse(consume_thread.is_alive())
        if errors:
            raise errors[0]
        self.assertTrue(approval_observed.is_set())

    def test_command_returncode_does_not_replace_verified_postcondition(self):
        committed = self.ops.execute_git(
            self.dispatch_id,
            "missing-ref-is-acceptable",
            "a" * 64,
            self.head,
            "failed-command-verified-operation",
            ["git", "rev-parse", "--verify", "refs/heads/missing"],
            lambda ignored: {"verified": True, "reason": "absence verified"},
        )
        self.assertEqual(committed["phase"], "COMMITTED")
        self.assertNotEqual(committed["result"]["command_returncode"], 0)

        failed = self.ops.execute_git(
            self.dispatch_id,
            "status-is-not-postcondition",
            "b" * 64,
            self.head,
            "successful-command-failed-verification",
            ["git", "status", "--short"],
            lambda ignored: {"verified": False, "reason": "postcondition absent"},
        )
        self.assertEqual(failed["phase"], "FAILED")
        self.assertEqual(failed["result"]["command_returncode"], 0)


if __name__ == "__main__":
    unittest.main()
