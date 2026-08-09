import math
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import team_control.operations as operations_module
from team_control.errors import BoundaryError, GitStateError, ReconciliationError
from team_control.git_context import RepoContext
from team_control.operations import ALLOWED_GIT_SUBCOMMANDS, OperationCoordinator
from team_control.store import ControlStore, StoreBusyError
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

    def prepare(self, suffix="1", action="verify-head", result=None):
        arguments = (
            self.dispatch_id,
            action,
            suffix[0] * 64,
            self.head,
            "operation-%s" % suffix,
        )
        if result is None:
            return self.store.prepare_operation(*arguments)
        return self.store.prepare_operation(*arguments, result=result)

    def operation_rows(self):
        with self.store.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM operations ORDER BY operation_id"
                ).fetchall()
            ]

    def tamper_task_worktree(self, path, agent="agent", slug="tampered"):
        branch = "agent/%s/%s-%s" % (agent, self.dispatch_id, slug)
        with self.store.mutation() as connection:
            connection.execute(
                """UPDATE tasks
                   SET agent = ?, slug = ?, branch = ?, worktree_path = ?
                   WHERE dispatch_id = ?""",
                (agent, slug, branch, str(path), self.dispatch_id),
            )

    def test_prepare_is_idempotent_and_conflicting_reuse_fails_closed(self):
        first = self.prepare()
        retry = self.prepare()

        self.assertIsNone(first["result"])
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

    def test_prepare_callback_intent_is_strict_json_and_publicly_decoded(self):
        operation = self.prepare(
            "2", result={"callback_status": "PENDING"}
        )

        self.assertEqual(operation["phase"], "PREPARED")
        self.assertEqual(operation["result"], {"callback_status": "PENDING"})
        self.assertNotIn("result_json", operation)
        self.assertEqual(
            self.store.get_operation(operation["operation_id"]), operation
        )
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE operations SET result_json = ? WHERE operation_id = ?",
                ('{"callback_status":NaN}', operation["operation_id"]),
            )
        with self.assertRaises(ReconciliationError):
            self.store.get_operation(operation["operation_id"])

        for index, invalid in enumerate(
            (
                {"callback_status": "PENDING", "value": math.nan},
                {"callback_status": "PENDING", "value": object()},
                {"callback_status": lambda: None},
            ),
            start=3,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReconciliationError):
                    self.prepare(str(index), result=invalid)

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

    def test_execute_returns_terminal_idempotent_operation_without_command(self):
        operation = self.prepare()
        terminal = self.store.finish_operation(
            operation["operation_id"], "COMMITTED", {"verified": True}
        )

        with mock.patch("team_control.operations.run_argv") as command:
            result = self.ops.execute_git(
                self.dispatch_id,
                operation["action"],
                operation["request_hash"],
                operation["target_sha"],
                operation["idempotency_key"],
                ["git", "status", "--short"],
                lambda ignored: {"verified": True},
            )

        self.assertEqual(result, terminal)
        command.assert_not_called()

    def test_execute_rejects_same_idempotency_prepared_without_replay(self):
        operation = self.prepare()

        with mock.patch("team_control.operations.run_argv") as command:
            with self.assertRaisesRegex(ReconciliationError, "reconcile"):
                self.ops.execute_git(
                    self.dispatch_id,
                    operation["action"],
                    operation["request_hash"],
                    operation["target_sha"],
                    operation["idempotency_key"],
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )

        command.assert_not_called()
        self.assertEqual(
            self.store.get_operation(operation["operation_id"])["phase"],
            "PREPARED",
        )

    def test_unrelated_prepared_operation_blocks_new_git_command(self):
        existing = self.prepare("1", action="existing")

        with mock.patch("team_control.operations.run_argv") as command:
            with self.assertRaisesRegex(ReconciliationError, "reconcile"):
                self.ops.execute_git(
                    self.dispatch_id,
                    "new-operation",
                    "b" * 64,
                    self.head,
                    "new-operation-key",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )

        command.assert_not_called()
        self.assertEqual(
            [row["operation_id"] for row in self.operation_rows()],
            [existing["operation_id"]],
        )

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
                operation = self.prepare(
                    str(index), result={"callback_status": "PENDING"}
                )
                result = self.ops.reconcile_one(
                    operation["operation_id"],
                    lambda ignored, value=verified: {
                        "verified": value,
                        "reason": "observed postcondition",
                        "callback_status": "forged-by-verifier",
                    },
                )
                self.assertEqual(result["phase"], expected_phase)
                self.assertIs(result["result"]["verified"], verified)
                if verified is True:
                    self.assertEqual(
                        result["result"]["callback_status"], "PENDING"
                    )
                else:
                    self.assertNotIn("callback_status", result["result"])
                    callback = mock.Mock(
                        side_effect=AssertionError("callback must not run")
                    )
                    with mock.patch("team_control.operations.run_argv") as command:
                        repeated = self.ops.execute_git(
                            operation["dispatch_id"],
                            operation["action"],
                            operation["request_hash"],
                            operation["target_sha"],
                            operation["idempotency_key"],
                            ["git", "status", "--short"],
                            mock.Mock(
                                side_effect=AssertionError(
                                    "verifier must not rerun"
                                )
                            ),
                            on_verified=callback,
                        )
                    self.assertEqual(repeated, result)
                    callback.assert_not_called()
                    command.assert_not_called()

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

    def test_reconcile_terminal_is_idempotent_without_reinvoking_verifier(self):
        operation = self.prepare()
        terminal = self.store.finish_operation(
            operation["operation_id"], "FAILED", {"verified": False}
        )
        verifier = mock.Mock(side_effect=AssertionError("must not run"))

        result = self.ops.reconcile_one(operation["operation_id"], verifier)

        self.assertEqual(result, terminal)
        verifier.assert_not_called()
        with self.assertRaises(ReconciliationError):
            self.ops.reconcile_one(str(uuid.uuid4()), verifier)

    def test_concurrent_reconcile_all_returns_same_terminal_without_race_error(self):
        operation = self.prepare()
        barrier = threading.Barrier(2)
        original_prepared = self.store.prepared_operations
        results = []
        errors = []
        result_lock = threading.Lock()

        def synchronized_prepared():
            prepared = original_prepared()
            barrier.wait()
            return prepared

        def reconcile():
            try:
                result = self.ops.reconcile_all(
                    {operation["action"]: lambda ignored: {"verified": True}}
                )
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        with mock.patch.object(
            self.store, "prepared_operations", side_effect=synchronized_prepared
        ):
            threads = [threading.Thread(target=reconcile) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        terminals = [result[0] for result in results]
        self.assertEqual(terminals[0], terminals[1])
        self.assertEqual(terminals[0]["phase"], "COMMITTED")

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
        invalid = (
            None,
            [],
            (),
            "git status",
            ["git", 1],
            ["git", ""],
            ["/usr/bin/printf", "not-git"],
            ["git", "-C", str(self.repo), "status"],
            ["git", "-c", "alias.escape=!printf pwn", "escape"],
            ["git", "--git-dir", str(self.context.common_dir), "status"],
            ["git", "--git-dir=%s" % self.context.common_dir, "status"],
            ["git", "--work-tree=%s" % self.repo, "status"],
            ["git", "--namespace=other", "status"],
            ["git", "--exec-path=/tmp", "status"],
            ["git", "--config-env=core.editor=EDITOR", "status"],
            ["git", "status", "--git-dir=%s" % self.context.common_dir],
            ["git", "untrusted-alias"],
            ["git", "clone", "source", "target"],
        )
        for index, argv in enumerate(invalid):
            with self.subTest(argv=argv):
                with mock.patch("team_control.operations.run_argv") as command:
                    with self.assertRaises(ReconciliationError):
                        self.ops.execute_git(
                            self.dispatch_id,
                            "verify-head",
                            "a" * 64,
                            self.head,
                            "invalid-argv-%s" % index,
                            argv,
                            lambda ignored: {"verified": True},
                        )
                    command.assert_not_called()
                self.assertEqual(self.operation_rows(), [])

    def test_preflight_failure_creates_no_operation_and_runs_no_git_mutation(self):
        observed = []

        def controlled_run(argv, cwd, check=True):
            observed.append(list(argv))
            if argv == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(
                    stdout=self.head + "\n", stderr="", returncode=0
                )
            raise AssertionError("mutation command must not run")

        def reject_fresh_state(task):
            self.assertEqual(task["dispatch_id"], self.dispatch_id)
            self.assertEqual(self.operation_rows(), [])
            raise ReconciliationError("fresh preflight rejected state")

        with mock.patch(
            "team_control.operations.run_argv", side_effect=controlled_run
        ):
            with self.assertRaisesRegex(
                ReconciliationError, "fresh preflight rejected state"
            ):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "preflight-rejected-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                    preflight=reject_fresh_state,
                )

        self.assertEqual(observed, [["git", "rev-parse", "HEAD"]])
        self.assertEqual(self.operation_rows(), [])

    def test_mvp0_control_lock_threat_model_is_explicit_and_reusable(self):
        threat_model = operations_module.MVP0_CONTROL_LOCK_THREAT_MODEL

        self.assertIn("common-directory control lock", threat_model)
        self.assertIn("Orchestrator and ControlPlane SQLite writes", threat_model)
        self.assertIn(
            "Codex-managed Worktree, branch, and control-plane Git mutations",
            threat_model,
        )
        self.assertIn("ordinary add and commit", threat_model)
        self.assertIn("one Worktree, one writer", threat_model)
        self.assertIn("do not use the common-directory control lock", threat_model)
        self.assertIn("same-user filesystem access", threat_model)
        self.assertIn("deliberately bypasses the lock", threat_model)

    def test_git_subcommand_allowlist_is_explicit_for_mvp_operations(self):
        self.assertEqual(
            ALLOWED_GIT_SUBCOMMANDS,
            frozenset(
                {
                    "status",
                    "rev-parse",
                    "merge",
                    "worktree",
                    "branch",
                    "commit",
                    "cherry-pick",
                    "rebase",
                    "reset",
                    "checkout",
                    "switch",
                    "restore",
                    "tag",
                    "update-ref",
                }
            ),
        )

    def test_external_repo_path_tamper_is_rejected_before_operation_or_command(self):
        worktree_root = self.repo / ".worktrees"
        worktree_root.mkdir()
        external = make_repo(
            worktree_root / ("%s-agent-tampered" % self.dispatch_id)
        )
        self.tamper_task_worktree(external, agent="agent", slug="tampered")

        with mock.patch("team_control.operations.run_argv") as command:
            with self.assertRaises(BoundaryError):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "external-repo-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )

        command.assert_not_called()
        self.assertEqual(self.operation_rows(), [])

    def test_execute_binds_head_and_command_to_registered_task_worktree(self):
        agent = "agent"
        slug = "trusted"
        branch = "agent/%s/%s-%s" % (agent, self.dispatch_id, slug)
        worktree = self.repo / ".worktrees" / (
            "%s-%s-%s" % (self.dispatch_id, agent, slug)
        )
        worktree.parent.mkdir()
        run(["git", "worktree", "add", "-b", branch, str(worktree)], self.repo)
        self.store.attach_worktree(
            self.dispatch_id, agent, slug, branch, worktree
        )
        calls = []

        def controlled_run(argv, cwd, check=True):
            calls.append((argv, Path(cwd), check))
            if argv == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(
                    stdout=self.head + "\n", stderr="", returncode=0
                )
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with mock.patch("team_control.operations.run_argv", side_effect=controlled_run):
            terminal = self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "trusted-worktree-operation",
                ["git", "status", "--short"],
                lambda ignored: {"verified": True},
            )

        self.assertEqual(terminal["phase"], "COMMITTED")
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            all(cwd == worktree.resolve() for ignored, cwd, check in calls)
        )

    def test_execute_requires_target_task_and_trusted_git_head_to_match(self):
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET current_head_sha = ? WHERE dispatch_id = ?",
                ("b" * 40, self.dispatch_id),
            )

        with mock.patch(
            "team_control.operations.run_argv",
            return_value=SimpleNamespace(
                stdout=self.head + "\n", stderr="", returncode=0
            ),
        ) as command:
            with self.assertRaises(GitStateError):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "task-head-drift-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                )

        self.assertEqual(command.call_count, 1)
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
        observed_operations = []
        callback = mock.Mock(side_effect=AssertionError("callback must not run"))

        def run_or_crash(argv, cwd, check=True):
            if argv == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(stdout=self.head + "\n", stderr="", returncode=0)
            with self.store.read_connection() as connection:
                rows = connection.execute(
                    "SELECT operation_id FROM operations"
                ).fetchall()
            observed_operations.extend(
                self.store.get_operation(row["operation_id"]) for row in rows
            )
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
                    on_verified=callback,
                )

        self.assertEqual(len(observed_operations), 1)
        self.assertEqual(observed_operations[0]["phase"], "PREPARED")
        self.assertEqual(
            observed_operations[0]["result"], {"callback_status": "PENDING"}
        )
        self.assertEqual(self.store.prepared_operations(), observed_operations)
        callback.assert_not_called()

    def test_verifier_crash_recovers_callback_intent_without_replaying_git(self):
        callback_payloads = []
        original_run_argv = operations_module.run_argv

        def verifier_crash(ignored):
            raise RuntimeError("simulated verifier crash")

        def callback(result):
            callback_payloads.append(result)
            self.store.transition(
                self.dispatch_id, "DISPATCHED", "recovered callback intent"
            )

        with mock.patch(
            "team_control.operations.run_argv", wraps=original_run_argv
        ) as command:
            with self.assertRaisesRegex(RuntimeError, "simulated verifier crash"):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "verifier-crash-callback-operation",
                    ["git", "status", "--short"],
                    verifier_crash,
                    on_verified=callback,
                )

            prepared = self.store.prepared_operations()[0]
            self.assertEqual(prepared["phase"], "PREPARED")
            self.assertEqual(
                prepared["result"], {"callback_status": "PENDING"}
            )
            self.assertEqual(
                self.store.get_task(self.dispatch_id)["state"], "PLANNED"
            )

            reconciled = self.ops.reconcile_all(
                {
                    "verify-head": lambda ignored: {
                        "verified": True,
                        "reason": "postcondition recovered",
                    }
                }
            )
            self.assertEqual(len(reconciled), 1)
            pending = reconciled[0]
            self.assertEqual(pending["phase"], "COMMITTED")
            self.assertEqual(
                pending["result"]["callback_status"], "PENDING"
            )

            completed = self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "verifier-crash-callback-operation",
                ["git", "status", "--short"],
                mock.Mock(side_effect=AssertionError("verifier must not rerun")),
                on_verified=callback,
            )

        self.assertEqual(completed["phase"], "COMMITTED")
        self.assertEqual(completed["result"]["callback_status"], "COMPLETED")
        self.assertEqual(
            self.store.get_task(self.dispatch_id)["state"], "DISPATCHED"
        )
        self.assertEqual(len(callback_payloads), 1)
        self.assertEqual(callback_payloads[0]["callback_status"], "PENDING")
        self.assertEqual(
            sum(
                call.args[0] == ["git", "status", "--short"]
                for call in command.call_args_list
            ),
            1,
        )

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

    def test_verifier_control_write_fails_and_leaves_operation_prepared(self):
        self.store.lock_timeout = 0.05

        def writing_verifier(ignored):
            self.store.transition(self.dispatch_id, "DISPATCHED", "illegal verifier write")
            return {"verified": True}

        with self.assertRaises(StoreBusyError):
            self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "writing-verifier-operation",
                ["git", "status", "--short"],
                writing_verifier,
            )

        operation = self.store.prepared_operations()[0]
        self.assertEqual(operation["phase"], "PREPARED")
        self.assertIsNone(operation["result"])
        self.assertEqual(self.store.get_task(self.dispatch_id)["state"], "PLANNED")

    def test_on_verified_runs_after_terminal_and_can_write_store(self):
        self.store.lock_timeout = 0.05
        callback_payloads = []

        def callback(result):
            callback_payloads.append(result)
            result["verified"] = False
            result["callback_mutation"] = True
            result["nested"]["value"] = "callback-mutated"
            self.store.transition(
                self.dispatch_id, "DISPATCHED", "callback after terminal"
            )

        terminal = self.ops.execute_git(
            self.dispatch_id,
            "verify-head",
            "a" * 64,
            self.head,
            "callback-write-operation",
            ["git", "status", "--short"],
            lambda ignored: {"verified": True, "nested": {"value": "stable"}},
            on_verified=callback,
        )

        self.assertEqual(terminal["phase"], "COMMITTED")
        self.assertEqual(terminal["result"]["callback_status"], "COMPLETED")
        self.assertIs(terminal["result"]["verified"], True)
        self.assertEqual(terminal["result"]["nested"], {"value": "stable"})
        self.assertNotIn("callback_mutation", terminal["result"])
        persisted = self.store.get_operation(terminal["operation_id"])
        self.assertEqual(persisted, terminal)
        self.assertEqual(self.store.get_task(self.dispatch_id)["state"], "DISPATCHED")
        self.assertEqual(len(callback_payloads), 1)
        self.assertEqual(callback_payloads[0]["callback_status"], "PENDING")

    def test_callback_failure_is_persisted_and_terminal_retry_recovers_only_callback(self):
        callback_attempts = []
        original_run_argv = operations_module.run_argv

        def recoverable_callback(result):
            callback_attempts.append(result)
            self.assertEqual(result["callback_status"], "PENDING")
            if len(callback_attempts) == 1:
                raise RuntimeError("callback failed")
            self.store.transition(
                self.dispatch_id, "DISPATCHED", "recovered callback"
            )

        with mock.patch(
            "team_control.operations.run_argv", wraps=original_run_argv
        ) as command:
            with self.assertRaisesRegex(RuntimeError, "callback failed"):
                self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "failing-callback-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                    on_verified=recoverable_callback,
                )

            pending = self.store.get_operation(
                self.operation_rows()[0]["operation_id"]
            )
            self.assertEqual(pending["phase"], "COMMITTED")
            self.assertEqual(pending["result"]["callback_status"], "PENDING")
            self.assertEqual(self.store.get_task(self.dispatch_id)["state"], "PLANNED")

            unchanged = self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "failing-callback-operation",
                ["git", "status", "--short"],
                mock.Mock(side_effect=AssertionError("verifier must not rerun")),
            )
            self.assertEqual(unchanged, pending)

            completed = self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "failing-callback-operation",
                ["git", "status", "--short"],
                mock.Mock(side_effect=AssertionError("verifier must not rerun")),
                on_verified=recoverable_callback,
            )
            self.assertEqual(completed["result"]["callback_status"], "COMPLETED")
            self.assertEqual(self.store.get_task(self.dispatch_id)["state"], "DISPATCHED")

            callback = mock.Mock(side_effect=AssertionError("callback must not rerun"))
            repeated = self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "failing-callback-operation",
                ["git", "status", "--short"],
                mock.Mock(side_effect=AssertionError("verifier must not rerun")),
                on_verified=callback,
            )

        self.assertEqual(repeated, completed)
        callback.assert_not_called()
        self.assertEqual(len(callback_attempts), 2)
        self.assertEqual(
            sum(
                call.args[0] == ["git", "status", "--short"]
                for call in command.call_args_list
            ),
            1,
        )

    def test_callback_completion_update_is_guarded_and_idempotent(self):
        operation = self.prepare()
        pending = self.store.finish_operation(
            operation["operation_id"],
            "COMMITTED",
            {
                "verified": True,
                "command_returncode": 0,
                "stderr": "",
                "callback_status": "PENDING",
            },
        )

        completed = self.store.complete_operation_callback(
            operation["operation_id"]
        )
        repeated = self.store.complete_operation_callback(
            operation["operation_id"]
        )

        self.assertEqual(completed, repeated)
        self.assertEqual(completed["phase"], "COMMITTED")
        self.assertEqual(completed["result"]["callback_status"], "COMPLETED")
        expected_result = dict(pending["result"])
        expected_result["callback_status"] = "COMPLETED"
        self.assertEqual(completed["result"], expected_result)

    def test_callback_never_runs_when_persisted_verification_is_not_true(self):
        operation = self.prepare()
        terminal = self.store.finish_operation(
            operation["operation_id"],
            "COMMITTED",
            {"verified": False, "callback_status": "PENDING"},
        )
        callback = mock.Mock(side_effect=AssertionError("callback must not run"))

        with mock.patch("team_control.operations.run_argv") as command:
            result = self.ops.execute_git(
                self.dispatch_id,
                operation["action"],
                operation["request_hash"],
                operation["target_sha"],
                operation["idempotency_key"],
                ["git", "status", "--short"],
                lambda ignored: {"verified": True},
                on_verified=callback,
            )

        self.assertEqual(result, terminal)
        callback.assert_not_called()
        command.assert_not_called()
        with self.assertRaises(ReconciliationError):
            self.store.complete_operation_callback(operation["operation_id"])

    def test_concurrent_pending_callback_retries_never_replay_git(self):
        def fail_initially(ignored):
            raise RuntimeError("leave callback pending")

        with self.assertRaisesRegex(RuntimeError, "leave callback pending"):
            self.ops.execute_git(
                self.dispatch_id,
                "verify-head",
                "a" * 64,
                self.head,
                "concurrent-callback-operation",
                ["git", "status", "--short"],
                lambda ignored: {"verified": True},
                on_verified=fail_initially,
            )

        callback_barrier = threading.Barrier(2)
        callback_calls = []
        results = []
        errors = []
        result_lock = threading.Lock()

        def callback(result):
            callback_calls.append(result)
            callback_barrier.wait()

        def retry():
            try:
                result = self.ops.execute_git(
                    self.dispatch_id,
                    "verify-head",
                    "a" * 64,
                    self.head,
                    "concurrent-callback-operation",
                    ["git", "status", "--short"],
                    lambda ignored: {"verified": True},
                    on_verified=callback,
                )
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        with mock.patch("team_control.operations.run_argv") as command:
            threads = [threading.Thread(target=retry) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(
            all(result["result"]["callback_status"] == "COMPLETED" for result in results)
        )
        self.assertEqual(len(callback_calls), 2)
        self.assertTrue(
            all(result["callback_status"] == "PENDING" for result in callback_calls)
        )
        command.assert_not_called()

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
