import json
import unittest
from pathlib import Path

from team_control.contracts import TASK_STATES, validate_record
from team_control.errors import ContractError


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
CREATED_AT = "2026-08-08T12:00:00Z"

VALID_RECORDS = {
    "task": {
        "schema_version": 1,
        "dispatch_id": "20260808-003",
        "title": "Example",
        "objective": "Prove contract",
        "risk_level": "L1",
        "state": "PLANNED",
        "task_base_sha": "a" * 40,
        "owner": "Codex",
    },
    "event": {
        "schema_version": 1,
        "dispatch_id": "20260808-003",
        "sequence": 1,
        "event_type": "DISPATCHED",
        "created_at": CREATED_AT,
    },
    "approval": {
        "schema_version": 1,
        "approval_id": "approval-1",
        "dispatch_id": "20260808-003",
        "action": "merge",
        "target_sha": "b" * 40,
        "request_hash": "c" * 64,
        "expires_at": CREATED_AT,
        "consumed_at": None,
        "status": "PENDING",
        "idempotency_key": "approval-1:merge",
    },
    "evidence": {
        "schema_version": 1,
        "evidence_id": "evidence-1",
        "dispatch_id": "20260808-003",
        "kind": "test",
        "path": "artifacts/test.txt",
        "sha256": "e" * 64,
        "source_sha": "d" * 40,
        "created_at": CREATED_AT,
    },
    "agent_status": {
        "schema_version": 1,
        "dispatch_id": "20260808-003",
        "agent_id": "codex-1",
        "role": "implementer",
        "model": None,
        "state": "IN_PROGRESS",
        "progress": 50,
        "updated_at": CREATED_AT,
    },
    "review": {
        "schema_version": 1,
        "review_id": "review-1",
        "dispatch_id": "20260808-003",
        "reviewer": "reviewer-1",
        "disposition": "ACCEPT",
        "source_sha": "f" * 40,
        "report_path": "artifacts/review.md",
        "report_sha256": "a" * 64,
        "created_at": CREATED_AT,
    },
    "blocker": {
        "schema_version": 1,
        "blocker_id": "blocker-1",
        "dispatch_id": "20260808-003",
        "reason": "Waiting for approval",
        "owner": "Codex",
        "status": "OPEN",
        "resolution_condition": None,
        "created_at": CREATED_AT,
        "resolved_at": None,
    },
}

