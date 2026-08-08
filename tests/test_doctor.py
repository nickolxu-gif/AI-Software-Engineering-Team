import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_control.doctor import WorktreeDoctor
from team_control.errors import BoundaryError, ReconciliationError
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class DoctorTests(unittest.TestCase):
    dispatch_id = "20260808-006"
    agent = "codex"
    slug = "minor"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        (self.repo / ".worktrees").mkdir()
        self.context = RepoContext.discover(self.repo)
        self.base = run(
            ["git", "rev-parse", "main"], self.repo
        ).stdout.strip()
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.store.create_task(
            {
                "schema_version": 1,
                "dispatch_id": self.dispatch_id,
                "title": "Minor",
                "objective": "Repair branch-only residue",
                "risk_level": "L1",
                "state": "PLANNED",
                "task_base_sha": self.base,
                "owner": "Codex",
            }
        )
        self.doctor = WorktreeDoctor(self.context, self.store)
        self.branch = "agent/codex/20260808-006-minor"
        self.path = (
            self.context.common_dir.parent
            / ".worktrees"
            / "20260808-006-codex-minor"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def inspect(self):
        return self.doctor.inspect(
            self.dispatch_id, self.agent, self.slug, self.base
        )

    def operation(self, action):
        with self.store.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE action = ?", (action,)
            ).fetchone()
        return dict(row) if row is not None else None

    def add_expected_worktree(self):
        run(
            [
                "git", "worktree", "add", "-b", self.branch,
                str(self.path), self.base,
            ],
            self.repo,
        )

    def test_no_residue_create_uses_controlled_git_and_becomes_healthy(self):
        report = self.inspect()
        self.assertEqual(report["classification"], "NO_RESIDUE")

        created = self.doctor.create(report)

        self.assertEqual(created["classification"], "HEALTHY")
        self.assertEqual(created["path"], str(self.path))
        self.assertEqual(created["head_sha"], self.base)
        self.assertTrue(created["clean"])
        self.assertEqual(created["common_dir"], str(self.context.common_dir))
        self.assertEqual(self.operation("create-worktree")["phase"], "COMMITTED")
        task = self.store.get_task(self.dispatch_id)
        self.assertEqual(task["branch"], self.branch)
        self.assertEqual(task["worktree_path"], str(self.path))

    def test_branch_only_at_exact_base_is_the_only_repairable_residue(self):
        run(["git", "branch", self.branch, self.base], self.repo)
        report = self.inspect()
        self.assertEqual(report["classification"], "REPAIRABLE_BRANCH_ONLY")

        repaired = self.doctor.repair(report)

        self.assertEqual(repaired["classification"], "HEALTHY")
        self.assertEqual(
            self.operation("doctor-reconstruct-worktree")["phase"],
            "COMMITTED",
        )

    def test_unknown_directory_residue_is_preserved_and_blocked(self):
        self.path.mkdir(parents=True)
        unknown = self.path / "unknown.txt"
        unknown.write_text("preserve me", encoding="utf-8")

        report = self.inspect()

        self.assertEqual(report["classification"], "BLOCKED_PATH_RESIDUE")
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)
        self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve me")

    def test_advanced_unregistered_branch_is_preserved_and_blocked(self):
        run(["git", "checkout", "-b", self.branch, self.base], self.repo)
        (self.repo / "change.txt").write_text("commit\n", encoding="utf-8")
        run(["git", "add", "--", "change.txt"], self.repo)
        run(["git", "commit", "-m", "test: advance branch"], self.repo)
        advanced = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        run(["git", "checkout", "main"], self.repo)

        report = self.inspect()

        self.assertEqual(report["classification"], "BLOCKED_BRANCH_ADVANCED")
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)
        self.assertEqual(
            run(["git", "rev-parse", self.branch], self.repo).stdout.strip(),
            advanced,
        )

    def test_stale_metadata_is_preserved_and_blocked(self):
        self.add_expected_worktree()
        shutil.rmtree(self.path)

        report = self.inspect()

        self.assertEqual(report["classification"], "BLOCKED_STALE_METADATA")
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)
        porcelain = run(
            ["git", "worktree", "list", "--porcelain"], self.repo
        ).stdout
        self.assertIn("worktree %s" % self.path, porcelain)

    def test_expected_path_registered_to_other_branch_is_blocked(self):
        other = "agent/codex/other-task"
        run(
            [
                "git", "worktree", "add", "-b", other,
                str(self.path), self.base,
            ],
            self.repo,
        )

        report = self.inspect()

        self.assertEqual(
            report["classification"], "BLOCKED_REGISTRATION_MISMATCH"
        )

    def test_dirty_registered_worktree_is_blocked_and_untouched(self):
        self.add_expected_worktree()
        dirty = self.path / "dirty.txt"
        dirty.write_text("do not delete\n", encoding="utf-8")

        report = self.inspect()

        self.assertEqual(report["classification"], "BLOCKED_DIRTY_WORKTREE")
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)
        self.assertEqual(dirty.read_text(encoding="utf-8"), "do not delete\n")

    def test_symlinked_root_and_target_are_blocked_without_following_them(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        self.repo.joinpath(".worktrees").rmdir()
        self.repo.joinpath(".worktrees").symlink_to(
            outside, target_is_directory=True
        )
        root_report = self.inspect()
        self.assertEqual(
            root_report["classification"], "BLOCKED_ROOT_SYMLINK"
        )
        self.repo.joinpath(".worktrees").unlink()
        self.repo.joinpath(".worktrees").mkdir()
        target = outside / "target"
        target.mkdir()
        self.path.symlink_to(target, target_is_directory=True)

        target_report = self.inspect()

        self.assertEqual(
            target_report["classification"], "BLOCKED_PATH_SYMLINK"
        )
        self.assertEqual(list(outside.iterdir()), [target])

    def test_registered_path_from_another_common_dir_is_blocked(self):
        self.add_expected_worktree()
        external = make_repo(Path(self.tmp.name) / "external")
        git_marker = self.path / ".git"
        git_marker.unlink()
        git_marker.symlink_to(external / ".git", target_is_directory=True)

        report = self.inspect()

        self.assertEqual(
            report["classification"], "BLOCKED_REPOSITORY_MISMATCH"
        )

    def test_linked_context_uses_main_repository_worktree_root(self):
        linked = Path(self.tmp.name) / "linked"
        run(
            ["git", "worktree", "add", "-b", "linked-doctor", str(linked)],
            self.repo,
        )
        linked_context = RepoContext.discover(linked)
        linked_doctor = WorktreeDoctor(linked_context, self.store)

        report = linked_doctor.inspect(
            self.dispatch_id, self.agent, self.slug, self.base
        )

        self.assertEqual(report["path"], str(self.path))
        self.assertNotEqual(
            report["path"], str(linked / ".worktrees" / self.path.name)
        )

    def test_stale_report_cannot_authorize_repair_after_state_changes(self):
        run(["git", "branch", self.branch, self.base], self.repo)
        report = self.inspect()
        self.path.mkdir()
        marker = self.path / "preserve.txt"
        marker.write_text("new residue", encoding="utf-8")

        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)

        self.assertTrue(marker.is_file())

    def test_start_write_task_dispatches_only_after_verified_creation(self):
        control = ControlPlane(self.context, self.store)
        task = control.start_write_task(
            "20260808-007", "Writer", "Create isolated worktree", "L1",
            "codex", "writer",
        )

        self.assertEqual(task["state"], "DISPATCHED")
        self.assertEqual(task["branch"], "agent/codex/20260808-007-writer")
        self.assertTrue(Path(task["worktree_path"]).is_dir())

    def test_start_write_task_failure_leaves_task_planned(self):
        blocked_path = self.repo / ".worktrees" / "20260808-008-codex-writer"
        blocked_path.mkdir()
        marker = blocked_path / "preserve.txt"
        marker.write_text("unknown", encoding="utf-8")
        control = ControlPlane(self.context, self.store)

        with self.assertRaises(ReconciliationError):
            control.start_write_task(
                "20260808-008", "Writer", "Fail closed", "L1",
                "codex", "writer",
            )

        task = self.store.get_task("20260808-008")
        self.assertEqual(task["state"], "PLANNED")
        self.assertIsNone(task["worktree_path"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "unknown")

    def test_first_write_task_creates_missing_ignored_worktree_root(self):
        self.repo.joinpath(".worktrees").rmdir()
        control = ControlPlane(self.context, self.store)

        task = control.start_write_task(
            "20260808-009", "First writer", "Create root safely", "L1",
            "codex", "first",
        )

        self.assertEqual(task["state"], "DISPATCHED")
        self.assertTrue(Path(task["worktree_path"]).is_dir())

    def test_committed_create_replays_only_pending_attach_callback(self):
        report = self.inspect()
        original_attach = self.store.attach_worktree
        attempts = []

        def interrupted_attach(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError("simulated callback interruption")
            return original_attach(*args)

        with mock.patch.object(
            self.store, "attach_worktree", side_effect=interrupted_attach
        ):
            with self.assertRaisesRegex(RuntimeError, "callback interruption"):
                self.doctor.create(report)

            self.assertEqual(self.inspect()["classification"], "HEALTHY")
            recovered = self.doctor.create(report)

        self.assertEqual(recovered["classification"], "HEALTHY")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            self.operation("create-worktree")["phase"], "COMMITTED"
        )
        self.assertEqual(
            self.store.get_task(self.dispatch_id)["worktree_path"],
            str(self.path),
        )

    def test_start_write_task_retry_recovers_pending_attach_and_dispatches(self):
        control = ControlPlane(self.context, self.store)
        original_attach = self.store.attach_worktree
        attempts = []

        def interrupted_attach(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError("simulated start interruption")
            return original_attach(*args)

        arguments = (
            "20260808-011", "Retry writer", "Resume the same start", "L1",
            "codex", "retry",
        )
        with mock.patch.object(
            self.store, "attach_worktree", side_effect=interrupted_attach
        ):
            with self.assertRaisesRegex(RuntimeError, "start interruption"):
                control.start_write_task(*arguments)
            self.assertEqual(
                self.store.get_task("20260808-011")["state"], "PLANNED"
            )

            recovered = control.start_write_task(*arguments)

        self.assertEqual(recovered["state"], "DISPATCHED")
        self.assertEqual(len(attempts), 2)

    def test_failed_create_gets_new_operation_only_after_safe_reinspection(self):
        self.repo.joinpath(".worktrees").rmdir()
        self.repo.joinpath(".worktrees").write_text(
            "temporarily unavailable", encoding="utf-8"
        )
        report = self.inspect()

        with self.assertRaises(ReconciliationError):
            self.doctor.create(report)

        first = self.operation("create-worktree")
        self.assertEqual(first["phase"], "FAILED")
        self.repo.joinpath(".worktrees").unlink()
        run(["git", "branch", "-d", self.branch], self.repo)

        created = self.doctor.create(report)

        self.assertEqual(created["classification"], "HEALTHY")
        with self.store.read_connection() as connection:
            operations = connection.execute(
                """SELECT phase, idempotency_key FROM operations
                   WHERE action = 'create-worktree'
                   ORDER BY created_at"""
            ).fetchall()
        self.assertEqual([row["phase"] for row in operations], ["FAILED", "COMMITTED"])
        self.assertNotEqual(
            operations[0]["idempotency_key"], operations[1]["idempotency_key"]
        )

    def test_start_write_task_repairs_only_exact_base_branch_residue(self):
        dispatch_id = "20260808-012"
        branch = "agent/codex/20260808-012-retry"
        run(["git", "branch", branch, self.base], self.repo)
        control = ControlPlane(self.context, self.store)

        task = control.start_write_task(
            dispatch_id, "Repair writer", "Use safe Doctor repair", "L1",
            "codex", "retry",
        )

        self.assertEqual(task["state"], "DISPATCHED")
        self.assertEqual(task["branch"], branch)
        self.assertTrue(Path(task["worktree_path"]).is_dir())

    def test_paused_task_can_be_inspected_but_not_mutated(self):
        self.store.transition(self.dispatch_id, "BLOCKED", "pause setup")
        self.store.transition(
            self.dispatch_id, "PAUSE_REQUESTED", "pause requested"
        )
        self.store.transition(self.dispatch_id, "PAUSED", "safe checkpoint")

        report = self.inspect()

        self.assertEqual(report["classification"], "NO_RESIDUE")
        with self.assertRaises(ReconciliationError):
            self.doctor.create(report)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.operation("create-worktree"))

    def test_invalid_identity_is_rejected_before_task_is_persisted(self):
        control = ControlPlane(self.context, self.store)

        with self.assertRaises(BoundaryError):
            control.start_write_task(
                "20260808-010", "Invalid", "Reject before insert", "L1",
                "bad/agent", "writer",
            )

        self.assertIsNone(self.store.get_task("20260808-010"))

    def test_attached_task_identity_mismatch_blocks_before_git_mutation(self):
        self.store.attach_worktree(
            self.dispatch_id,
            "codex",
            "other",
            "agent/codex/20260808-006-other",
            "/tmp/other",
        )

        report = self.inspect()

        self.assertEqual(report["classification"], "BLOCKED_TASK_MISMATCH")
        with self.assertRaises(ReconciliationError):
            self.doctor.create(report)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.operation("create-worktree"))

    def test_store_refuses_to_replace_attached_worktree_identity(self):
        self.store.attach_worktree(
            self.dispatch_id,
            "codex",
            "first",
            "agent/codex/20260808-006-first",
            "/tmp/first",
        )

        with self.assertRaises(ReconciliationError):
            self.store.attach_worktree(
                self.dispatch_id,
                "codex",
                "second",
                "agent/codex/20260808-006-second",
                "/tmp/second",
            )

        task = self.store.get_task(self.dispatch_id)
        self.assertEqual(task["slug"], "first")
        self.assertEqual(task["worktree_path"], "/tmp/first")


if __name__ == "__main__":
    unittest.main()
