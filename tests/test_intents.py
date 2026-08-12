import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_control.contracts import INTENT_ACTIONS
from team_control.errors import ContractError
from team_control.intents import (
    IntentService,
    normalize_intent_request,
    request_hash,
    validate_intent_request,
)
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class IntentRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "action": "PAUSE_REQUEST",
            "dispatch_id": "20260812-004",
            "target_sha": "a" * 40,
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
            "parameters": {},
        }

    def test_normalizes_a_pause_request(self):
        self.assertEqual(normalize_intent_request(self.request), self.request)

    def test_public_validation_entrypoint_accepts_a_resume_request(self):
        request = dict(self.request, action="RESUME_REQUEST")

        self.assertEqual(validate_intent_request(request), request)

    def test_action_whitelist_is_exact(self):
        self.assertEqual(
            INTENT_ACTIONS,
            frozenset({"PAUSE_REQUEST", "RESUME_REQUEST", "APPROVAL_REQUEST"}),
        )
        for action in ("PAUSE", "pause_request", "MERGE_REQUEST"):
            with self.subTest(action=action):
                request = dict(self.request, action=action)
                with self.assertRaises(ContractError):
                    normalize_intent_request(request)

    def test_rejects_noncanonical_identity_or_unknown_fields(self):
        cases = (
            {"idempotency_key": "not-a-uuid"},
            {"target_sha": "A" * 40},
            {"target_sha": "a" * 39},
            {"extra": "not in the contract"},
        )
        for update in cases:
            with self.subTest(update=update):
                with self.assertRaises(ContractError):
                    normalize_intent_request(dict(self.request, **update))

    def test_normalizes_a_standard_uuid_idempotency_key(self):
        normalized = normalize_intent_request(
            dict(
                self.request,
                idempotency_key="123E4567-E89B-12D3-A456-426614174000",
            )
        )
        self.assertEqual(
            normalized["idempotency_key"],
            "123e4567-e89b-12d3-a456-426614174000",
        )

    def test_pause_and_resume_require_empty_parameters(self):
        for action in ("PAUSE_REQUEST", "RESUME_REQUEST"):
            with self.subTest(action=action):
                request = dict(self.request, action=action, parameters={"reason": "later"})
                with self.assertRaises(ContractError):
                    normalize_intent_request(request)

    def test_rejects_non_json_parameters_and_non_ascii_keys(self):
        for parameters in (
            [],
            {"reason": float("nan")},
            {"reason": ("not", "a", "JSON", "array")},
        ):
            with self.subTest(parameters=parameters):
                with self.assertRaises(ContractError):
                    normalize_intent_request(dict(self.request, parameters=parameters))

        for requested_parameters in (
            {"原由": "non-ASCII key"},
            {"nested": [{"原由": "non-ASCII key"}]},
        ):
            approval_request = dict(
                self.request,
                action="APPROVAL_REQUEST",
                parameters={
                    "requested_action": "merge",
                    "requested_parameters": requested_parameters,
                    "confirmation": "yes",
                },
            )
            with self.subTest(requested_parameters=requested_parameters):
                with self.assertRaises(ContractError):
                    normalize_intent_request(approval_request)

    def test_approval_parameters_reject_integers_outside_js_safe_range(self):
        request = dict(
            self.request,
            action="APPROVAL_REQUEST",
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"items": [9007199254740992]},
                "confirmation": "yes",
            },
        )

        with self.assertRaises(ContractError):
            normalize_intent_request(request)

        for value in (-9007199254740991, 9007199254740991):
            with self.subTest(value=value):
                accepted = dict(
                    request,
                    parameters={
                        "requested_action": "merge",
                        "requested_parameters": {"items": [value]},
                        "confirmation": "yes",
                    },
                )
                self.assertEqual(
                    normalize_intent_request(accepted)["parameters"]
                    ["requested_parameters"]["items"],
                    [value],
                )

        negative_overflow = dict(
            request,
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"items": [-9007199254740992]},
                "confirmation": "yes",
            },
        )
        with self.assertRaises(ContractError):
            normalize_intent_request(negative_overflow)

    def test_normalizes_approval_without_retaining_confirmation(self):
        confirmation = "确认此项受限操作"
        request = dict(
            self.request,
            action="APPROVAL_REQUEST",
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"branch": "main"},
                "confirmation": confirmation,
            },
        )

        normalized = normalize_intent_request(request)

        self.assertEqual(
            normalized["parameters"],
            {
                "requested_action": "merge",
                "requested_parameters": {"branch": "main"},
                "confirmation_hash": hashlib.sha256(
                    b"team-control/intent-confirmation/v1\n"
                    + confirmation.encode("utf-8")
                ).hexdigest(),
            },
        )
        self.assertNotIn("confirmation", normalized["parameters"])
        self.assertNotIn(confirmation, repr(normalized))

    def test_approval_accepts_only_the_required_parameters(self):
        valid = {
            "requested_action": "merge",
            "requested_parameters": {},
            "confirmation": "yes",
        }
        cases = (
            {},
            dict(valid, extra=True),
            dict(valid, confirmation=""),
            dict(valid, confirmation="x" * 257),
            dict(valid, requested_parameters=[]),
            dict(valid, requested_parameters={"原由": "no"}),
        )
        for parameters in cases:
            with self.subTest(parameters=parameters):
                with self.assertRaises(ContractError):
                    normalize_intent_request(
                        dict(self.request, action="APPROVAL_REQUEST", parameters=parameters)
                    )

    def test_request_hash_is_domain_separated_canonical_json(self):
        expected_json = (
            b'{"action":"PAUSE_REQUEST","dispatch_id":"20260812-004",'
            b'"idempotency_key":"123e4567-e89b-12d3-a456-426614174000",'
            b'"parameters":{},"target_sha":"' + b"a" * 40 + b'"}'
        )
        self.assertEqual(
            request_hash(self.request),
            hashlib.sha256(b"team-control/intent-request/v1\n" + expected_json).hexdigest(),
        )

    def test_approval_request_hash_never_contains_raw_confirmation(self):
        request = dict(
            self.request,
            action="APPROVAL_REQUEST",
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"target": "main"},
                "confirmation": "only-this-secret-text",
            },
        )

        normalized = validate_intent_request(request)
        self.assertNotIn("confirmation", normalized["parameters"])
        self.assertNotIn("only-this-secret-text", repr(normalized))
        self.assertEqual(request_hash(request), request_hash(dict(request)))

    def test_approval_hash_preserves_unicode_value_as_utf8_canonical_json(self):
        request = dict(
            self.request,
            action="APPROVAL_REQUEST",
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"note": "中文"},
                "confirmation": "yes",
            },
        )
        normalized = validate_intent_request(request)
        expected_json = (
            '{"action":"APPROVAL_REQUEST","dispatch_id":"20260812-004",'
            '"idempotency_key":"123e4567-e89b-12d3-a456-426614174000",'
            '"parameters":{"confirmation_hash":"%s","requested_action":"merge",'
            '"requested_parameters":{"note":"中文"}},"target_sha":"%s"}'
            % (normalized["parameters"]["confirmation_hash"], "a" * 40)
        )
        self.assertEqual(
            request_hash(request),
            hashlib.sha256(
                b"team-control/intent-request/v1\n"
                + expected_json.encode("utf-8")
            ).hexdigest(),
        )

    def test_rejects_lone_surrogates_as_contract_errors(self):
        for field, parameters in (
            (
                "requested_action",
                {
                    "requested_action": "\ud800",
                    "requested_parameters": {},
                    "confirmation": "yes",
                },
            ),
            (
                "confirmation",
                {
                    "requested_action": "merge",
                    "requested_parameters": {},
                    "confirmation": "\ud800",
                },
            ),
            (
                "requested_parameters",
                {
                    "requested_action": "merge",
                    "requested_parameters": {"note": "\ud800"},
                    "confirmation": "yes",
                },
            ),
        ):
            request = dict(
                self.request,
                action="APPROVAL_REQUEST",
                parameters=parameters,
            )
            with self.subTest(field=field):
                with self.assertRaises(ContractError):
                    validate_intent_request(request)

    def test_rejects_excessively_nested_approval_parameters(self):
        nested = "leaf"
        for _ in range(33):
            nested = [nested]
        request = dict(
            self.request,
            action="APPROVAL_REQUEST",
            parameters={
                "requested_action": "merge",
                "requested_parameters": {"payload": nested},
                "confirmation": "yes",
            },
        )

        with self.assertRaises(ContractError):
            validate_intent_request(request)


class IntentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.control = ControlPlane(self.context, self.store)
        self.head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.task = self.control.create_task(
            "20260812-004", "Intent adapter", "Process bounded intents", "L2"
        )
        self.control.transition("20260812-004", "DISPATCHED", "start")
        self.control.transition("20260812-004", "IN_PROGRESS", "start")
        self.service = IntentService(self.context, self.store, self.control)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, action, parameters=None):
        request = {
            "dispatch_id": "20260812-004",
            "action": action,
            "target_sha": self.head,
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
            "parameters": parameters or {},
        }
        return self.service.submit(request)

    def test_process_pause_revalidates_head_then_requests_pause(self):
        intent = self.submit("PAUSE_REQUEST")

        result = self.service.process(intent["intent_id"])

        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["result_code"], "PAUSE_REQUESTED")
        self.assertEqual(self.store.get_task("20260812-004")["state"], "PAUSE_REQUESTED")
        self.assertEqual(self.service.process(intent["intent_id"]), result)

    def test_process_rejects_noncanonical_intent_identifiers(self):
        intent = self.submit("PAUSE_REQUEST")

        with self.assertRaises(ContractError):
            self.service.process(intent["intent_id"].replace("-", ""))

    def test_process_rejects_stale_head_before_transition(self):
        intent = self.submit("PAUSE_REQUEST")
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET current_head_sha = ? WHERE dispatch_id = ?",
                ("b" * 40, "20260812-004"),
            )

        result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), ("REJECTED", "STALE_HEAD"))
        self.assertEqual(self.store.get_task("20260812-004")["state"], "IN_PROGRESS")

    def test_approval_request_only_records_preparation(self):
        intent = self.submit(
            "APPROVAL_REQUEST",
            {
                "requested_action": "merge",
                "requested_parameters": {"branch": "main"},
                "confirmation": "yes",
            },
        )

        result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), (
            "APPLIED", "APPROVAL_PREPARATION_REQUESTED",
        ))
        self.assertEqual(self.store.list_approvals("20260812-004"), [])
        self.assertEqual(
            self.store.list_events("20260812-004")[-1]["event_type"],
            "APPROVAL_PREPARATION_REQUESTED",
        )

    def test_approval_request_rejects_a_closed_task(self):
        intent = self.submit(
            "APPROVAL_REQUEST",
            {
                "requested_action": "merge",
                "requested_parameters": {},
                "confirmation": "yes",
            },
        )
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET state = 'CLOSED' WHERE dispatch_id = ?",
                ("20260812-004",),
            )

        result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), (
            "REJECTED", "STATE_CONFLICT",
        ))

    def test_process_blocks_when_an_operation_is_prepared(self):
        intent = self.submit("PAUSE_REQUEST")
        self.store.prepare_operation(
            "20260812-004", "merge", "a" * 64, self.head,
            "123e4567-e89b-12d3-a456-426614174001",
        )

        result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), (
            "BLOCKED", "PREPARED_OPERATION",
        ))
        self.assertEqual(self.store.get_task("20260812-004")["state"], "IN_PROGRESS")

    def test_resume_blocks_when_a_pending_approval_exists(self):
        self.control.transition("20260812-004", "PAUSE_REQUESTED", "pause")
        self.control.transition("20260812-004", "PAUSED", "checkpoint")
        intent = self.submit("RESUME_REQUEST")
        self.store.create_approval(
            "20260812-004", "merge", self.head, "a" * 64,
            "safe-approval-nonce-0001", 30,
            "approval-idempotency-0001",
        )

        result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), (
            "BLOCKED", "PENDING_APPROVAL",
        ))
        self.assertEqual(self.store.get_task("20260812-004")["state"], "PAUSED")

    def test_resume_uses_the_fresh_resume_state_held_in_the_transition_transaction(self):
        self.control.transition("20260812-004", "PAUSE_REQUESTED", "pause")
        self.control.transition("20260812-004", "PAUSED", "checkpoint")
        intent = self.submit("RESUME_REQUEST")
        stale_task = self.store.get_task("20260812-004")
        with self.store.mutation() as connection:
            connection.execute(
                "UPDATE tasks SET resume_state = 'REVIEWING' WHERE dispatch_id = ?",
                ("20260812-004",),
            )
        with mock.patch.object(self.store, "get_task", return_value=stale_task):
            result = self.service.process(intent["intent_id"])

        self.assertEqual((result["status"], result["result_code"]), (
            "APPLIED", "REVIEWING",
        ))
        self.assertEqual(self.store.get_task("20260812-004")["state"], "REVIEWING")

if __name__ == "__main__":
    unittest.main()
