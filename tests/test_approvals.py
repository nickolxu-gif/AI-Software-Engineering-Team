import hashlib
import hmac
import json
import math
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from team_control.contracts import validate_record
from team_control.errors import (
    ApprovalError,
    ContractError,
    TeamControlError,
)
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo


DEFAULT_PARAMETERS = object()


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.control = ControlPlane(self.context, self.store)
        self.dispatch_id = "20260808-004"
        self.task = self.control.create_task(
            self.dispatch_id,
            "Approval",
            "Prove one-shot approval",
            "L3",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def request(
        self,
        nonce="human-confirmation",
        parameters=DEFAULT_PARAMETERS,
        ttl=10,
    ):
        if parameters is DEFAULT_PARAMETERS:
            parameters = {"branch": "candidate"}
        return self.control.request_approval(
            self.dispatch_id,
            "integrate",
            self.task["current_head_sha"],
            parameters,
            nonce,
            ttl,
        )

    def approval_rows(self):
        with self.store.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM approvals ORDER BY approval_id"
                ).fetchall()
            ]

    def operation_rows(self):
        with self.store.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM operations ORDER BY operation_id"
                ).fetchall()
            ]

    def test_request_uses_canonical_hash_and_returns_public_contract(self):
        nonce = "PLAINTEXT-NONCE-SECRET"
        parameters = {"z": [1, True, None], "a": {"key": "value"}}
        approval = self.request(nonce=nonce, parameters=parameters)

        canonical = json.dumps(
            {
                "dispatch_id": self.dispatch_id,
                "action": "integrate",
                "target_sha": self.task["current_head_sha"],
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self.assertEqual(
            approval["request_hash"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            set(approval),
            {
                "schema_version",
                "approval_id",
                "dispatch_id",
                "action",
                "target_sha",
                "request_hash",
                "nonce_hash",
                "expires_at",
                "consumed_at",
                "idempotency_key",
            },
        )
        self.assertEqual(approval["schema_version"], 1)
        self.assertEqual(
            approval["nonce_hash"], hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        )
        uuid.UUID(approval["approval_id"])
        uuid.UUID(approval["idempotency_key"])
        validate_record("approval", approval)
        self.assertEqual(self.store.get_approval(approval["approval_id"]), approval)
        self.assertEqual(self.store.pending_approvals(self.dispatch_id), [approval])

        persisted = json.dumps(self.approval_rows(), sort_keys=True)
        returned = json.dumps(approval, sort_keys=True)
        events = json.dumps(self.store.list_events(self.dispatch_id), sort_keys=True)
        self.assertNotIn(nonce, persisted)
        self.assertNotIn(nonce, returned)
        self.assertNotIn(nonce, events)

    def test_nonce_can_be_consumed_only_once(self):
        nonce = "human-confirmation"
        approval = self.request(nonce=nonce)
        self.assertEqual(
            self.control.status(self.dispatch_id)["effective_state"],
            "NEEDS_HUMAN_APPROVAL",
        )

        operation = self.control.consume_approval(
            approval["approval_id"], nonce, self.task["current_head_sha"]
        )

        self.assertEqual(operation["phase"], "PREPARED")
        self.assertIsNone(operation["result_json"])
        self.assertEqual(len(self.operation_rows()), 1)
        self.assertEqual(
            self.control.status(self.dispatch_id)["effective_state"], "PLANNED"
        )
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(
                approval["approval_id"], nonce, self.task["current_head_sha"]
            )
        self.assertEqual(len(self.operation_rows()), 1)

        consumed = self.store.get_approval(approval["approval_id"])
        self.assertIsNotNone(consumed["consumed_at"])
        validate_record("approval", consumed)

    def test_concurrent_consumption_has_exactly_one_winner(self):
        nonce = "one-winner"
        approval = self.request(nonce=nonce)
        barrier = threading.Barrier(3)
        results = []
        result_lock = threading.Lock()

        def consume_once():
            barrier.wait()
            try:
                result = self.control.consume_approval(
                    approval["approval_id"], nonce, self.task["current_head_sha"]
                )
            except BaseException as error:
                result = error
            with result_lock:
                results.append(result)

        threads = [threading.Thread(target=consume_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(sum(isinstance(item, ApprovalError) for item in results), 1)
        self.assertEqual(len(self.operation_rows()), 1)

    def test_nonce_comparison_is_constant_time_and_empty_nonce_is_rejected(self):
        nonce = "constant-time-secret"
        approval = self.request(nonce=nonce)
        expected_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()

        with mock.patch(
            "team_control.store.hmac.compare_digest", wraps=hmac.compare_digest
        ) as compare_digest:
            self.control.consume_approval(
                approval["approval_id"], nonce, self.task["current_head_sha"]
            )

        compare_digest.assert_called_once_with(expected_hash, expected_hash)
        with self.assertRaises(ContractError):
            self.request(nonce="")
        second = self.request(nonce="nonempty")
        before = len(self.operation_rows())
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(
                second["approval_id"], "", self.task["current_head_sha"]
            )
        self.assertEqual(len(self.operation_rows()), before)

    def test_rejected_consumptions_leave_approval_and_operations_unchanged(self):
        cases = (
            "wrong_nonce",
            "target_drift",
            "expired",
            "missing",
            "non_pending",
            "consumed_marker",
        )
        for case in cases:
            with self.subTest(case=case):
                nonce = "nonce-%s" % case
                approval = None if case == "missing" else self.request(nonce=nonce)
                if case == "expired":
                    expired = (
                        datetime.now(timezone.utc) - timedelta(minutes=1)
                    ).isoformat()
                    with self.store.mutation() as connection:
                        connection.execute(
                            "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                            (expired, approval["approval_id"]),
                        )
                elif case == "non_pending":
                    with self.store.mutation() as connection:
                        connection.execute(
                            "UPDATE approvals SET status = 'REJECTED' WHERE approval_id = ?",
                            (approval["approval_id"],),
                        )
                elif case == "consumed_marker":
                    with self.store.mutation() as connection:
                        connection.execute(
                            "UPDATE approvals SET consumed_at = ? WHERE approval_id = ?",
                            (datetime.now(timezone.utc).isoformat(), approval["approval_id"]),
                        )

                approval_id = str(uuid.uuid4()) if approval is None else approval["approval_id"]
                supplied_nonce = "wrong" if case == "wrong_nonce" else nonce
                actual_sha = "b" * 40 if case == "target_drift" else self.task["current_head_sha"]
                before_operations = len(self.operation_rows())

                with self.assertRaises(ApprovalError):
                    self.control.consume_approval(
                        approval_id, supplied_nonce, actual_sha
                    )

                self.assertEqual(len(self.operation_rows()), before_operations)
                if approval is not None:
                    with self.store.read_connection() as connection:
                        row = connection.execute(
                            "SELECT status, consumed_at FROM approvals WHERE approval_id = ?",
                            (approval_id,),
                        ).fetchone()
                    expected_status = (
                        "REJECTED" if case == "non_pending" else "PENDING"
                    )
                    self.assertEqual(row["status"], expected_status)
                    if case == "consumed_marker":
                        self.assertIsNotNone(row["consumed_at"])
                    else:
                        self.assertIsNone(row["consumed_at"])

    def test_ttl_must_be_finite_and_within_one_day(self):
        invalid = (None, True, "10", 0, -1, 0.999, 1440.001, math.nan, math.inf)
        for ttl in invalid:
            with self.subTest(ttl=ttl):
                before = len(self.approval_rows())
                with self.assertRaises(ContractError):
                    self.request(ttl=ttl)
                self.assertEqual(len(self.approval_rows()), before)

        for ttl in (1, 1440):
            with self.subTest(valid_ttl=ttl):
                approval = self.request(ttl=ttl)
                validate_record("approval", approval)

    def test_parameters_must_be_strict_json_object(self):
        invalid = (
            None,
            [],
            "not-an-object",
            {"value": object()},
            {"value": (1, 2)},
            {"value": math.nan},
            {"value": math.inf},
            {1: "non-string-key"},
            {"nested": {2: "non-string-key"}},
        )
        for parameters in invalid:
            with self.subTest(parameters=parameters):
                before = len(self.approval_rows())
                with self.assertRaises(ContractError):
                    self.request(parameters=parameters)
                self.assertEqual(len(self.approval_rows()), before)

    def test_request_inputs_fail_closed_and_target_must_match_task(self):
        invalid = (
            ("../dispatch", "integrate", self.task["current_head_sha"]),
            (self.dispatch_id, "../action", self.task["current_head_sha"]),
            (self.dispatch_id, "", self.task["current_head_sha"]),
            (self.dispatch_id, None, self.task["current_head_sha"]),
            (self.dispatch_id, "integrate", "ABC"),
            (self.dispatch_id, "integrate", None),
        )
        for dispatch_id, action, target_sha in invalid:
            with self.subTest(
                dispatch_id=dispatch_id, action=action, target_sha=target_sha
            ):
                before = len(self.approval_rows())
                with self.assertRaises(TeamControlError):
                    self.control.request_approval(
                        dispatch_id, action, target_sha, {}, "nonce", 10
                    )
                self.assertEqual(len(self.approval_rows()), before)

        with self.assertRaises(ApprovalError):
            self.control.request_approval(
                self.dispatch_id, "integrate", "b" * 40, {}, "nonce", 10
            )
        with self.assertRaises(KeyError):
            self.control.request_approval(
                "missing-task", "integrate", self.task["current_head_sha"], {}, "nonce", 10
            )
        self.assertEqual(self.approval_rows(), [])

    def test_store_create_rolls_back_validation_and_enforces_idempotency(self):
        arguments = (
            self.dispatch_id,
            "integrate",
            self.task["current_head_sha"],
            "a" * 64,
            "nonce",
            10,
            str(uuid.uuid4()),
        )
        with mock.patch(
            "team_control.store.validate_record",
            side_effect=ContractError("invalid public approval"),
        ):
            with self.assertRaises(ContractError):
                self.store.create_approval(*arguments)
        self.assertEqual(self.approval_rows(), [])

        idempotency_key = str(uuid.uuid4())
        first = self.store.create_approval(
            self.dispatch_id,
            "integrate",
            self.task["current_head_sha"],
            "b" * 64,
            "nonce",
            10,
            idempotency_key,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_approval(
                self.dispatch_id,
                "integrate",
                self.task["current_head_sha"],
                "b" * 64,
                "nonce",
                10,
                idempotency_key,
            )
        self.assertEqual(len(self.approval_rows()), 1)
        self.assertEqual(self.store.get_approval(first["approval_id"]), first)

    def test_status_reads_task_events_and_approvals_from_one_snapshot(self):
        task_read = threading.Event()
        writer_started = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        read_count = {"value": 0}
        main_thread = threading.current_thread()
        original_read_connection = self.store.read_connection

        @contextmanager
        def coordinated_read_connection():
            read_count["value"] += 1
            with original_read_connection() as connection:
                class CoordinatedConnection:
                    def execute(inner_self, statement, parameters=()):
                        cursor = connection.execute(statement, parameters)
                        normalized = " ".join(statement.split())
                        if (
                            threading.current_thread() is main_thread
                            and normalized.startswith("SELECT * FROM tasks")
                        ):
                            task_read.set()
                            if not writer_started.wait(5.0):
                                raise AssertionError("approval writer did not start")
                        return cursor

                yield CoordinatedConnection()

        def writer():
            try:
                if not task_read.wait(5.0):
                    raise AssertionError("status did not read task")
                writer_started.set()
                self.request(nonce="snapshot-writer")
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        thread = threading.Thread(target=writer)
        with mock.patch.object(
            self.store, "read_connection", coordinated_read_connection
        ):
            thread.start()
            status = self.control.status(self.dispatch_id)
            thread.join(5.0)

        self.assertFalse(thread.is_alive(), "approval writer leaked")
        self.assertTrue(writer_done.is_set())
        if writer_errors:
            raise writer_errors[0]
        self.assertEqual(read_count["value"], 1)
        self.assertEqual(status["pending_approvals"], [])
        self.assertEqual(status["effective_state"], self.task["state"])
        self.assertEqual(len(self.store.pending_approvals(self.dispatch_id)), 1)

    def test_status_missing_task_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.control.status("missing-task")

    def test_status_uses_fixed_git_argv_and_reports_head_drift(self):
        completed = SimpleNamespace(stdout=self.task["current_head_sha"] + "\n")
        with mock.patch(
            "team_control.service.run_argv", return_value=completed
        ) as called:
            status = self.control.status(self.dispatch_id)
        called.assert_called_once_with(
            ["git", "rev-parse", "HEAD"], self.context.root
        )
        self.assertEqual(status["actual_head_sha"], self.task["current_head_sha"])
        self.assertFalse(status["head_drift"])

        worktree_path = str(self.context.root)
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET worktree_path = ? WHERE dispatch_id = ?",
                (worktree_path, self.dispatch_id),
            )
        drifted = SimpleNamespace(stdout="b" * 40 + "\n")
        with mock.patch(
            "team_control.service.run_argv", return_value=drifted
        ) as called:
            status = self.control.status(self.dispatch_id)
        called.assert_called_once_with(
            ["git", "rev-parse", "HEAD"], worktree_path
        )
        self.assertEqual(status["actual_head_sha"], "b" * 40)
        self.assertTrue(status["head_drift"])

    def test_expired_pending_approval_remains_visible_and_blocks_status(self):
        approval = self.request(nonce="expired-visible")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                (expired, approval["approval_id"]),
            )

        status = self.control.status(self.dispatch_id)

        self.assertEqual(
            [item["approval_id"] for item in status["pending_approvals"]],
            [approval["approval_id"]],
        )
        self.assertEqual(status["effective_state"], "NEEDS_HUMAN_APPROVAL")
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(
                approval["approval_id"],
                "expired-visible",
                self.task["current_head_sha"],
            )
        self.assertEqual(self.operation_rows(), [])


if __name__ == "__main__":
    unittest.main()
