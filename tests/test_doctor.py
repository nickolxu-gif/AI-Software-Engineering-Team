import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import team_control.operations as operations_module
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

    def test_expected_branch_registered_at_another_path_is_blocked(self):
        other_path = self.repo / ".worktrees" / "unexpected-location"
        run(
            [
                "git", "worktree", "add", "-b", self.branch,
                str(other_path), self.base,
            ],
            self.repo,
        )

        report = self.inspect()

        self.assertEqual(
            report["classification"], "BLOCKED_REGISTRATION_MISMATCH"
        )
        with self.assertRaises(ReconciliationError):
            self.doctor.repair(report)
        self.assertTrue(other_path.is_dir())
        self.assertFalse(self.path.exists())

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

    def test_linked_advanced_head_still_starts_from_main_head(self):
        linked = Path(self.tmp.name) / "linked-advanced"
        run(
            [
                "git", "worktree", "add", "-b", "linked-advanced",
                str(linked), self.base,
            ],
            self.repo,
        )
        (linked / "advanced.txt").write_text("advanced\n", encoding="utf-8")
        run(["git", "add", "--", "advanced.txt"], linked)
        run(["git", "commit", "-m", "test: advance linked"], linked)
        linked_head = run(["git", "rev-parse", "HEAD"], linked).stdout.strip()
        control = ControlPlane(RepoContext.discover(linked), self.store)

        task = control.start_write_task(
            "20260808-013", "Linked writer", "Use main base", "L1",
            "codex", "linked",
        )

        self.assertNotEqual(linked_head, self.base)
        self.assertEqual(task["task_base_sha"], self.base)
        self.assertEqual(
            run(
                ["git", "rev-parse", "HEAD"], Path(task["worktree_path"])
            ).stdout.strip(),
            self.base,
        )

    def test_main_root_must_be_on_main_and_clean(self):
        run(["git", "checkout", "-b", "not-main"], self.repo)
        control = ControlPlane(self.context, self.store)

        with self.assertRaisesRegex(ReconciliationError, "current branch must be main"):
            control.start_write_task(
                "20260808-014", "Wrong branch", "Fail closed", "L1",
                "codex", "wrong-branch",
            )

        self.assertIsNone(self.operation("create-worktree"))
        self.assertEqual(
            run(
                ["git", "show-ref", "--verify", "--quiet",
                 "refs/heads/agent/codex/20260808-014-wrong-branch"],
                self.repo,
                check=False,
            ).returncode,
            1,
        )

    def test_dirty_main_root_is_rejected_before_operation(self):
        (self.repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        control = ControlPlane(self.context, self.store)

        with self.assertRaisesRegex(ReconciliationError, "main root must be clean"):
            control.start_write_task(
                "20260808-015", "Dirty main", "Fail closed", "L1",
                "codex", "dirty-main",
            )

        self.assertIsNone(self.operation("create-worktree"))

    def test_unignored_worktree_root_is_rejected_before_operation(self):
        self.repo.joinpath(".gitignore").write_text("", encoding="utf-8")
        run(["git", "add", "--", ".gitignore"], self.repo)
        run(["git", "commit", "-m", "test: stop ignoring worktrees"], self.repo)
        control = ControlPlane(self.context, self.store)

        with self.assertRaisesRegex(ReconciliationError, "must be ignored"):
            control.start_write_task(
                "20260808-016", "Unsafe root", "Fail closed", "L1",
                "codex", "unignored",
            )

        self.assertIsNone(self.operation("create-worktree"))

    def test_task_base_must_equal_fresh_main_head_before_create(self):
        (self.repo / "main-advance.txt").write_text("advance\n", encoding="utf-8")
        run(["git", "add", "--", "main-advance.txt"], self.repo)
        run(["git", "commit", "-m", "test: advance main"], self.repo)
        report = self.inspect()
        self.assertEqual(report["classification"], "NO_RESIDUE")

        with self.assertRaisesRegex(ReconciliationError, "main HEAD"):
            self.doctor.create(report)

        self.assertIsNone(self.operation("create-worktree"))
        self.assertEqual(
            run(
                ["git", "show-ref", "--verify", "--quiet",
                 "refs/heads/%s" % self.branch],
                self.repo,
                check=False,
            ).returncode,
            1,
        )

    def test_locked_preflight_rejects_root_symlink_from_stale_window(self):
        report = self.inspect()
        self.assertEqual(report["classification"], "NO_RESIDUE")
        outside = Path(self.tmp.name) / "outside-race"
        outside.mkdir()
        original_execute = self.doctor.operations.execute_git

        def replace_root_before_locked_preflight(*args, **kwargs):
            self.repo.joinpath(".worktrees").rmdir()
            self.repo.joinpath(".worktrees").symlink_to(
                outside, target_is_directory=True
            )
            return original_execute(*args, **kwargs)

        with mock.patch.object(
            self.doctor.operations,
            "execute_git",
            side_effect=replace_root_before_locked_preflight,
        ):
            with self.assertRaisesRegex(ReconciliationError, "SYMLINK|symlink"):
                self.doctor.create(report)

        self.assertIsNone(self.operation("create-worktree"))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(
            run(
                ["git", "show-ref", "--verify", "--quiet",
                 "refs/heads/%s" % self.branch],
                self.repo,
                check=False,
            ).returncode,
            1,
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

    def test_start_retry_reconciles_prepared_healthy_without_replaying_git(self):
        control = ControlPlane(self.context, self.store)
        arguments = (
            "20260808-017", "Verifier crash", "Recover prepared operation", "L1",
            "codex", "verifier-crash",
        )

        with mock.patch.object(
            WorktreeDoctor,
            "_verified",
            side_effect=RuntimeError("simulated verifier crash"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated verifier crash"):
                control.start_write_task(*arguments)

        prepared = self.store.prepared_operations()
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["phase"], "PREPARED")
        self.assertEqual(
            prepared[0]["result"], {"callback_status": "PENDING"}
        )
        task = self.store.get_task(arguments[0])
        self.assertEqual(task["state"], "PLANNED")
        self.assertEqual(
            WorktreeDoctor(self.context, self.store).inspect(
                arguments[0], arguments[4], arguments[5], task["task_base_sha"]
            )["classification"],
            "HEALTHY",
        )

        with mock.patch(
            "team_control.operations.run_argv",
            wraps=operations_module.run_argv,
        ) as command:
            recovered = control.start_write_task(*arguments)

        self.assertEqual(recovered["state"], "DISPATCHED")
        self.assertEqual(
            sum(
                call.args[0][:3] == ["git", "worktree", "add"]
                for call in command.call_args_list
            ),
            0,
        )
        with self.store.read_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE dispatch_id = ?",
                (arguments[0],),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        operation = self.store.get_operation(prepared[0]["operation_id"])
        self.assertEqual(operation["phase"], "COMMITTED")
        self.assertEqual(operation["result"]["callback_status"], "COMPLETED")

    def test_concurrent_identical_starts_are_idempotent(self):
        arguments = (
            "20260808-018", "Concurrent", "Create exactly once", "L1",
            "codex", "concurrent",
        )
        barrier = threading.Barrier(3)
        results = []
        errors = []
        result_lock = threading.Lock()
        with self.store.read_connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0], "wal"
            )

        def start_once():
            try:
                barrier.wait()
                result = ControlPlane(self.context, self.store).start_write_task(
                    *arguments
                )
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [threading.Thread(target=start_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(10.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        if errors:
            raise errors[0].with_traceback(errors[0].__traceback__)
        self.assertEqual(len(results), 2)
        self.assertEqual({task["state"] for task in results}, {"DISPATCHED"})
        self.assertEqual(
            {task["worktree_path"] for task in results},
            {
                str(
                    self.context.common_dir.parent
                    / ".worktrees"
                    / "20260808-018-codex-concurrent"
                )
            },
        )
        with self.store.read_connection() as connection:
            operation_count = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE dispatch_id = ?",
                (arguments[0],),
            ).fetchone()[0]
        self.assertEqual(operation_count, 1)

    def test_concurrent_conflicting_starts_return_domain_error(self):
        dispatch_id = "20260808-019"
        barrier = threading.Barrier(3)
        results = []
        errors = []
        result_lock = threading.Lock()

        def start_once(slug):
            try:
                barrier.wait()
                result = ControlPlane(self.context, self.store).start_write_task(
                    dispatch_id, "Concurrent conflict", "Choose one identity", "L1",
                    "codex", slug,
                )
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [
            threading.Thread(target=start_once, args=(slug,))
            for slug in ("first", "second")
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(10.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ReconciliationError)
        self.assertEqual(
            str(errors[0]), "existing write task does not match the start request"
        )
        self.assertNotIn("IntegrityError", type(errors[0]).__name__)
        self.assertEqual(self.store.get_task(dispatch_id)["state"], "DISPATCHED")

    def test_failed_create_gets_new_operation_only_after_safe_reinspection(self):
        report = self.inspect()
        original_run_argv = operations_module.run_argv

        def fail_worktree_add(argv, cwd, check=True):
            if list(argv[:3]) == ["git", "worktree", "add"]:
                return SimpleNamespace(
                    stdout="", stderr="simulated add failure", returncode=1
                )
            return original_run_argv(argv, cwd, check=check)

        with mock.patch(
            "team_control.operations.run_argv", side_effect=fail_worktree_add
        ):
            with self.assertRaises(ReconciliationError):
                self.doctor.create(report)

        first = self.operation("create-worktree")
        self.assertEqual(first["phase"], "FAILED")
        self.path.mkdir()
        marker = self.path / "preserve.txt"
        marker.write_text("unsafe retry", encoding="utf-8")

        with self.assertRaises(ReconciliationError):
            self.doctor.create(report)
        with self.store.read_connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM operations WHERE action = 'create-worktree'"
                ).fetchone()[0],
                1,
            )

        marker.unlink()
        self.path.rmdir()

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
