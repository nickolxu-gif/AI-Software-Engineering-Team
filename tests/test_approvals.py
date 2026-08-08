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

import team_control.service as service_module
from team_control.contracts import validate_record
from team_control.errors import (
    ApprovalError,
    BoundaryError,
    ContractError,
    GitStateError,
    TeamControlError,
)
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore, StoreBusyError
from tests.helpers import make_repo, run


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

    def tamper_task_worktree(self, path, agent="agent", slug="malicious"):
        branch = "agent/%s/%s-%s" % (agent, self.dispatch_id, slug)
        with self.store.mutation() as connection:
            connection.execute(
                """UPDATE tasks
                   SET agent = ?, slug = ?, branch = ?, worktree_path = ?
                   WHERE dispatch_id = ?""",
                (agent, slug, branch, str(path), self.dispatch_id),
            )

    def test_canonical_request_has_fixed_domain_separated_bytes_and_hash(self):
        canonical = service_module._canonical_approval_request_bytes(
            "20260808-004",
            "integrate",
            "a" * 40,
            {"z": [1, True, None], "a": {"key": "值"}},
        )
        expected = (
            b"team-control/approval-request/v1\n"
            b'{"action":"integrate","dispatch_id":"20260808-004",'
            b'"parameters":{"a":{"key":"\xe5\x80\xbc"},"z":[1,true,null]},'
            b'"target_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        )
        self.assertEqual(canonical, expected)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "8c6a4e3717d0fb792cc8951d9c387bbcdade92cbdb3b18255822c1402ff623f2",
        )

    def test_request_uses_canonical_hash_and_returns_public_contract(self):
        nonce = "PLAINTEXT-NONCE-SECRET"
        parameters = {"z": [1, True, None], "a": {"key": "value"}}
        approval = self.request(nonce=nonce, parameters=parameters)

        canonical = (
            "team-control/approval-request/v1\n"
            '{"action":"integrate","dispatch_id":"%s",'
            '"parameters":{"a":{"key":"value"},"z":[1,true,null]},'
            '"target_sha":"%s"}'
            % (self.dispatch_id, self.task["current_head_sha"])
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
                "expires_at",
                "consumed_at",
                "idempotency_key",
            },
        )
        self.assertEqual(approval["schema_version"], 1)
        uuid.UUID(approval["approval_id"])
        uuid.UUID(approval["idempotency_key"])
        validate_record("approval", approval)
        self.assertEqual(self.store.get_approval(approval["approval_id"]), approval)
        self.assertEqual(self.store.pending_approvals(self.dispatch_id), [approval])

        persisted = json.dumps(self.approval_rows(), sort_keys=True)
        public = json.dumps(
            {
                "approval": approval,
                "get": self.store.get_approval(approval["approval_id"]),
                "pending": self.store.pending_approvals(self.dispatch_id),
                "status": self.control.status(self.dispatch_id),
            },
            sort_keys=True,
        )
        events = json.dumps(self.store.list_events(self.dispatch_id), sort_keys=True)
        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        self.assertEqual(self.approval_rows()[0]["nonce_hash"], nonce_hash)
        self.assertNotIn(nonce, persisted)
        self.assertNotIn(nonce, public)
        self.assertNotIn(nonce_hash, public)
        self.assertNotIn("nonce_hash", public)
        self.assertNotIn(nonce, events)

    def test_nonce_can_be_consumed_only_once(self):
        nonce = "human-confirmation"
        approval = self.request(nonce=nonce)
        self.assertEqual(
            self.control.status(self.dispatch_id)["effective_state"],
            "NEEDS_HUMAN_APPROVAL",
        )

        operation = self.control.consume_approval(approval["approval_id"], nonce)

        self.assertEqual(operation["phase"], "PREPARED")
        self.assertIsNone(operation["result_json"])
        self.assertEqual(len(self.operation_rows()), 1)
        self.assertEqual(
            self.control.status(self.dispatch_id)["effective_state"], "PLANNED"
        )
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(approval["approval_id"], nonce)
        self.assertEqual(len(self.operation_rows()), 1)

        consumed = self.store.get_approval(approval["approval_id"])
        self.assertIsNotNone(consumed["consumed_at"])
        validate_record("approval", consumed)

    def test_concurrent_consumption_has_exactly_one_winner(self):
        nonce = "one-winner-nonce"
        approval = self.request(nonce=nonce)
        barrier = threading.Barrier(3)
        results = []
        result_lock = threading.Lock()

        def consume_once():
            barrier.wait()
            try:
                result = self.control.consume_approval(approval["approval_id"], nonce)
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

    def test_consume_uses_trusted_git_head_and_has_no_caller_sha_argument(self):
        nonce = "trusted-head-nonce"
        approval = self.request(nonce=nonce)
        (self.repo / "drift.txt").write_text("new head\n", encoding="utf-8")
        run(["git", "add", "--", "drift.txt"], self.repo)
        run(["git", "commit", "-m", "test: move approval head"], self.repo)

        with self.assertRaises(TypeError):
            self.control.consume_approval(
                approval["approval_id"], nonce, self.task["current_head_sha"]
            )
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(approval["approval_id"], nonce)

        self.assertEqual(self.operation_rows(), [])
        persisted = self.store.get_approval(approval["approval_id"])
        self.assertIsNone(persisted["consumed_at"])

    def test_head_observation_and_consume_share_the_control_lock(self):
        nonce = "locked-observation-nonce"
        approval = self.request(nonce=nonce)
        head_observed = threading.Event()
        writer_done = threading.Event()
        writer_results = []
        original_actual_head = self.control._trusted_actual_head

        def observed_actual_head(task):
            result = original_actual_head(task)
            head_observed.set()
            if not writer_done.wait(2.0):
                raise AssertionError("controlled Git writer did not finish")
            return result

        def controlled_git_writer():
            try:
                if not head_observed.wait(2.0):
                    raise AssertionError("approval did not observe Git HEAD")
                with self.store.mutation() as connection:
                    (self.repo / "check-use-window.txt").write_text(
                        "new head\n", encoding="utf-8"
                    )
                    run(["git", "add", "--", "check-use-window.txt"], self.repo)
                    run(
                        ["git", "commit", "-m", "test: exercise check-use window"],
                        self.repo,
                    )
                    changed_head = run(
                        ["git", "rev-parse", "HEAD"], self.repo
                    ).stdout.strip()
                    connection.execute(
                        "UPDATE tasks SET current_head_sha = ? WHERE dispatch_id = ?",
                        (changed_head, self.dispatch_id),
                    )
                writer_results.append("updated")
            except BaseException as error:
                writer_results.append(error)
            finally:
                writer_done.set()

        original_timeout = self.store.lock_timeout
        self.store.lock_timeout = 0.1
        thread = threading.Thread(target=controlled_git_writer)
        try:
            thread.start()
            with mock.patch.object(
                self.control,
                "_trusted_actual_head",
                side_effect=observed_actual_head,
            ):
                operation = self.control.consume_approval(
                    approval["approval_id"], nonce
                )
        finally:
            self.store.lock_timeout = original_timeout
            thread.join(2.0)

        self.assertFalse(thread.is_alive(), "controlled Git writer leaked")
        self.assertEqual(operation["phase"], "PREPARED")
        self.assertEqual(len(writer_results), 1)
        self.assertIsInstance(writer_results[0], StoreBusyError)
        self.assertEqual(
            self.store.get_task(self.dispatch_id)["current_head_sha"],
            self.task["current_head_sha"],
        )
        self.assertEqual(len(self.operation_rows()), 1)

    def test_same_nonce_retries_are_idempotent_and_conflicts_fail_closed(self):
        nonce = "idempotent-request-nonce"
        first = self.request(nonce=nonce)
        retry = self.request(nonce=nonce)

        self.assertEqual(retry, first)
        self.assertEqual(len(self.approval_rows()), 1)
        with self.assertRaises(ApprovalError):
            self.request(nonce=nonce, parameters={"branch": "different"})
        self.assertEqual(len(self.approval_rows()), 1)

        self.control.consume_approval(first["approval_id"], nonce)
        consumed_retry = self.request(nonce=nonce)
        self.assertEqual(consumed_retry["approval_id"], first["approval_id"])
        self.assertIsNotNone(consumed_retry["consumed_at"])
        self.assertEqual(len(self.approval_rows()), 1)

    def test_concurrent_same_nonce_requests_create_one_approval(self):
        nonce = "concurrent-request-nonce"
        barrier = threading.Barrier(3)
        results = []
        result_lock = threading.Lock()

        def request_once():
            barrier.wait()
            try:
                result = self.request(nonce=nonce)
            except BaseException as error:
                result = error
            with result_lock:
                results.append(result)

        threads = [threading.Thread(target=request_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 2)
        self.assertEqual(
            {item["approval_id"] for item in results if isinstance(item, dict)},
            {self.approval_rows()[0]["approval_id"]},
        )
        self.assertEqual(len(self.approval_rows()), 1)

    def test_nonce_requires_at_least_sixteen_characters(self):
        for nonce in (None, "", "short", " " * 16, "123456789012345"):
            with self.subTest(nonce=nonce):
                with self.assertRaisesRegex(ContractError, "at least 16"):
                    self.request(nonce=nonce)
        approval = self.request(nonce="valid-sixteen-characters")
        with self.assertRaisesRegex(ApprovalError, "at least 16"):
            self.control.consume_approval(approval["approval_id"], "short")
        self.assertEqual(self.operation_rows(), [])

    def test_nonce_comparison_is_constant_time_and_empty_nonce_is_rejected(self):
        nonce = "constant-time-secret"
        approval = self.request(nonce=nonce)
        expected_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()

        with mock.patch(
            "team_control.store.hmac.compare_digest", wraps=hmac.compare_digest
        ) as compare_digest:
            self.control.consume_approval(approval["approval_id"], nonce)

        compare_digest.assert_called_once_with(expected_hash, expected_hash)
        with self.assertRaises(ContractError):
            self.request(nonce="")
        second = self.request(nonce="another-valid-nonce")
        before = len(self.operation_rows())
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(second["approval_id"], "")
        self.assertEqual(len(self.operation_rows()), before)

    def test_rejected_consumptions_leave_approval_and_operations_unchanged(self):
        cases = (
            "wrong_nonce",
            "expired",
            "invalid_expiry",
            "missing",
            "non_pending",
            "consumed_marker",
        )
        for case in cases:
            with self.subTest(case=case):
                nonce = "approval-nonce-%s" % case
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
                elif case == "invalid_expiry":
                    with self.store.mutation() as connection:
                        connection.execute(
                            "UPDATE approvals SET expires_at = 'not-a-date' WHERE approval_id = ?",
                            (approval["approval_id"],),
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
                supplied_nonce = (
                    "wrong-approval-nonce" if case == "wrong_nonce" else nonce
                )
                before_operations = len(self.operation_rows())

                with self.assertRaises(ApprovalError):
                    self.control.consume_approval(approval_id, supplied_nonce)

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
            {"value": 1.0},
            {"value": math.nan},
            {"value": math.inf},
            {"value": 9007199254740992},
            {"value": -9007199254740992},
            {1: "non-string-key"},
            {"nested": {2: "non-string-key"}},
            {"\ue000": "private-use-key"},
            {"nested": {"\U0001f600": "emoji-key"}},
        )
        for index, parameters in enumerate(invalid):
            with self.subTest(parameters=parameters):
                before = len(self.approval_rows())
                with self.assertRaises(ContractError):
                    self.request(
                        nonce="invalid-parameters-%02d" % index,
                        parameters=parameters,
                    )
                self.assertEqual(len(self.approval_rows()), before)

        approval = self.request(
            nonce="safe-integer-bounds",
            parameters={"minimum": -9007199254740991, "maximum": 9007199254740991},
        )
        validate_record("approval", approval)

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
                        dispatch_id, action, target_sha, {}, "request-input-nonce", 10
                    )
                self.assertEqual(len(self.approval_rows()), before)

        with self.assertRaises(ApprovalError):
            self.control.request_approval(
                self.dispatch_id,
                "integrate",
                "b" * 40,
                {},
                "request-target-nonce",
                10,
            )
        with self.assertRaises(KeyError):
            self.control.request_approval(
                "missing-task",
                "integrate",
                self.task["current_head_sha"],
                {},
                "request-missing-nonce",
                10,
            )
        self.assertEqual(self.approval_rows(), [])

    def test_store_create_rolls_back_validation_and_enforces_idempotency(self):
        arguments = (
            self.dispatch_id,
            "integrate",
            self.task["current_head_sha"],
            "a" * 64,
            "store-validation-nonce",
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
            "store-idempotency-nonce-one",
            10,
            idempotency_key,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_approval(
                self.dispatch_id,
                "integrate",
                self.task["current_head_sha"],
                "b" * 64,
                "store-idempotency-nonce-two",
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
                self.request(nonce="snapshot-writer-nonce")
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
        fake_repo_context = SimpleNamespace(
            discover=mock.Mock(return_value=self.context)
        )
        with mock.patch.object(
            service_module, "RepoContext", fake_repo_context, create=True
        ):
            with mock.patch(
                "team_control.service.run_argv", return_value=completed
            ) as called:
                status = self.control.status(self.dispatch_id)
        called.assert_called_once_with(
            ["git", "rev-parse", "HEAD"], self.context.root
        )
        self.assertEqual(status["actual_head_sha"], self.task["current_head_sha"])
        self.assertFalse(status["head_drift"])

        drifted = SimpleNamespace(stdout="b" * 40 + "\n")
        with mock.patch.object(
            service_module, "RepoContext", fake_repo_context, create=True
        ):
            with mock.patch(
                "team_control.service.run_argv", return_value=drifted
            ) as called:
                status = self.control.status(self.dispatch_id)
        called.assert_called_once_with(
            ["git", "rev-parse", "HEAD"], self.context.root
        )
        self.assertEqual(status["actual_head_sha"], "b" * 40)
        self.assertTrue(status["head_drift"])

    def test_external_repo_in_normative_path_is_rejected_for_status_and_consume(self):
        approval = self.request(nonce="external-repo-cwd-nonce")
        worktree_root = self.repo / ".worktrees"
        worktree_root.mkdir()
        candidate = worktree_root / ("%s-agent-malicious" % self.dispatch_id)
        make_repo(candidate)
        self.tamper_task_worktree(candidate)

        for operation in (
            lambda: self.control.status(self.dispatch_id),
            lambda: self.control.consume_approval(
                approval["approval_id"], "external-repo-cwd-nonce"
            ),
        ):
            with self.assertRaises((BoundaryError, GitStateError)):
                operation()
        self.assertEqual(self.operation_rows(), [])

    def test_missing_tampered_worktree_is_rejected_with_domain_error(self):
        approval = self.request(nonce="missing-worktree-cwd-nonce")
        candidate = self.repo / ".worktrees" / (
            "%s-agent-malicious" % self.dispatch_id
        )
        self.tamper_task_worktree(candidate)

        for operation in (
            lambda: self.control.status(self.dispatch_id),
            lambda: self.control.consume_approval(
                approval["approval_id"], "missing-worktree-cwd-nonce"
            ),
        ):
            with self.assertRaises((BoundaryError, GitStateError)):
                operation()
        self.assertEqual(self.operation_rows(), [])

    def test_symlinked_worktree_target_is_rejected_before_git_use(self):
        approval = self.request(nonce="target-symlink-cwd-nonce")
        external = make_repo(Path(self.tmp.name) / "external-target")
        worktree_root = self.repo / ".worktrees"
        worktree_root.mkdir()
        candidate = worktree_root / ("%s-agent-malicious" % self.dispatch_id)
        candidate.symlink_to(external, target_is_directory=True)
        self.tamper_task_worktree(candidate)

        with self.assertRaises(BoundaryError):
            self.control.status(self.dispatch_id)
        with self.assertRaises(BoundaryError):
            self.control.consume_approval(
                approval["approval_id"], "target-symlink-cwd-nonce"
            )
        self.assertEqual(self.operation_rows(), [])

    def test_symlinked_worktree_root_is_rejected_before_git_use(self):
        approval = self.request(nonce="root-symlink-cwd-nonce")
        outside = Path(self.tmp.name) / "outside-worktrees"
        outside.mkdir()
        candidate = outside / ("%s-agent-malicious" % self.dispatch_id)
        make_repo(candidate)
        worktree_root = self.repo / ".worktrees"
        worktree_root.symlink_to(outside, target_is_directory=True)
        self.tamper_task_worktree(worktree_root / candidate.name)

        with self.assertRaises(BoundaryError):
            self.control.status(self.dispatch_id)
        with self.assertRaises(BoundaryError):
            self.control.consume_approval(
                approval["approval_id"], "root-symlink-cwd-nonce"
            )
        self.assertEqual(self.operation_rows(), [])

    def test_expired_approval_is_audited_without_blocking_and_needs_new_nonce(self):
        nonce = "expired-visible-nonce"
        approval = self.request(nonce=nonce)
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                (expired, approval["approval_id"]),
            )

        status = self.control.status(self.dispatch_id)

        self.assertEqual(status["pending_approvals"], [])
        self.assertEqual(status["effective_state"], self.task["state"])
        self.assertEqual(
            self.store.get_approval(approval["approval_id"])["approval_id"],
            approval["approval_id"],
        )
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(approval["approval_id"], nonce)
        self.assertEqual(self.operation_rows(), [])

        retry = self.request(nonce=nonce)
        self.assertEqual(retry["approval_id"], approval["approval_id"])
        self.assertEqual(len(self.approval_rows()), 1)
        fresh = self.request(nonce="fresh-after-expiry-nonce")
        operation = self.control.consume_approval(
            fresh["approval_id"], "fresh-after-expiry-nonce"
        )
        self.assertEqual(operation["phase"], "PREPARED")

    def test_rfc3339_z_expiry_is_timezone_aware_for_future_and_past(self):
        future = self.request(nonce="future-z-expiry-nonce")
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                ("2999-01-01T00:00:00Z", future["approval_id"]),
            )
        self.assertEqual(
            [item["approval_id"] for item in self.store.pending_approvals(self.dispatch_id)],
            [future["approval_id"]],
        )
        operation = self.control.consume_approval(
            future["approval_id"], "future-z-expiry-nonce"
        )
        self.assertEqual(operation["phase"], "PREPARED")

        past = self.request(nonce="past-z-expiry-nonce")
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                ("2000-01-01T00:00:00Z", past["approval_id"]),
            )
        self.assertEqual(self.store.pending_approvals(self.dispatch_id), [])
        with self.assertRaises(ApprovalError):
            self.control.consume_approval(past["approval_id"], "past-z-expiry-nonce")
        self.assertEqual(len(self.operation_rows()), 1)


if __name__ == "__main__":
    unittest.main()