STRING_FIELDS = {
    "task": ("dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("dispatch_id", "event_type", "created_at"),
    "approval": ("approval_id", "dispatch_id", "action", "target_sha", "request_hash", "expires_at", "idempotency_key"),
    "evidence": ("evidence_id", "dispatch_id", "kind", "path", "sha256", "source_sha", "created_at"),
    "agent_status": ("dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "report_path", "report_sha256", "created_at"),
    "blocker": ("blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}

NULLABLE_STRING_FIELDS = {
    "approval": ("consumed_at",),
    "agent_status": ("model",),
    "blocker": ("resolution_condition", "resolved_at"),
}

DATE_TIME_FIELDS = {
    "event": ("created_at",),
    "approval": ("expires_at", "consumed_at"),
    "evidence": ("created_at",),
    "agent_status": ("updated_at",),
    "review": ("created_at",),
    "blocker": ("created_at", "resolved_at"),
}


def changed(contract_kind, **updates):
    record = dict(VALID_RECORDS[contract_kind])
    record.update(updates)
    return record


def without(contract_kind, field):
    record = dict(VALID_RECORDS[contract_kind])
    del record[field]
    return record


class ContractTests(unittest.TestCase):
    def test_state_vocabulary_contains_pause_and_fail_closed_states(self):
        self.assertIn("PAUSE_REQUESTED", TASK_STATES)
        self.assertIn("PAUSED", TASK_STATES)
        self.assertIn("UNKNOWN", TASK_STATES)

    def test_task_requires_dispatch_and_git_identity(self):
        with self.assertRaises(ContractError):
            validate_record("task", {"dispatch_id": "20260808-003"})

    def test_all_contract_kinds_accept_valid_records(self):
        for kind, record in VALID_RECORDS.items():
            with self.subTest(kind=kind):
                self.assertIs(validate_record(kind, record), record)

    def test_rfc3339_dates_with_offsets_are_accepted(self):
        for kind, fields in DATE_TIME_FIELDS.items():
            for field in fields:
                updates = {field: "2026-08-08T12:00:00+08:00"}
                if kind == "approval" and field == "consumed_at":
                    updates["status"] = "CONSUMED"
                record = changed(kind, **updates)
                with self.subTest(kind=kind, field=field):
                    self.assertIs(validate_record(kind, record), record)

    def test_rfc3339_leap_seconds_are_accepted(self):
        leap_seconds = (
            "1990-12-31T23:59:60Z",
            "1990-12-31T23:59:60+08:00",
        )
        for kind, fields in DATE_TIME_FIELDS.items():
            for field in fields:
                for value in leap_seconds:
                    updates = {field: value}
                    if kind == "approval" and field == "consumed_at":
                        updates["status"] = "CONSUMED"
                    record = changed(kind, **updates)
                    with self.subTest(kind=kind, field=field, value=value):
                        try:
                            result = validate_record(kind, record)
                        except ContractError as error:
                            self.fail("valid RFC3339 leap second was rejected: %s" % error)
                        self.assertIs(result, record)

    def test_invalid_records_raise_only_contract_error(self):
        cases = [
            ("kind type", [], {}),
            ("record type", "task", None),
            ("schema version type", "event", changed("event", schema_version=True)),
            ("task dispatch pattern", "task", changed("task", dispatch_id="bad dispatch")),
            ("task empty title", "task", changed("task", title="")),
            ("task empty objective", "task", changed("task", objective="")),
            ("task risk enum", "task", changed("task", risk_level="L4")),
            ("task state enum", "task", changed("task", state="RUNNING")),
            ("task SHA", "task", changed("task", task_base_sha="xyz")),
            ("task owner", "task", changed("task", owner="Other")),
            ("event sequence type", "event", changed("event", sequence="1")),
            ("event sequence bool", "event", changed("event", sequence=True)),
            ("event sequence range", "event", changed("event", sequence=0)),
            ("event empty type", "event", changed("event", event_type="")),
            ("event empty timestamp", "event", changed("event", created_at="")),
            ("event invalid timezone offset", "event", changed("event", created_at="2026-08-08T12:00:00+08:99")),
            ("event second 61", "event", changed("event", created_at="1990-12-31T23:59:61Z")),
            ("event invalid leap date", "event", changed("event", created_at="1990-02-30T23:59:60Z")),
            ("event naive leap second", "event", changed("event", created_at="1990-12-31T23:59:60")),
            ("event leap second invalid offset", "event", changed("event", created_at="1990-12-31T23:59:60+08:99")),
            ("approval target SHA", "approval", changed("approval", target_sha="g" * 40)),
            ("approval request hash", "approval", changed("approval", request_hash="c" * 63)),
            ("approval nonce hash", "approval", changed("approval", nonce_hash="d" * 63)),
            ("approval empty expiry", "approval", changed("approval", expires_at="")),
            ("approval empty consumed time", "approval", changed("approval", consumed_at="")),
            ("approval status enum", "approval", changed("approval", status="EXPIRED")),
            (
                "approval pending with consumed time",
                "approval",
                changed("approval", status="PENDING", consumed_at=CREATED_AT),
            ),
            (
                "approval consumed without consumed time",
                "approval",
                changed("approval", status="CONSUMED", consumed_at=None),
            ),
            ("approval empty idempotency key", "approval", changed("approval", idempotency_key="")),
            ("approval missing consumed time", "approval", without("approval", "consumed_at")),
            ("approval missing status", "approval", without("approval", "status")),
            ("approval missing idempotency key", "approval", without("approval", "idempotency_key")),
            ("evidence kind enum", "evidence", changed("evidence", kind="log")),
            ("evidence empty path", "evidence", changed("evidence", path="")),
            ("evidence hash", "evidence", changed("evidence", sha256="E" * 64)),
            ("evidence source SHA", "evidence", changed("evidence", source_sha="d" * 39)),
            ("evidence empty timestamp", "evidence", changed("evidence", created_at="")),
            ("agent state enum", "agent_status", changed("agent_status", state="PAUSED")),
            ("agent progress below range", "agent_status", changed("agent_status", progress=-1)),
            ("agent progress above range", "agent_status", changed("agent_status", progress=101)),
            ("agent progress bool", "agent_status", changed("agent_status", progress=True)),
            ("review disposition enum", "review", changed("review", disposition="PASS")),
            ("review source SHA", "review", changed("review", source_sha="f" * 39)),
            ("review report hash", "review", changed("review", report_sha256="a" * 63)),
            ("blocker empty reason", "blocker", changed("blocker", reason="")),
            ("blocker empty owner", "blocker", changed("blocker", owner="")),
            ("blocker status enum", "blocker", changed("blocker", status="CLOSED")),
        ]
        for kind, fields in STRING_FIELDS.items():
            for field in fields:
                cases.append(("%s %s type" % (kind, field), kind, changed(kind, **{field: 7})))
        for kind, fields in NULLABLE_STRING_FIELDS.items():
            for field in fields:
                cases.append(("%s %s type" % (kind, field), kind, changed(kind, **{field: 7})))
        for kind, fields in DATE_TIME_FIELDS.items():
            for field in fields:
                cases.append(("%s %s invalid date" % (kind, field), kind, changed(kind, **{field: "not-a-date"})))
                cases.append(("%s %s naive date" % (kind, field), kind, changed(kind, **{field: "2026-08-08T12:00:00"})))

        for label, kind, record in cases:
            with self.subTest(label=label):
                try:
                    validate_record(kind, record)
                except Exception as error:
                    self.assertIsInstance(error, ContractError)
                else:
                    self.fail("invalid record was accepted: %s" % label)

    def test_schema_documents_are_valid_json(self):
        expected = {
            "agent-status.schema.json",
            "approval.schema.json",
            "blocker.schema.json",
            "event.schema.json",
            "evidence.schema.json",
            "review.schema.json",
            "task.schema.json",
        }
        loaded = {}
        for schema_path in SCHEMA_DIR.glob("*.schema.json"):
            with schema_path.open() as schema_file:
                loaded[schema_path.name] = json.load(schema_file)
        self.assertEqual(set(loaded), expected)

        approval = loaded["approval.schema.json"]
        self.assertTrue(
            {"consumed_at", "status", "idempotency_key"}.issubset(
                approval["required"]
            )
        )
        self.assertNotIn("nonce_hash", approval["required"])
        self.assertEqual(
            approval["properties"]["nonce_hash"]["pattern"], "^[0-9a-f]{64}$"
        )
        self.assertEqual(approval["properties"]["idempotency_key"]["minLength"], 1)
        self.assertEqual(
            approval["properties"]["status"]["enum"],
            ["PENDING", "CONSUMED"],
        )

        schema_constraints = {
            "evidence path minLength": (
                loaded["evidence.schema.json"]["properties"]["path"],
                "minLength",
                1,
            ),
            "blocker reason minLength": (
                loaded["blocker.schema.json"]["properties"]["reason"],
                "minLength",
                1,
            ),
            "blocker owner minLength": (
                loaded["blocker.schema.json"]["properties"]["owner"],
                "minLength",
                1,
            ),
            "review source SHA pattern": (
                loaded["review.schema.json"]["properties"]["source_sha"],
                "pattern",
                "^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
            ),
            "evidence source SHA pattern": (
                loaded["evidence.schema.json"]["properties"]["source_sha"],
                "pattern",
                "^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
            ),
            "task base SHA pattern": (
                loaded["task.schema.json"]["properties"]["task_base_sha"],
                "pattern",
                "^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
            ),
        }
        for label, (schema_property, keyword, expected_value) in schema_constraints.items():
            with self.subTest(label=label):
                self.assertEqual(schema_property.get(keyword), expected_value)

    def test_git_sha_contracts_accept_sha1_and_sha256_lengths(self):
        for length in (40, 64):
            with self.subTest(length=length, kind="task"):
                validate_record("task", changed("task", task_base_sha="a" * length))
            with self.subTest(length=length, kind="review"):
                validate_record("review", changed("review", source_sha="b" * length))
            with self.subTest(length=length, kind="evidence"):
                validate_record("evidence", changed("evidence", source_sha="c" * length))
        for length in (41, 63):
            for kind, field in (
                ("task", "task_base_sha"),
                ("review", "source_sha"),
                ("evidence", "source_sha"),
            ):
                with self.subTest(length=length, kind=kind):
                    with self.assertRaises(ContractError):
                        validate_record(kind, changed(kind, **{field: "a" * length}))
