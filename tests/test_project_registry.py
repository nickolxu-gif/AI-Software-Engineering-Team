import tempfile
import unittest
from pathlib import Path

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

        self.assertEqual(entry["display_name"], "LifeLogger")
        self.assertEqual(entry["root_path"], str(self.target_root.resolve()))
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


if __name__ == "__main__":
    unittest.main()
