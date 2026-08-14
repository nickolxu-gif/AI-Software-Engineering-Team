import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_control.errors import BoundaryError, ContractError, GitStateError
from team_control.git_context import RepoContext
from team_control.project_registry import ProjectRegistryService
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
        self.assertEqual(events, [{
            "event_type": "PROJECT_REGISTERED",
            "project_id": summary["project_id"],
            "created_at": entry["created_at"],
        }])
        self.assertEqual(set(summary), {
            "project_id", "display_name", "status", "created_at", "updated_at",
        })
        self.assertNotIn("root_path", summary)
        self.assertNotIn("common_dir_path", summary)

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

    def test_register_rejects_a_supplied_symbolic_link_without_audit_event(self):
        link = self.root / "target-link"
        try:
            link.symlink_to(self.target_root, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest("platform cannot create a symbolic-link fixture: %s" % error)

        with self.assertRaises(BoundaryError):
            self.registry.register("Link", link)
        self.assertEqual(self.store.list_project_registry_entries(), [])
        self.assertEqual(self.store.list_project_registry_events(), [])

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
        self.assertEqual(self.store.list_project_registry_events(), [])

    def test_register_rejects_twenty_first_active_project(self):
        for number in range(20):
            self.registry.register("Project %02d" % number, self.make_target("target-%02d" % number))

        with self.assertRaises(ContractError):
            self.registry.register("Project 20", self.make_target("target-20"))
        self.assertEqual(len(self.store.list_project_registry_entries("ACTIVE")), 20)
        self.assertEqual(len(self.store.list_project_registry_events()), 20)

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
             for event in self.store.list_project_registry_events(retired["project_id"])],
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
        self.assertEqual(self.store.list_project_registry_events(), [])

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
        self.assertEqual(self.store.list_project_registry_events(project_id), [])


if __name__ == "__main__":
    unittest.main()
