import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from team_control.contracts import validate_record
from team_control.errors import BoundaryError, ContractError, ReconciliationError
from team_control.evidence import EvidenceManager
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class EvidenceHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.control = ControlPlane(self.context, self.store)
        self.dispatch_id = "20260808-hardening"
        self.head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.store.create_task({
            "schema_version": 1,
            "dispatch_id": self.dispatch_id,
            "title": "Hardening",
            "objective": "Close Task 9 trust gaps",
            "risk_level": "L2",
            "state": "PLANNED",
            "task_base_sha": self.head,
            "owner": "Codex",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def agent_record(self, **updates):
        record = {
            "schema_version": 1,
            "dispatch_id": self.dispatch_id,
            "agent_id": "worker-1",
            "role": "executor",
            "model": "configured-default",
            "state": "IN_PROGRESS",
            "progress": 25,
            "updated_at": "2026-08-08T08:00:00+08:00",
        }
        record.update(updates)
        return record

    def set_task_head(self, sha):
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET current_head_sha = ? WHERE dispatch_id = ?",
                (sha, self.dispatch_id),
            )

    def commit(self, message):
        run(["git", "commit", "--allow-empty", "-m", message], self.repo)
        return run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def review_file(self, name="review.md"):
        path = self.repo / "artifacts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("review\n", encoding="utf-8")
        return path

    def test_linked_worktree_uses_main_relative_identity_and_linked_bytes(self):
        root = self.repo / ".worktrees"
        root.mkdir()
        linked = root / "linked-evidence"
        branch = "agent/codex/%s-linked-evidence" % self.dispatch_id
        run(
            ["git", "worktree", "add", "-b", branch, str(linked)],
            self.repo,
        )
        self.store.attach_worktree(
            self.dispatch_id,
            "codex",
            "linked-evidence",
            branch,
            linked,
        )
        (self.repo / "result.txt").write_text("MAIN\n", encoding="utf-8")
        linked_result = linked / "result.txt"
        linked_result.write_text("LINKED\n", encoding="utf-8")
        linked_context = RepoContext.discover(linked)
        manager = EvidenceManager(linked_context, self.store)

        record = manager.record(
            self.dispatch_id, "test", linked_result, self.head
        )
        summary = manager.write_summary(self.dispatch_id)

        self.assertEqual(record["path"], ".worktrees/linked-evidence/result.txt")
        self.assertEqual(
            record["sha256"], hashlib.sha256(b"LINKED\n").hexdigest()
        )
        self.assertEqual(
            summary,
            self.repo.resolve() / "artifacts/dispatches" / self.dispatch_id /
            "evidence-index.json",
        )
        self.assertEqual(
            json.loads(summary.read_text(encoding="utf-8"))["evidence"][0]["path"],
            record["path"],
        )

    def test_agent_report_rejects_unknown_secret_and_bad_lengths_or_types(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas/agent-status.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        cases = (
            self.agent_record(raw_secret="token-value"),
            self.agent_record(agent_id="x" * 129),
            self.agent_record(model=["not", "text"]),
        )
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(ContractError):
                    self.store.upsert_agent_status(record)
        self.assertEqual(self.store.list_agent_status(self.dispatch_id), [])

    def test_agent_status_normalizes_time_and_rejects_stale_or_conflicting_upsert(self):
        first = self.store.upsert_agent_status(self.agent_record())
        self.assertEqual(first["updated_at"], "2026-08-08T00:00:00+00:00")

        with self.assertRaisesRegex(ReconciliationError, "stale"):
            self.store.upsert_agent_status(self.agent_record(
                state="COMPLETED",
                updated_at="2026-08-07T23:59:59+00:00",
            ))
        with self.assertRaisesRegex(ReconciliationError, "conflict"):
            self.store.upsert_agent_status(self.agent_record(
                state="COMPLETED",
                updated_at="2026-08-08T00:00:00Z",
            ))
        same = self.store.upsert_agent_status(self.agent_record(
            updated_at="2026-08-08T00:00:00Z"
        ))
        self.assertEqual(same, first)
        current = self.store.list_agent_status(self.dispatch_id)[0]
        self.assertEqual(current["state"], "IN_PROGRESS")

    def test_review_and_evidence_require_existing_task_bound_commit_and_safe_report(self):
        report = self.review_file()
        outside = Path(self.tmp.name) / "outside-review.md"
        outside.write_text("outside\n", encoding="utf-8")
        fake = "f" * 40
        result = self.repo / "result.txt"
        result.write_text("PASS\n", encoding="utf-8")
        manager = EvidenceManager(self.context, self.store)

        with self.assertRaises(ReconciliationError):
            self.store.add_review(
                self.dispatch_id, "reviewer", "MODIFY", fake, report
            )
        with self.assertRaises(BoundaryError):
            self.store.add_review(
                self.dispatch_id, "reviewer", "MODIFY", self.head, outside
            )
        with self.assertRaises(ReconciliationError):
            manager.record(self.dispatch_id, "test", result, fake)
        with self.assertRaises(ContractError):
            manager.record(self.dispatch_id, "test", result, None)

    def test_accept_must_target_current_head_and_status_marks_later_drift_stale(self):
        report = self.review_file()
        newer = self.commit("test: newer head")
        self.set_task_head(newer)
        ancestor_review = self.store.add_review(
            self.dispatch_id, "reviewer", "MODIFY", self.head, report
        )
        with self.assertRaisesRegex(ReconciliationError, "current head"):
            self.store.add_review(
                self.dispatch_id, "reviewer", "ACCEPT", self.head, report
            )
        accepted = self.store.add_review(
            self.dispatch_id, "reviewer", "ACCEPT", newer, report
        )
        evidence_file = self.repo / "result.txt"
        evidence_file.write_text("PASS\n", encoding="utf-8")
        EvidenceManager(self.context, self.store).record(
            self.dispatch_id, "test", evidence_file, newer
        )

        latest = self.commit("test: drift after acceptance")
        self.set_task_head(latest)
        evidence_file.write_text("CHANGED\n", encoding="utf-8")
        status = self.control.status(self.dispatch_id)

        reviews = {record["review_id"]: record for record in status["reviews"]}
        self.assertFalse(reviews[ancestor_review["review_id"]]["stale"])
        self.assertTrue(reviews[accepted["review_id"]]["stale"])
        self.assertFalse(reviews[accepted["review_id"]]["effective"])
        self.assertIn(accepted["review_id"], status["review_stale"])
        self.assertTrue(status["evidence_stale"])
        self.assertFalse(status["valid_acceptance"])

    def test_all_task_scoped_writes_translate_missing_task_inside_transaction(self):
        report = self.review_file()
        result = self.repo / "result.txt"
        result.write_text("PASS\n", encoding="utf-8")
        record = self.agent_record(dispatch_id="missing")
        evidence = {
            "schema_version": 1,
            "evidence_id": "missing-evidence",
            "dispatch_id": "missing",
            "kind": "test",
            "path": "result.txt",
            "sha256": hashlib.sha256(b"PASS\n").hexdigest(),
            "source_sha": self.head,
            "created_at": "2026-08-08T00:00:00+00:00",
        }
        operations = (
            lambda: self.store.upsert_agent_status(record),
            lambda: self.store.add_blocker("missing", "reason", "Codex", None),
            lambda: self.store.add_review(
                "missing", "reviewer", "MODIFY", self.head, report
            ),
            lambda: self.store.add_evidence(evidence),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ReconciliationError):
                    operation()

    def test_resolve_blocker_is_idempotent_and_reports_resolution_time(self):
        blocker = self.store.add_blocker(
            self.dispatch_id, "waiting", "Codex", "dependency restored"
        )

        resolved = self.store.resolve_blocker(
            self.dispatch_id, blocker["blocker_id"]
        )
        repeated = self.store.resolve_blocker(
            self.dispatch_id, blocker["blocker_id"]
        )

        self.assertEqual(resolved, repeated)
        self.assertEqual(resolved["status"], "RESOLVED")
        self.assertIsNotNone(resolved["resolved_at"])
        validate_record("blocker", resolved)
        with self.assertRaises(ReconciliationError):
            self.store.resolve_blocker(self.dispatch_id, "missing-blocker")
        with self.assertRaises(ReconciliationError):
            self.store.resolve_blocker("missing", blocker["blocker_id"])

    def test_status_snapshot_and_git_head_are_observed_under_same_control_lock(self):
        observer_entered = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        original = self.control._trusted_actual_head

        def observed(task):
            observer_entered.set()
            self.assertFalse(
                writer_done.wait(0.2),
                "controlled Git writer entered during status observation",
            )
            return original(task)

        def writer():
            try:
                if not observer_entered.wait(5.0):
                    raise AssertionError("status did not observe HEAD")
                with self.store.mutation() as connection:
                    run(
                        ["git", "commit", "--allow-empty", "-m", "test: concurrent head"],
                        self.repo,
                    )
                    current = run(
                        ["git", "rev-parse", "HEAD"], self.repo
                    ).stdout.strip()
                    connection.execute(
                        "UPDATE tasks SET current_head_sha = ? WHERE dispatch_id = ?",
                        (current, self.dispatch_id),
                    )
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        thread = threading.Thread(target=writer)
        with mock.patch.object(self.control, "_trusted_actual_head", observed):
            thread.start()
            status = self.control.status(self.dispatch_id)
            thread.join(5.0)

        self.assertFalse(thread.is_alive())
        if writer_errors:
            raise writer_errors[0]
        self.assertEqual(status["actual_head_sha"], self.head)
        self.assertEqual(status["task"]["current_head_sha"], self.head)
        self.assertIn("observed_at", status)


if __name__ == "__main__":
    unittest.main()
