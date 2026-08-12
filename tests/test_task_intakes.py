import tempfile
import unittest
from pathlib import Path

from team_control.errors import (
    ContractError,
    SchemaMigrationRequiredError,
    SchemaUnsupportedError,
)
from team_control.git_context import RepoContext
from team_control.store import ControlStore
from team_control.task_intakes import (
    TaskIntakeService,
    normalize_task_intake_request,
    safe_task_intake_summary,
)
from tests.helpers import make_repo


class TaskIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = make_repo(Path(self.temporary.name) / "repo")
        self.store = ControlStore.for_repo(RepoContext.discover(self.repo))
        self.store.initialize()
        self.service = TaskIntakeService(self.store)
        self.request = {
            "title": "Add a safe task entry",
            "objective": "Let the dashboard submit a request",
            "context": "No Git from browser",
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
        }

    def test_normalize_requires_exact_bounded_fields(self):
        normalized = normalize_task_intake_request(self.request)
        self.assertEqual(normalized["title"], self.request["title"])
        self.assertEqual(normalized["objective"], self.request["objective"])
        for invalid in (
            {},
            {**self.request, "unknown": True},
            {**self.request, "title": ""},
            {**self.request, "context": "x" * 2001},
            {**self.request, "title": "\ud800"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractError):
                    normalize_task_intake_request(invalid)

    def test_submit_is_idempotent_without_creating_a_task(self):
        first = self.service.submit(self.request)
        second = self.service.submit(self.request)

        self.assertEqual(first["intake_id"], second["intake_id"])
        self.assertEqual(first["status"], "PENDING")
        with self.store.read_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM task_intake_requests").fetchone()[0],
                1,
            )

    def test_safe_summary_hides_context_and_request_hash(self):
        intake = self.service.submit(self.request)
        summary = safe_task_intake_summary(intake)

        self.assertEqual(set(summary), {
            "intake_id", "title", "objective", "status", "result_code",
            "created_at", "updated_at",
        })
        self.assertNotIn("context", summary)
        self.assertNotIn("request_hash", summary)

    def test_codex_can_read_one_or_a_bounded_pending_list(self):
        intake = self.service.submit(self.request)

        self.assertEqual(
            self.store.get_task_intake(intake["intake_id"])["context"],
            self.request["context"],
        )
        self.assertEqual(
            [item["intake_id"] for item in self.store.list_task_intakes(limit=1)],
            [intake["intake_id"]],
        )

    def test_schema_preflight_reports_missing_or_incompatible_task_intake_table(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_requests")
        with self.assertRaises(SchemaMigrationRequiredError):
            self.store.require_schema_compatible()

        self.store.initialize()
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_requests")
            connection.execute("CREATE VIEW task_intake_requests AS SELECT 1 AS intake_id")
        with self.assertRaises(SchemaUnsupportedError):
            self.store.require_schema_compatible()
