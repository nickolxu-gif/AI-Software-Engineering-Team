import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from team_control.errors import BoundaryError, ContractError, CursorStaleError, GitStateError
from team_control.git_context import RepoContext
from team_control.project_registry import (
    LOCAL_SNAPSHOT_OVERHEAD_BYTES,
    MAX_TARGET_SNAPSHOT_BYTES,
    ProjectRegistryService,
    ProjectSnapshotReader,
    READONLY_GIT_PREFIX,
    TARGET_CONTROL_REQUIRED_SCHEMA,
)
from team_control.store import ControlStore
from tests.helpers import make_repo


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.central_root = make_repo(self.root / "central")
        self.target_root = make_repo(self.root / "target")
        self.context = RepoContext.discover(self.central_root)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.registry = ProjectRegistryService(self.context, self.store)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_target(self, name):
        return make_repo(self.root / name)

    def make_compatible_target_database(self, target, state="IN_PROGRESS"):
        """Create the historical core schema without central registry tables."""
        context = RepoContext.discover(target)
        database = context.common_dir / "team" / "runtime" / "team.db"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(database))
        try:
            for table, columns in TARGET_CONTROL_REQUIRED_SCHEMA.items():
                definitions = ", ".join(
                    "%s TEXT" % column for column in sorted(columns)
                )
                connection.execute("CREATE TABLE %s (%s)" % (table, definitions))
            connection.execute(
                """INSERT INTO tasks (
                       dispatch_id, title, objective, risk_level, state, owner,
                       agent, slug, branch, worktree_path, task_base_sha,
                       current_head_sha, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "20260814-101", "Safe title", "Safe objective", "L2", state,
                    "Codex", None, None, None, None, "a" * 40, "a" * 40,
                    "2026-08-14T00:00:00+00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        verification_connection = sqlite3.connect(str(database))
        try:
            tables = {
                row[0]
                for row in verification_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            verification_connection.close()
        self.assertFalse({"project_registry", "project_registry_events"} & tables)
        return database

    @staticmethod
    def file_fingerprint(path):
        candidate = Path(path)
        if not candidate.exists() and not candidate.is_symlink():
            return None
        metadata = candidate.lstat()
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )

    def registered_entry(self, name="Target"):
        summary = self.registry.register(name, self.target_root)
        return self.store.get_project_registry_entry(summary["project_id"])

    def test_register_writes_private_identity_and_audit_event_atomically(self):
        summary = self.registry.register("LifeLogger", self.target_root)

        entry = self.store.get_project_registry_entry(summary["project_id"])
        events = self.store.list_project_registry_events(summary["project_id"])
        target_context = RepoContext.discover(self.target_root)
        root_metadata = target_context.root.lstat()
        common_dir_metadata = target_context.common_dir.lstat()

        self.assertEqual(entry["display_name"], "LifeLogger")
        self.assertEqual(entry["root_path"], str(self.target_root.resolve()))
        self.assertEqual(
            (entry["root_device"], entry["root_inode"], entry["root_mode"]),
            (root_metadata.st_dev, root_metadata.st_ino, root_metadata.st_mode),
        )
        self.assertEqual(
            (
                entry["common_dir_device"], entry["common_dir_inode"],
                entry["common_dir_mode"],
            ),
            (
                common_dir_metadata.st_dev, common_dir_metadata.st_ino,
                common_dir_metadata.st_mode,
            ),
        )
        self.assertEqual(events["events"], [{
            "event_type": "PROJECT_REGISTERED",
            "project_id": summary["project_id"],
            "created_at": entry["created_at"],
        }])
        self.assertEqual(set(summary), {
            "project_id", "display_name", "status", "created_at", "updated_at",
        })
        self.assertNotIn("root_path", summary)
        self.assertNotIn("common_dir_path", summary)

    def test_registry_constructor_retains_context_keyword_compatibility(self):
        registry = ProjectRegistryService(context=self.context, store=self.store)

        self.assertIs(registry.store, self.store)

    def test_register_rejects_invalid_display_names(self):
        for value in (True, None, 7, "", "   ", "line\nbreak", "x" * 81):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    self.registry.register(value, self.target_root)

    def test_register_rejects_duplicate_display_name_and_repository_identity(self):
        self.registry.register("One", self.target_root)
        other_target = self.make_target("other-target")

        with self.assertRaises(ContractError):
            self.registry.register("One", other_target)
        with self.assertRaises(ContractError):
            self.registry.register("Two", self.target_root)

    def test_register_normalizes_display_name_and_rejects_the_trimmed_duplicate(self):
        registered = self.registry.register("  Foo  ", self.target_root)

        self.assertEqual(registered["display_name"], "Foo")
        self.assertEqual(
            self.store.get_project_registry_entry(registered["project_id"])["display_name"],
            "Foo",
        )
        with self.assertRaises(ContractError):
            self.registry.register("Foo", self.make_target("foo-duplicate"))

    def test_retired_project_can_be_registered_again_with_the_same_name_and_identity(self):
        first = self.registry.register("Target", self.target_root)
        retired = self.registry.retire(first["project_id"])
        second = self.registry.register("Target", self.target_root)

        self.assertEqual(retired["status"], "RETIRED")
        self.assertEqual(second["status"], "ACTIVE")
        self.assertNotEqual(first["project_id"], second["project_id"])
        entries = self.store.list_project_registry_entries(status=None)
        self.assertEqual([entry["status"] for entry in entries], ["RETIRED", "ACTIVE"])
        self.assertEqual(
            len(self.store.list_project_registry_events(first["project_id"])["events"]),
            2,
        )

    def test_registry_rejects_an_active_duplicate_persisted_identity(self):
        target_context = RepoContext.discover(self.target_root)
        root_metadata = target_context.root.lstat()
        common_dir_metadata = target_context.common_dir.lstat()
        self.store.create_project_registry_entry(
            "123e4567-e89b-12d3-a456-426614174001",
            "Existing alias",
            "/private/alias-one",
            "/private/common-one",
            root_metadata.st_dev,
            root_metadata.st_ino,
            root_metadata.st_mode,
            common_dir_metadata.st_dev,
            common_dir_metadata.st_ino,
            common_dir_metadata.st_mode,
        )

        with self.assertRaises(ContractError):
            self.registry.register("Second alias", self.target_root)

    def test_project_registry_event_listing_is_explicitly_paginated_without_truncation(self):
        for number in range(11):
            registered = self.registry.register(
                "Audit %02d" % number, self.make_target("audit-%02d" % number)
            )
            self.registry.retire(registered["project_id"])

        first = self.store.list_project_registry_events(limit=20)
        second = self.store.list_project_registry_events(
            limit=20, cursor=first["next_cursor"]
        )

        self.assertEqual(set(first), {"events", "next_cursor", "has_more"})
        self.assertEqual(len(first["events"]), 20)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(len(second["events"]), 2)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])

    def test_project_registry_event_cursor_rejects_malformed_timestamp(self):
        with self.assertRaises(ContractError):
            self.store.list_project_registry_events(
                cursor={
                    "created_at": "not-a-timestamp",
                    "event_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            )

    def test_project_registry_cursor_rejects_non_string_identifier(self):
        with self.assertRaises(ContractError):
            self.store.list_project_registry_events(
                cursor={
                    "created_at": "2026-08-14T00:00:00+00:00",
                    "event_id": 123,
                }
            )

    def test_project_registry_entry_listing_is_explicitly_paginated(self):
        for number in range(21):
            registered = self.registry.register(
                "Retired %02d" % number, self.make_target("retired-%02d" % number)
            )
            self.registry.retire(registered["project_id"])

        first = self.store.list_project_registry_entries_page(limit=20)
        second = self.store.list_project_registry_entries_page(
            limit=20, cursor=first["next_cursor"]
        )

        self.assertEqual(len(first["entries"]), 20)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(len(second["entries"]), 1)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(
            len(self.store.list_project_registry_entries_page()["entries"]), 20
        )

        with self.assertRaises(ContractError):
            self.store.list_project_registry_entries_page(
                cursor={
                    "created_at": "not-a-timestamp",
                    "project_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            )

        with self.assertRaises(ContractError):
            self.store.list_project_registry_entries_page(
                cursor={
                    "created_at": "2026-08-14T00:00:00+00:00",
                    "project_id": 123,
                }
            )

        for invalid_limit in (False, 0, -1, "20", 21):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaises(ContractError):
                    self.store.list_project_registry_entries_page(
                        limit=invalid_limit
                    )

    def test_project_registry_event_cursor_rejects_unknown_valid_timestamp(self):
        with self.assertRaises(CursorStaleError) as error:
            self.store.list_project_registry_events(
                cursor={
                    "created_at": "2026-08-14T00:00:00Z",
                    "event_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            )
        self.assertEqual(error.exception.code, "CURSOR_STALE")

    def test_project_registry_cursor_cannot_cross_entry_and_event_endpoints(self):
        self.registry.register("Target", self.target_root)
        entry = self.store.list_project_registry_entries_page(limit=1)["entries"][0]
        event = self.store.list_project_registry_events(limit=1)["events"][0]
        with self.store.read_connection() as connection:
            event_id = connection.execute(
                "SELECT event_id FROM project_registry_events LIMIT 1"
            ).fetchone()[0]
        with self.assertRaises(CursorStaleError):
            self.store.list_project_registry_entries_page(
                cursor={
                    "created_at": event["created_at"],
                    "project_id": event_id,
                }
            )
        with self.assertRaises(CursorStaleError):
            self.store.list_project_registry_events(
                cursor={
                    "created_at": entry["created_at"],
                    "event_id": entry["project_id"],
                }
            )

    def test_project_registry_pagination_uses_one_read_snapshot(self):
        self.registry.register("Target", self.target_root)
        original_read_snapshot = self.store.read_snapshot
        calls = []

        @contextmanager
        def tracked_read_snapshot():
            calls.append("snapshot")
            with original_read_snapshot() as connection:
                yield connection

        with mock.patch.object(
            self.store, "read_snapshot", side_effect=tracked_read_snapshot
        ):
            self.store.list_project_registry_entries_page(limit=1)
            self.store.list_project_registry_events(limit=1)

        self.assertEqual(calls, ["snapshot", "snapshot"])

    def test_registry_read_snapshot_begins_and_rolls_back(self):
        original_read_connection = self.store.read_connection
        calls = []

        @contextmanager
        def recording_read_connection():
            with original_read_connection() as connection:
                class RecordingConnection:
                    def execute(inner_self, statement, *parameters):
                        calls.append(("execute", statement))
                        return connection.execute(statement, *parameters)

                    def rollback(inner_self):
                        calls.append(("rollback",))
                        return connection.rollback()

                yield RecordingConnection()

        with mock.patch.object(
            self.store, "read_connection", side_effect=recording_read_connection
        ):
            with self.store.read_snapshot():
                pass

        self.assertIn(("execute", "BEGIN"), calls)
        self.assertIn(("rollback",), calls)

    def test_register_rejects_a_supplied_symbolic_link_without_audit_event(self):
        link = self.root / "target-link"
        try:
            link.symlink_to(self.target_root, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest("platform cannot create a symbolic-link fixture: %s" % error)

        with self.assertRaises(BoundaryError):
            self.registry.register("Link", link)
        self.assertEqual(self.store.list_project_registry_entries(), [])
        self.assertEqual(self.store.list_project_registry_events()["events"], [])

    def test_directory_identity_rejects_replacement_between_lstat_and_resolve(self):
        original_lstat = Path.lstat
        replacement_performed = False

        def replace_target_after_lstat(path):
            nonlocal replacement_performed
            metadata = original_lstat(path)
            if path == self.target_root and not replacement_performed:
                self.target_root.rename(self.root / "pre-resolve-original-target")
                replacement = self.make_target("pre-resolve-replacement-target")
                replacement.rename(self.target_root)
                replacement_performed = True
            return metadata

        with mock.patch.object(Path, "lstat", new=replace_target_after_lstat):
            with self.assertRaises(BoundaryError):
                self.registry._directory_identity(self.target_root, "target root")

        self.assertTrue(replacement_performed)

    def test_register_rejects_a_repository_replaced_at_the_same_path(self):
        outer = self

        class ReplacingRegistry(ProjectRegistryService):
            def __init__(inner, *args):
                super().__init__(*args)
                inner.capture_count = 0

            def _capture_target(inner, raw_root):
                captured = super()._capture_target(raw_root)
                if inner.capture_count == 0:
                    outer.target_root.rename(outer.root / "original-target")
                    replacement = outer.make_target("replacement-target")
                    replacement.rename(outer.target_root)
                inner.capture_count += 1
                return captured

        registry = ReplacingRegistry(self.context, self.store)
        with self.assertRaises(BoundaryError):
            registry.register("Replaced", self.target_root)

        self.assertEqual(self.store.list_project_registry_entries(), [])
        self.assertEqual(self.store.list_project_registry_events()["events"], [])

    def test_register_rejects_twenty_first_active_project(self):
        for number in range(20):
            self.registry.register("Project %02d" % number, self.make_target("target-%02d" % number))

        with self.assertRaises(ContractError):
            self.registry.register("Project 20", self.make_target("target-20"))
        self.assertEqual(len(self.store.list_project_registry_entries("ACTIVE")), 20)
        self.assertEqual(len(self.store.list_project_registry_events()["events"]), 20)

    def test_retirement_is_immutable_and_frees_active_capacity(self):
        registered = [
            self.registry.register(
                "Project %02d" % number,
                self.make_target("retire-target-%02d" % number),
            )
            for number in range(20)
        ]

        retired = self.registry.retire(registered[0]["project_id"])
        replacement = self.registry.register(
            "Replacement", self.make_target("replacement-target")
        )

        self.assertEqual(retired["status"], "RETIRED")
        self.assertIsNotNone(self.store.get_project_registry_entry(retired["project_id"])["retired_at"])
        self.assertEqual(replacement["status"], "ACTIVE")
        self.assertEqual(len(self.store.list_project_registry_entries("ACTIVE")), 20)
        self.assertEqual(
            [event["event_type"]
             for event in self.store.list_project_registry_events(retired["project_id"])["events"]],
            ["PROJECT_REGISTERED", "PROJECT_RETIRED"],
        )
        with self.assertRaises(ContractError):
            self.registry.retire(retired["project_id"])

    def test_failed_registration_leaves_neither_entry_nor_audit_event(self):
        not_a_repository = self.root / "not-a-repository"
        not_a_repository.mkdir()

        with self.assertRaises(GitStateError):
            self.registry.register("Invalid", not_a_repository)
        self.assertEqual(self.store.list_project_registry_entries(), [])
        self.assertEqual(self.store.list_project_registry_events()["events"], [])

    def test_audit_insert_failure_rolls_back_the_matching_registry_entry(self):
        project_id = "123e4567-e89b-12d3-a456-426614174000"
        target_context = RepoContext.discover(self.target_root)
        root_metadata = target_context.root.lstat()
        common_dir_metadata = target_context.common_dir.lstat()
        original_preflight = self.store._require_schema_compatible_in_connection

        def install_failing_audit_trigger(connection):
            original_preflight(connection)
            connection.execute(
                """CREATE TEMP TRIGGER fail_project_registry_audit
                   BEFORE INSERT ON project_registry_events
                   BEGIN
                       SELECT RAISE(ABORT, 'forced project registry audit failure');
                   END"""
            )

        with mock.patch.object(
            self.store,
            "_require_schema_compatible_in_connection",
            side_effect=install_failing_audit_trigger,
        ):
            with self.assertRaises(ContractError):
                self.store.create_project_registry_entry(
                    project_id,
                    "Forced failure",
                    str(target_context.root),
                    str(target_context.common_dir),
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                    root_metadata.st_mode,
                    common_dir_metadata.st_dev,
                    common_dir_metadata.st_ino,
                    common_dir_metadata.st_mode,
                )

        self.assertIsNone(self.store.get_project_registry_entry(project_id))
        self.assertEqual(self.store.list_project_registry_events(project_id)["events"], [])

    def test_snapshot_reader_returns_a_safe_healthy_card_without_registry_tables(self):
        self.make_compatible_target_database(self.target_root, "BLOCKED")
        card = ProjectSnapshotReader(self.registered_entry()).snapshot()

        self.assertEqual(card["control_status"], "HEALTHY")
        self.assertEqual(len(card["head_sha"]), 40)
        self.assertTrue(all(character in "0123456789abcdef" for character in card["head_sha"]))
        self.assertEqual(card["task_counts"]["BLOCKED"], 1)
        self.assertEqual(card["latest_task_updated_at"], "2026-08-14T00:00:00+00:00")
        self.assertEqual(set(card), {
            "project_id", "display_name", "registry_status", "sampled_at",
            "head_sha", "control_status", "task_counts",
            "latest_task_updated_at",
        })
        self.assertNotIn(str(self.target_root), repr(card))
        self.assertNotIn("root_path", card)
        self.assertNotIn("common_dir_path", card)

    def test_snapshot_reader_marks_a_missing_target_database_uninitialized_without_creating_it(self):
        entry = self.registered_entry()
        database = RepoContext.discover(self.target_root).common_dir / "team" / "runtime" / "team.db"
        database.parent.mkdir(parents=True)

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNINITIALIZED")
        self.assertFalse(database.exists())

    def test_snapshot_reader_marks_a_broken_database_link_unavailable(self):
        entry = self.registered_entry()
        database = RepoContext.discover(self.target_root).common_dir / "team" / "runtime" / "team.db"
        database.parent.mkdir(parents=True)
        try:
            database.symlink_to(self.root / "missing-team.db")
        except (NotImplementedError, OSError) as error:
            self.skipTest("platform cannot create a symbolic-link fixture: %s" % error)

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNAVAILABLE")

    def test_snapshot_reader_marks_a_missing_runtime_parent_unavailable(self):
        entry = self.registered_entry()

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNAVAILABLE")

    def test_snapshot_reader_marks_a_broken_runtime_parent_link_unavailable(self):
        entry = self.registered_entry()
        common_dir = RepoContext.discover(self.target_root).common_dir
        broken_team = common_dir / "team"
        try:
            broken_team.symlink_to(self.root / "missing-runtime", target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest("platform cannot create a symbolic-link fixture: %s" % error)

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNAVAILABLE")

    def test_snapshot_reader_marks_malformed_target_database_unsupported(self):
        entry = self.registered_entry()
        database = RepoContext.discover(self.target_root).common_dir / "team" / "runtime" / "team.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not sqlite")

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNSUPPORTED")

    def test_snapshot_reader_marks_a_database_read_error_unavailable(self):
        self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        with mock.patch(
            "team_control.project_registry.sqlite3.connect",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "UNAVAILABLE")

    def test_snapshot_reader_detects_target_replacement_without_rebinding(self):
        self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        self.target_root.rename(self.root / "original-target")
        replacement = self.make_target("replacement-target")
        replacement.rename(self.target_root)

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "IDENTITY_MISMATCH")
        self.assertNotIn(str(self.target_root), repr(card))

    def test_snapshot_reader_detects_common_directory_replacement_without_rebinding(self):
        self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        common_dir = RepoContext.discover(self.target_root).common_dir
        common_dir.rename(self.root / "original-common-dir")
        common_dir.mkdir()

        card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "IDENTITY_MISMATCH")

    def test_snapshot_reader_runtime_git_uses_only_the_head_allowlist(self):
        self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        real_run = subprocess.run
        with mock.patch(
            "team_control.project_registry.subprocess.run", wraps=real_run
        ) as observed:
            card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "HEALTHY")
        self.assertEqual(
            [call.args[0] for call in observed.call_args_list],
            [
                [
                    *READONLY_GIT_PREFIX,
                    "-C", entry["root_path"],
                    "rev-parse", "--absolute-git-dir", "--git-common-dir",
                ],
                [
                    *READONLY_GIT_PREFIX,
                    "--git-dir", entry["common_dir_path"],
                    "--work-tree", entry["root_path"],
                    "rev-parse", "HEAD",
                ],
            ],
        )
        for call in observed.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 2.0)
            self.assertEqual(call.kwargs["encoding"], "utf-8")
            self.assertEqual(call.kwargs["errors"], "replace")
            self.assertEqual(call.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(call.kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(call.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_snapshot_reader_uses_the_registered_linked_worktree_head(self):
        linked_root = self.root / "linked-target"
        subprocess.run(
            ["git", "worktree", "add", "-b", "linked-target", str(linked_root)],
            cwd=str(self.target_root),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "test: linked head"],
                cwd=str(linked_root),
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            linked_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(linked_root),
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip()
            main_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.target_root),
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip()
            self.assertNotEqual(linked_head, main_head)

            entry = self.registry.register("Linked Target", linked_root)
            card = ProjectSnapshotReader(
                self.store.get_project_registry_entry(entry["project_id"])
            ).snapshot()

            self.assertEqual(card["head_sha"], linked_head)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(linked_root)],
                cwd=str(self.target_root),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_snapshot_reader_rejects_a_git_common_directory_mismatch(self):
        entry = self.registered_entry()
        entry["common_dir_path"] = str(self.root / "different-common-dir")

        with self.assertRaises(GitStateError):
            ProjectSnapshotReader(entry)._registered_git_dir()

    def test_snapshot_reader_uses_sqlite_readonly_mode_and_denies_write_actions(self):
        database = self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        real_connect = sqlite3.connect
        calls = []

        class RecordingConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, *parameters):
                calls.append(("execute", statement))
                return self.connection.execute(statement, *parameters)

            def set_authorizer(self, authorizer):
                calls.append(("authorizer", authorizer))
                return self.connection.set_authorizer(authorizer)

            def close(self):
                return self.connection.close()

        def record_connect(*args, **kwargs):
            calls.append(("connect", args, kwargs))
            return RecordingConnection(real_connect(*args, **kwargs))

        with mock.patch(
            "team_control.project_registry.sqlite3.connect", side_effect=record_connect
        ):
            card = ProjectSnapshotReader(entry).snapshot()

        self.assertEqual(card["control_status"], "HEALTHY")
        connect = next(
            call
            for call in calls
            if call[0] == "connect"
            and Path(call[1][0]).is_relative_to(Path(tempfile.gettempdir()))
        )
        self.assertNotEqual(connect[1][0], str(database))
        self.assertTrue(Path(connect[1][0]).is_relative_to(Path(tempfile.gettempdir())))
        self.assertFalse(connect[2].get("uri", False))
        self.assertIn(("execute", "PRAGMA query_only = ON"), calls)
        authorizer_index = next(
            index for index, call in enumerate(calls) if call[0] == "authorizer"
        )
        schema_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "execute"
            and call[1].startswith("SELECT type FROM sqlite_master")
        )
        self.assertLess(authorizer_index, schema_index)
        authorizer = calls[authorizer_index][1]
        self.assertEqual(
            authorizer(getattr(sqlite3, "SQLITE_ATTACH", 24), None, None, None, None),
            sqlite3.SQLITE_DENY,
        )
        self.assertEqual(
            authorizer(getattr(sqlite3, "SQLITE_INSERT", 18), None, None, None, None),
            sqlite3.SQLITE_DENY,
        )
        self.assertEqual(
            authorizer(getattr(sqlite3, "SQLITE_PRAGMA", 19), None, None, None, None),
            sqlite3.SQLITE_DENY,
        )
        self.assertEqual(
            authorizer(
                getattr(sqlite3, "SQLITE_PRAGMA", 19),
                "table_info", None, None, None,
            ),
            sqlite3.SQLITE_OK,
        )

    def test_snapshot_reader_opens_target_inputs_with_nofollow_file_descriptors(self):
        database = self.make_compatible_target_database(self.target_root)
        observed_flags = []
        real_open = os.open

        def record_open(path, flags, *arguments, **kwargs):
            if Path(path) == database:
                observed_flags.append(flags)
            return real_open(path, flags, *arguments, **kwargs)

        with mock.patch("team_control.project_registry.os.open", side_effect=record_open):
            with ProjectSnapshotReader._local_database_snapshot(database):
                pass

        self.assertEqual(len(observed_flags), 1)
        self.assertTrue(observed_flags[0] & os.O_NOFOLLOW)
        self.assertTrue(observed_flags[0] & os.O_NONBLOCK)

    def test_snapshot_reader_fails_closed_without_required_os_capabilities(self):
        source = self.root / "source.db"
        source.write_bytes(b"safe")
        identity = ProjectSnapshotReader._snapshot_file_identity(source)

        for attribute, value in (
            ("O_NOFOLLOW", 0),
            ("O_NONBLOCK", None),
            ("O_NONBLOCK", 0),
            ("pread", None),
        ):
            with self.subTest(attribute=attribute):
                destination = self.root / ("copy-%s.db" % attribute)
                with mock.patch(
                    "team_control.project_registry.os.%s" % attribute, value
                ), mock.patch("team_control.project_registry.os.open") as open_file:
                    with self.assertRaisesRegex(OSError, "unsupported on this platform"):
                        ProjectSnapshotReader._copy_snapshot_file(
                            source, destination, identity, identity.size
                        )

                open_file.assert_not_called()
                self.assertFalse(destination.exists())

    def test_snapshot_reader_handles_unbuffered_short_reads(self):
        source = self.root / "source.db"
        destination = self.root / "copy.db"
        source.write_bytes(b"short reads remain complete")
        identity = ProjectSnapshotReader._snapshot_file_identity(source)
        real_fdopen = os.fdopen

        class ShortReader:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *arguments):
                return self.handle.__exit__(*arguments)

            def read(self, size):
                return self.handle.read(min(size, 3))

        def short_fdopen(descriptor, *arguments, **kwargs):
            return ShortReader(real_fdopen(descriptor, *arguments, **kwargs))

        with mock.patch(
            "team_control.project_registry.os.fdopen", side_effect=short_fdopen
        ):
            copied_bytes = ProjectSnapshotReader._copy_snapshot_file(
                source, destination, identity, identity.size
            )

        self.assertEqual(copied_bytes, identity.size)
        self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_snapshot_reader_rejects_content_changes_with_restored_identity(self):
        database = self.make_compatible_target_database(self.target_root)
        initial_identity = ProjectSnapshotReader._snapshot_file_identity(database)
        temporary_directory = tempfile.TemporaryDirectory(dir=str(self.root))
        self.addCleanup(temporary_directory.cleanup)
        temporary_path = Path(temporary_directory.name)
        original_contents = database.read_bytes()
        source_metadata = database.stat()
        changed_contents = bytearray(original_contents)
        changed_contents[200] ^= 1
        real_fdopen = os.fdopen
        reads = 0

        class MutatingReader:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *arguments):
                return self.handle.__exit__(*arguments)

            def read(self, size):
                nonlocal reads
                reads += 1
                if reads == 1:
                    database.write_bytes(changed_contents)
                    os.utime(
                        database,
                        ns=(source_metadata.st_atime_ns, source_metadata.st_mtime_ns),
                    )
                    chunk = self.handle.read(size)
                    database.write_bytes(original_contents)
                    os.utime(
                        database,
                        ns=(source_metadata.st_atime_ns, source_metadata.st_mtime_ns),
                    )
                    return chunk
                return self.handle.read(size)

        def record_fdopen(descriptor, *arguments, **kwargs):
            return MutatingReader(real_fdopen(descriptor, *arguments, **kwargs))

        with mock.patch(
            "team_control.project_registry.os.fdopen",
            side_effect=record_fdopen,
        ), mock.patch(
            "team_control.project_registry.tempfile.TemporaryDirectory",
            return_value=temporary_directory,
        ):
            with self.assertRaisesRegex(OSError, "changed during snapshot capture"):
                with ProjectSnapshotReader._local_database_snapshot(database):
                    pass

        self.assertEqual(reads, 1)
        self.assertFalse(temporary_path.exists())
        self.assertEqual(database.read_bytes(), original_contents)
        self.assertEqual(
            ProjectSnapshotReader._snapshot_file_identity(database), initial_identity
        )

    def test_snapshot_reader_rejects_copy_over_the_total_budget(self):
        source = self.root / "source.db"
        destination = self.root / "copy.db"
        source.write_bytes(b"exceeds")
        identity = ProjectSnapshotReader._snapshot_file_identity(source)

        with self.assertRaisesRegex(OSError, "exceeds the size budget"):
            ProjectSnapshotReader._copy_snapshot_file(source, destination, identity, 1)

    def test_snapshot_reader_rejects_oversized_source_before_hashing_it(self):
        source = self.root / "source.db"
        destination = self.root / "copy.db"
        source.write_bytes(b"exceeds")
        identity = ProjectSnapshotReader._snapshot_file_identity(source)

        with mock.patch.object(ProjectSnapshotReader, "_snapshot_digest") as digest:
            with self.assertRaises(OSError):
                ProjectSnapshotReader._copy_snapshot_file(source, destination, identity, 1)

        digest.assert_not_called()

    def test_snapshot_reader_shares_the_total_budget_between_database_and_wal(self):
        database = self.root / "target.db"
        wal = Path(str(database) + "-wal")
        database.write_bytes(b"db")
        wal.write_bytes(b"wal")
        observed_remaining_bytes = []
        original_copy = ProjectSnapshotReader._copy_snapshot_file

        def record_copy(source, destination, identity, remaining_bytes):
            observed_remaining_bytes.append((Path(source), remaining_bytes))
            return original_copy(source, destination, identity, remaining_bytes)

        with mock.patch.object(
            ProjectSnapshotReader, "_copy_snapshot_file", side_effect=record_copy
        ):
            with ProjectSnapshotReader._local_database_snapshot(database):
                pass

        self.assertEqual(
            observed_remaining_bytes,
            [
                (database, MAX_TARGET_SNAPSHOT_BYTES),
                (wal, MAX_TARGET_SNAPSHOT_BYTES - database.stat().st_size),
            ],
        )

    def test_snapshot_reader_drops_an_untrusted_latest_task_timestamp(self):
        database = self.make_compatible_target_database(self.target_root)
        connection = sqlite3.connect(str(database))
        try:
            connection.execute(
                "UPDATE tasks SET updated_at = ?", ("9999-12-31T99:99:99+00:00\\x01",)
            )
            connection.commit()
        finally:
            connection.close()

        card = ProjectSnapshotReader(self.registered_entry()).snapshot()

        self.assertEqual(card["control_status"], "HEALTHY")
        self.assertIsNone(card["latest_task_updated_at"])

    def test_snapshot_reader_does_not_touch_wal_sidecars(self):
        database = self.make_compatible_target_database(self.target_root)
        writer = sqlite3.connect(str(database))
        try:
            journal_mode = writer.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if journal_mode.lower() != "wal":
                self.skipTest("SQLite build does not support WAL fixtures")
            writer.execute(
                "UPDATE tasks SET updated_at = ?", ("2026-08-14T00:00:01+00:00",)
            )
            writer.commit()
            entry = self.registered_entry()
            tracked = [
                database,
                Path(str(database) + "-wal"),
                Path(str(database) + "-shm"),
            ]
            before = {str(path): self.file_fingerprint(path) for path in tracked}

            card = ProjectSnapshotReader(entry).snapshot()

            after = {str(path): self.file_fingerprint(path) for path in tracked}
            self.assertEqual(card["control_status"], "HEALTHY")
            self.assertEqual(
                card["latest_task_updated_at"], "2026-08-14T00:00:01+00:00"
            )
            self.assertEqual(after, before)
        finally:
            writer.close()

    def test_snapshot_reader_rejects_a_database_larger_than_the_snapshot_budget(self):
        database = self.make_compatible_target_database(self.target_root)
        with database.open("r+b") as handle:
            handle.truncate(MAX_TARGET_SNAPSHOT_BYTES + 1)

        with self.assertRaisesRegex(OSError, "exceeds the size budget"):
            with ProjectSnapshotReader._local_database_snapshot(database):
                pass
        card = ProjectSnapshotReader(self.registered_entry()).snapshot()

        self.assertEqual(card["control_status"], "UNAVAILABLE")

    def test_snapshot_reader_rejects_one_byte_below_the_full_copy_space_budget(self):
        database = self.make_compatible_target_database(self.target_root)

        with mock.patch(
            "team_control.project_registry.shutil.disk_usage",
            return_value=mock.Mock(
                free=MAX_TARGET_SNAPSHOT_BYTES + LOCAL_SNAPSHOT_OVERHEAD_BYTES - 1
            ),
        ):
            with self.assertRaisesRegex(OSError, "insufficient local space"):
                with ProjectSnapshotReader._local_database_snapshot(database):
                    pass

    def test_snapshot_reader_reserves_space_for_the_full_copy_budget(self):
        database = self.make_compatible_target_database(self.target_root)
        current_snapshot_bytes = database.stat().st_size

        with mock.patch(
            "team_control.project_registry.shutil.disk_usage",
            return_value=mock.Mock(
                free=current_snapshot_bytes + LOCAL_SNAPSHOT_OVERHEAD_BYTES
            ),
        ):
            with self.assertRaisesRegex(OSError, "insufficient local space"):
                with ProjectSnapshotReader._local_database_snapshot(database):
                    pass

    def test_snapshot_reader_accepts_the_exact_full_copy_space_budget(self):
        database = self.make_compatible_target_database(self.target_root)

        with mock.patch(
            "team_control.project_registry.shutil.disk_usage",
            return_value=mock.Mock(
                free=MAX_TARGET_SNAPSHOT_BYTES + LOCAL_SNAPSHOT_OVERHEAD_BYTES
            ),
        ):
            with ProjectSnapshotReader._local_database_snapshot(database) as snapshot:
                self.assertTrue(snapshot.is_file())

    def test_snapshot_reader_does_not_modify_target_database_or_wal_sidecars(self):
        database = self.make_compatible_target_database(self.target_root)
        entry = self.registered_entry()
        tracked = [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
        before = {str(path): self.file_fingerprint(path) for path in tracked}

        card = ProjectSnapshotReader(entry).snapshot()

        after = {str(path): self.file_fingerprint(path) for path in tracked}
        self.assertEqual(card["control_status"], "HEALTHY")
        self.assertEqual(after, before)

    def test_snapshot_reader_discards_data_when_target_replaced_during_read_window(self):
        self.make_compatible_target_database(self.target_root, "BLOCKED")
        entry = self.registered_entry()
        reader = ProjectSnapshotReader(entry)
        real_head = reader._head_sha

        def replace_target_before_result():
            self.target_root.rename(self.root / "read-window-original-target")
            replacement = self.make_target("read-window-replacement-target")
            replacement.rename(self.target_root)
            return real_head()

        with mock.patch.object(reader, "_head_sha", side_effect=replace_target_before_result):
            card = reader.snapshot()

        self.assertEqual(card["control_status"], "IDENTITY_MISMATCH")
        self.assertEqual(card["head_sha"], "HEAD_UNAVAILABLE")
        self.assertEqual(card["task_counts"], {
            state: 0 for state in sorted(ProjectSnapshotReader._public_card(entry)["task_counts"])
        })
        self.assertIsNone(card["latest_task_updated_at"])

    def test_snapshot_reader_discards_data_when_database_replaced_during_read_window(self):
        database = self.make_compatible_target_database(self.target_root, "BLOCKED")
        entry = self.registered_entry()
        reader = ProjectSnapshotReader(entry)
        real_head = reader._head_sha

        def replace_database_before_result():
            original = database.with_name("original-team.db")
            database.rename(original)
            shutil.copy2(original, database)
            return real_head()

        with mock.patch.object(reader, "_head_sha", side_effect=replace_database_before_result):
            card = reader.snapshot()

        self.assertEqual(card["control_status"], "IDENTITY_MISMATCH")
        self.assertEqual(card["head_sha"], "HEAD_UNAVAILABLE")
        self.assertTrue(all(count == 0 for count in card["task_counts"].values()))
        self.assertIsNone(card["latest_task_updated_at"])


if __name__ == "__main__":
    unittest.main()
