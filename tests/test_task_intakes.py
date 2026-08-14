import tempfile
import unittest
from pathlib import Path

from team_control.errors import (
    ContractError,
    ReconciliationError,
    SchemaMigrationRequiredError,
    SchemaUnsupportedError,
)
from team_control.git_context import RepoContext
from team_control.store import (
    ControlStore,
    MAX_PENDING_INTENT_BATCH,
    MAX_TASK_INTAKE_RECORDS,
)
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

    def test_submit_rejects_new_request_when_local_inbox_capacity_is_reached(self):
        for index in range(MAX_TASK_INTAKE_RECORDS):
            self.service.submit({
                **self.request,
                "idempotency_key": "123e4567-e89b-12d3-a456-%012d" % index,
            })

        with self.assertRaises(ContractError):
            self.service.submit({
                **self.request,
                "idempotency_key": "123e4567-e89b-12d3-a456-999999999999",
            })
        with self.store.read_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM task_intake_requests").fetchone()[0],
                MAX_TASK_INTAKE_RECORDS,
            )

    def test_submit_accepts_new_request_after_an_acknowledged_record_leaves_pending_inbox(self):
        intakes = []
        for index in range(MAX_TASK_INTAKE_RECORDS):
            intakes.append(self.service.submit({
                **self.request,
                "idempotency_key": "123e4567-e89b-12d3-a456-%012d" % index,
            }))

        dispatch_id = self._create_formal_task()
        self.codex_service.acknowledge(intakes[0]["intake_id"], dispatch_id)

        accepted = self.service.submit({
            **self.request,
            "idempotency_key": "123e4567-e89b-12d3-a456-999999999999",
        })
        self.assertEqual(accepted["status"], "PENDING")
        self.assertEqual(
            self._intake_status_counts(),
            {"ACKNOWLEDGED": 1, "PENDING": MAX_TASK_INTAKE_RECORDS},
        )

    def test_submit_returns_only_browser_safe_summary(self):
        intake = self.service.submit(self.request)

        self.assertEqual(set(intake), {
            "intake_id", "title", "objective", "status", "result_code",
            "created_at", "updated_at",
        })
        self.assertNotIn("context", intake)
        self.assertNotIn("request_hash", intake)
        self.assertNotIn("idempotency_key", intake)

    def test_store_rejects_control_characters_from_direct_task_intake_callers(self):
        with self.assertRaises(ContractError):
            self.store.create_task_intake(
                "unsafe\nintake", self.request["objective"], self.request["context"],
                "0" * 64, self.request["idempotency_key"],
            )

    def test_submit_rejects_unicode_control_and_format_characters(self):
        for index, unsafe_title in enumerate(("unsafe\u0085intake", "unsafe\u202eintake")):
            with self.subTest(unsafe_title=unsafe_title):
                with self.assertRaises(ContractError):
                    self.service.submit({
                        **self.request,
                        "title": unsafe_title,
                        "idempotency_key": "123e4567-e89b-12d3-a456-%012d" % index,
                    })

    def test_task_intake_readers_support_bounded_pagination(self):
        for index in range(MAX_PENDING_INTENT_BATCH + 1):
            self.service.submit({
                **self.request,
                "idempotency_key": "123e4567-e89b-12d3-a456-%012d" % index,
            })

        with self.store.read_connection() as connection:
            expected_pending = [row["intake_id"] for row in connection.execute(
                """SELECT intake_id FROM task_intake_requests
                   WHERE status = 'PENDING'
                   ORDER BY created_at, intake_id LIMIT 1 OFFSET ?""",
                (MAX_PENDING_INTENT_BATCH,),
            )]
            expected_all = [row["intake_id"] for row in connection.execute(
                """SELECT intake_id FROM task_intake_requests
                   ORDER BY created_at, intake_id LIMIT 1 OFFSET ?""",
                (MAX_PENDING_INTENT_BATCH,),
            )]

        self.assertEqual(
            [item["intake_id"] for item in self.store.list_pending_task_intakes(
                limit=1, offset=MAX_PENDING_INTENT_BATCH,
            )],
            expected_pending,
        )
        self.assertEqual(
            [item["intake_id"] for item in self.store.list_task_intakes(
                limit=1, offset=MAX_PENDING_INTENT_BATCH,
            )],
            expected_all,
        )

    def test_task_intake_readers_reject_invalid_pagination_offsets(self):
        for offset in (-1, 10001, True):
            with self.subTest(offset=offset):
                with self.assertRaises(ContractError):
                    self.store.list_pending_task_intakes(limit=1, offset=offset)
                with self.assertRaises(ContractError):
                    self.store.list_task_intakes(limit=1, offset=offset)

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
        self.assertIsNone(self.store.get_task_intake_handling(second["intake_id"]))
        self.assertEqual(
            self.store.get_task_intake_handling(first["intake_id"])["dispatch_id"],
            dispatch_id,
        )

    def test_codex_acknowledgement_rejects_conflicting_replay_disposition(self):
        intake = self.service.submit(self.request)
        dispatch_id = self._create_formal_task()
        self.codex_service.acknowledge(intake["intake_id"], dispatch_id)

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(
                intake["intake_id"], dispatch_id, disposition="BLOCKED"
            )

    def test_codex_acknowledgement_rejects_replay_with_a_different_dispatch(self):
        intake = self.service.submit(self.request)
        first_dispatch_id = self._create_formal_task()
        second_dispatch_id = self._create_formal_task("20260813-010")
        self.codex_service.acknowledge(intake["intake_id"], first_dispatch_id)

        with self.assertRaises(ContractError):
            self.codex_service.acknowledge(intake["intake_id"], second_dispatch_id)
        self.assertEqual(
            self.store.get_task_intake_handling(intake["intake_id"])["dispatch_id"],
            first_dispatch_id,
        )

    def test_codex_acknowledgement_rejects_invalid_dispatch_id_before_lookup(self):
        intake = self.service.submit(self.request)

        with self.assertRaisesRegex(ContractError, "handling dispatch ID is invalid"):
            self.codex_service.acknowledge(intake["intake_id"], "invalid dispatch")

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

    def test_acknowledgement_rejects_legacy_task_intake_schema_before_writing(self):
        intake = self.service.submit(self.request)
        dispatch_id = self._create_formal_task()
        with self.store.mutation() as connection:
            row = connection.execute(
                "SELECT * FROM task_intake_requests WHERE intake_id = ?",
                (intake["intake_id"],),
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
                "INSERT INTO task_intake_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row),
            )

        with self.assertRaises(SchemaMigrationRequiredError):
            self.codex_service.acknowledge(intake["intake_id"], dispatch_id)
        with self.store.read_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM task_intake_handlings").fetchone()[0],
                0,
            )

    def test_task_intake_readers_reject_legacy_schema_before_returning_rows(self):
        intake = self.service.submit(self.request)
        with self.store.mutation() as connection:
            row = connection.execute(
                "SELECT * FROM task_intake_requests WHERE intake_id = ?",
                (intake["intake_id"],),
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
                "INSERT INTO task_intake_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row),
            )

        for reader in (
            lambda: self.store.list_pending_task_intakes(limit=1),
            lambda: self.store.get_task_intake(intake["intake_id"]),
            lambda: self.store.list_task_intakes(limit=1),
            lambda: self.store.get_task_intake_handling(intake["intake_id"]),
        ):
            with self.subTest(reader=reader):
                with self.assertRaises(SchemaMigrationRequiredError):
                    reader()

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

    def test_initialize_rejects_task_intake_handling_schema_without_constraints(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
            connection.execute(
                """CREATE TABLE task_intake_handlings (
                       intake_id TEXT PRIMARY KEY REFERENCES task_intake_requests(intake_id),
                       dispatch_id TEXT NOT NULL REFERENCES tasks(dispatch_id),
                       disposition TEXT NOT NULL,
                       handled_at TEXT NOT NULL
                   )"""
            )
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()
        with self.store.read_connection() as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'task_intake_handlings'"
            ).fetchone()["sql"]
        self.assertNotIn("UNIQUE", schema.upper())

    def test_initialize_rejects_task_intake_handling_schema_with_lowercase_check_values(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
            connection.execute(
                """CREATE TABLE task_intake_handlings (
                       intake_id TEXT PRIMARY KEY REFERENCES task_intake_requests(intake_id),
                       dispatch_id TEXT NOT NULL UNIQUE REFERENCES tasks(dispatch_id),
                       disposition TEXT NOT NULL CHECK (disposition IN ('dispatched', 'blocked')),
                       handled_at TEXT NOT NULL
                   )"""
            )
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()

    def test_current_task_intake_schema_rejects_extra_index(self):
        with self.store.mutation() as connection:
            connection.execute(
                "CREATE INDEX task_intake_title_index ON task_intake_requests(title)"
            )

        with self.assertRaises(SchemaUnsupportedError):
            self.store.require_schema_compatible()
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()

    def test_current_task_intake_schema_rejects_extra_trigger(self):
        with self.store.mutation() as connection:
            connection.execute(
                """CREATE TRIGGER task_intake_result_rewrite
                   AFTER INSERT ON task_intake_requests
                   BEGIN
                       UPDATE task_intake_requests
                       SET result_code = 'REWRITTEN'
                       WHERE intake_id = NEW.intake_id;
                   END"""
            )

        with self.assertRaises(SchemaUnsupportedError):
            self.store.require_schema_compatible()
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()

    def test_current_task_intake_schema_rejects_cross_table_trigger(self):
        with self.store.mutation() as connection:
            connection.execute(
                """CREATE TRIGGER task_intake_cross_table_rewrite
                   AFTER INSERT ON tasks
                   BEGIN
                       UPDATE task_intake_requests
                       SET result_code = 'REWRITTEN'
                       WHERE status = 'PENDING';
                   END"""
            )

        with self.assertRaises(SchemaUnsupportedError):
            self.store.require_schema_compatible()
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()

    def test_current_task_intake_schema_rejects_unrelated_persistent_trigger(self):
        with self.store.mutation() as connection:
            connection.execute(
                "CREATE TRIGGER unrelated_trigger AFTER INSERT ON tasks BEGIN SELECT 1; END"
            )

        with self.assertRaises(SchemaUnsupportedError):
            self.store.require_schema_compatible()
        with self.assertRaises(SchemaUnsupportedError):
            self.store.initialize()

    def test_schema_preflight_rejects_temporary_trigger_on_active_connection(self):
        with self.store.mutation() as connection:
            connection.execute(
                "CREATE TEMP TRIGGER temporary_trigger AFTER INSERT ON tasks BEGIN SELECT 1; END"
            )
            with self.assertRaises(SchemaUnsupportedError):
                self.store._require_schema_compatible_in_connection(connection)

    def test_initialize_rejects_task_intake_migration_residue_without_removing_it(self):
        with self.store.mutation() as connection:
            connection.execute("CREATE TABLE task_intake_requests_migrated (value TEXT)")

        with self.assertRaises(ReconciliationError):
            self.store.initialize()
        with self.store.read_connection() as connection:
            self.assertIsNotNone(connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'task_intake_requests_migrated'"""
            ).fetchone())

    def _intake_status_counts(self):
        with self.store.read_connection() as connection:
            return {
                row["status"]: row["count"]
                for row in connection.execute(
                    """SELECT status, COUNT(*) AS count
                       FROM task_intake_requests GROUP BY status"""
                )
            }

    def _create_formal_task(self, dispatch_id="20260813-009"):
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
