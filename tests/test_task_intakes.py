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
    CodexTaskIntakeService,
    TaskIntakeSubmissionService,
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
        self.service = TaskIntakeSubmissionService(self.store)
        self.codex_service = CodexTaskIntakeService(self.store)
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

    def test_codex_acknowledgement_removes_an_intake_from_pending_queue(self):
        intake = self.service.submit(self.request)
        dispatch_id = self._create_formal_task()

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(intake["intake_id"], "missing-task")
        acknowledged = self.codex_service.acknowledge(intake["intake_id"], dispatch_id)

        self.assertEqual(acknowledged["status"], "ACKNOWLEDGED")
        self.assertEqual(acknowledged["result_code"], "DISPATCHED")
        self.assertEqual(self.store.list_pending_task_intakes(limit=1), [])
        self.assertEqual(
            self.codex_service.acknowledge(intake["intake_id"], dispatch_id)["status"],
            "ACKNOWLEDGED",
        )
        handling = self.store.get_task_intake_handling(intake["intake_id"])
        self.assertEqual(handling["dispatch_id"], dispatch_id)
        self.assertEqual(handling["disposition"], "DISPATCHED")

    def test_codex_acknowledgement_rejects_a_dispatch_already_bound_to_another_intake(self):
        first = self.service.submit(self.request)
        second = self.service.submit({
            **self.request,
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174001",
        })
        dispatch_id = self._create_formal_task()
        self.codex_service.acknowledge(first["intake_id"], dispatch_id)

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(second["intake_id"], dispatch_id)
        self.assertEqual(
            self.store.get_task_intake(second["intake_id"])["status"], "PENDING"
        )

    def test_codex_acknowledgement_rejects_conflicting_replay_disposition(self):
        intake = self.service.submit(self.request)
        dispatch_id = self._create_formal_task()
        self.codex_service.acknowledge(intake["intake_id"], dispatch_id)

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(
                intake["intake_id"], dispatch_id, disposition="BLOCKED"
            )

    def test_codex_can_acknowledge_blocked_intake_only_with_open_blocker(self):
        intake = self.service.submit(self.request)
        dispatch_id = self._create_formal_task()

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(
                intake["intake_id"], dispatch_id, disposition="BLOCKED"
            )
        self.store.add_blocker(dispatch_id, "Needs a decision", "Codex", None)
        blocked = self.codex_service.acknowledge(
            intake["intake_id"], dispatch_id, disposition="BLOCKED"
        )

        self.assertEqual(blocked["status"], "ACKNOWLEDGED")
        self.assertEqual(blocked["result_code"], "BLOCKED")
        self.assertEqual(
            self.store.get_task_intake_handling(intake["intake_id"])["disposition"],
            "BLOCKED",
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

    def test_initialize_migrates_legacy_pending_only_task_intake_schema(self):
        self.service.submit(self.request)
        with self.store.mutation() as connection:
            row = connection.execute(
                "SELECT * FROM task_intake_requests"
            ).fetchone()
            connection.execute("DROP TABLE task_intake_requests")
            connection.execute(
                """CREATE TABLE task_intake_requests (
                       intake_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                       objective TEXT NOT NULL, context TEXT, request_hash TEXT NOT NULL,
                       status TEXT NOT NULL CHECK (status IN ('PENDING')),
                       result_code TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                       created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO task_intake_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(row),
            )

        self.store.initialize()

        dispatch_id = self._create_formal_task()
        self.assertEqual(self.codex_service.acknowledge(row["intake_id"], dispatch_id)["status"], "ACKNOWLEDGED")
        self.store.initialize()

    def test_initialize_rejects_unknown_legacy_intake_schema_without_rebuild(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_requests")
            connection.execute(
                """CREATE TABLE task_intake_requests (
                       intake_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                       objective TEXT NOT NULL, context TEXT, request_hash TEXT NOT NULL,
                       status TEXT NOT NULL CHECK (status IN ('PENDING')),
                       result_code TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                       created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                       unexpected TEXT
                   )"""
            )
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()
        with self.store.read_connection() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(task_intake_requests)")]
        self.assertIn("unexpected", columns)

    def _create_formal_task(self):
        dispatch_id = "20260813-009"
        self.store.create_task({
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "title": "Formal task for intake",
            "objective": "Persist a formal handling record",
            "risk_level": "L1",
            "state": "PLANNED",
            "task_base_sha": "0" * 40,
            "owner": "Codex",
        })
        return dispatch_id
