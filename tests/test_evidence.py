import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from team_control.contracts import validate_record
from team_control.errors import BoundaryError, ContractError, ReconciliationError
from team_control.evidence import EvidenceManager
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.control = ControlPlane(self.context, self.store)
        self.head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.dispatch_id = "20260808-007"
        self.store.create_task({
            "schema_version": 1,
            "dispatch_id": self.dispatch_id,
            "title": "Evidence",
            "objective": "Track verified artifacts",
            "risk_level": "L2",
            "state": "PLANNED",
            "task_base_sha": self.head,
            "owner": "Codex",
        })
        self.manager = EvidenceManager(self.context, self.store)

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
            "updated_at": "2026-08-08T00:00:00+00:00",
        }
        record.update(updates)
        return record

    def test_recorded_hash_matches_regular_file_and_summary_is_distilled(self):
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")

        record = self.manager.record(
            self.dispatch_id, "test", result_file, self.head
        )

        self.assertEqual(record["path"], "result.txt")
        self.assertEqual(
            record["sha256"], hashlib.sha256(b"PASS\n").hexdigest()
        )
        validate_record("evidence", record)

        artifact = self.manager.write_summary(self.dispatch_id)
        data = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(data["dispatch_id"], self.dispatch_id)
        self.assertEqual(data["evidence"], [record])
        self.assertNotIn("file_contents", data)
        self.assertNotIn("PASS\n", artifact.read_text(encoding="utf-8"))

    def test_record_accepts_repo_relative_path(self):
        (self.repo / "relative.txt").write_text("relative\n", encoding="utf-8")

        record = self.manager.record(
            self.dispatch_id, "artifact", Path("relative.txt"), self.head
        )

        self.assertEqual(record["path"], "relative.txt")

    def test_record_rejects_escape_symlink_directory_and_missing_file(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        directory = self.repo / "directory"
        directory.mkdir()
        symlink = self.repo / "link.txt"
        symlink.symlink_to(outside)

        cases = (
            (outside, BoundaryError),
            (symlink, BoundaryError),
            (directory, BoundaryError),
            (self.repo / "missing.txt", BoundaryError),
        )
        for path, error in cases:
            with self.subTest(path=path):
                with self.assertRaises(error):
                    self.manager.record(self.dispatch_id, "artifact", path)

        self.assertEqual(self.store.list_evidence(self.dispatch_id), [])

    def test_record_rejects_symlinked_parent_even_when_target_stays_in_repo(self):
        real = self.repo / "real"
        real.mkdir()
        (real / "result.txt").write_text("PASS\n", encoding="utf-8")
        (self.repo / "redirect").symlink_to(real, target_is_directory=True)

        with self.assertRaises(BoundaryError):
            self.manager.record(
                self.dispatch_id, "artifact", self.repo / "redirect/result.txt"
            )

    def test_summary_fails_closed_when_evidence_hash_no_longer_matches(self):
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")
        self.manager.record(self.dispatch_id, "test", result_file, self.head)
        result_file.write_text("CHANGED\n", encoding="utf-8")

        with self.assertRaises(ReconciliationError):
            self.manager.write_summary(self.dispatch_id)

        self.assertFalse(
            (self.repo / "artifacts/dispatches" / self.dispatch_id /
             "evidence-index.json").exists()
        )

    def test_summary_rejects_symlinked_artifact_parent(self):
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")
        self.manager.record(self.dispatch_id, "test", result_file, self.head)
        outside = Path(self.tmp.name) / "outside-artifacts"
        outside.mkdir()
        (self.repo / "artifacts").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(BoundaryError):
            self.manager.write_summary(self.dispatch_id)

        self.assertEqual(list(outside.iterdir()), [])

    def test_agent_review_blocker_and_evidence_records_are_contract_valid(self):
        agent = self.store.upsert_agent_status(self.agent_record())
        blocker = self.store.add_blocker(
            self.dispatch_id,
            "dependency unavailable",
            "Codex",
            "dependency restored",
        )
        report = self.repo / "artifacts" / "review.md"
        report.parent.mkdir()
        report.write_text("MODIFY\n", encoding="utf-8")
        review = self.store.add_review(
            self.dispatch_id,
            "reviewer-1",
            "MODIFY",
            self.head,
            report,
        )
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")
        evidence = self.manager.record(
            self.dispatch_id, "test", result_file, self.head
        )

        for kind, expected, records in (
            ("agent_status", agent, self.store.list_agent_status(self.dispatch_id)),
            ("blocker", blocker, self.store.list_blockers(self.dispatch_id)),
            ("review", review, self.store.list_reviews(self.dispatch_id)),
            ("evidence", evidence, self.store.list_evidence(self.dispatch_id)),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(records, [expected])
                self.assertIs(validate_record(kind, records[0]), records[0])

    def test_invalid_collaborator_records_are_rejected_without_writes(self):
        with self.assertRaises(ContractError):
            self.store.upsert_agent_status(self.agent_record(progress=101))
        with self.assertRaises(ContractError):
            self.store.add_blocker(self.dispatch_id, "", "Codex", None)
        with self.assertRaises(ContractError):
            self.store.add_review(
                self.dispatch_id, "reviewer-1", "PASS", self.head, None
            )

        self.assertEqual(self.store.list_agent_status(self.dispatch_id), [])
        self.assertEqual(self.store.list_blockers(self.dispatch_id), [])
        self.assertEqual(self.store.list_reviews(self.dispatch_id), [])

    def test_store_rejects_evidence_that_bypasses_path_or_hash_verification(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        inside = self.repo / "inside.txt"
        inside.write_text("inside\n", encoding="utf-8")
        base = {
            "schema_version": 1,
            "dispatch_id": self.dispatch_id,
            "kind": "artifact",
            "source_sha": self.head,
            "created_at": "2026-08-08T00:00:00+00:00",
        }
        escaped = dict(
            base,
            evidence_id="escaped",
            path=str(outside),
            sha256=hashlib.sha256(b"outside\n").hexdigest(),
        )
        mismatched = dict(
            base,
            evidence_id="mismatched",
            path="inside.txt",
            sha256="0" * 64,
        )

        with self.assertRaises(BoundaryError):
            self.store.add_evidence(escaped)
        with self.assertRaises(ReconciliationError):
            self.store.add_evidence(mismatched)

        self.assertEqual(self.store.list_evidence(self.dispatch_id), [])

    def test_agent_status_normalizes_optional_progress_to_zero(self):
        record = self.agent_record()
        del record["progress"]

        persisted = self.store.upsert_agent_status(record)

        self.assertEqual(persisted["progress"], 0)
        self.assertEqual(
            self.store.list_agent_status(self.dispatch_id), [persisted]
        )
        validate_record("agent_status", persisted)

    def test_status_includes_collaborators_and_evidence_from_one_snapshot(self):
        initial = self.store.upsert_agent_status(self.agent_record())
        self.store.add_blocker(
            self.dispatch_id, "waiting", "Codex", "dependency restored"
        )
        report = self.repo / "review.md"
        report.write_text("MODIFY\n", encoding="utf-8")
        self.store.add_review(
            self.dispatch_id, "reviewer-1", "MODIFY", self.head, report
        )
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")
        self.manager.record(self.dispatch_id, "test", result_file, self.head)

        status = self.control.status(self.dispatch_id)
        self.store.upsert_agent_status(self.agent_record(
            state="COMPLETED",
            progress=100,
            updated_at="2026-08-08T00:01:00+00:00",
        ))
        self.assertEqual(status["agents"], [initial])
        self.assertEqual(len(status["blockers"]), 1)
        self.assertEqual(len(status["reviews"]), 1)
        self.assertEqual(len(status["evidence"]), 1)
        self.assertEqual(
            self.store.list_agent_status(self.dispatch_id)[0]["state"],
            "COMPLETED",
        )

    def test_missing_task_and_worktree_fail_with_stable_domain_errors(self):
        result_file = self.repo / "result.txt"
        result_file.write_text("PASS\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconciliationError, "missing"):
            self.manager.record("missing", "test", result_file, self.head)
        with self.assertRaisesRegex(ReconciliationError, "missing"):
            self.control.status("missing")

        branch = "agent/codex/%s-evidence" % self.dispatch_id
        missing_path = self.repo / ".worktrees" / (
            "%s-codex-evidence" % self.dispatch_id
        )
        self.store.attach_worktree(
            self.dispatch_id, "codex", "evidence", branch, missing_path
        )
        with self.assertRaisesRegex(BoundaryError, "unavailable"):
            self.control.status(self.dispatch_id)


if __name__ == "__main__":
    unittest.main()
