import hashlib
import unittest

from team_control.contracts import INTENT_ACTIONS
from team_control.errors import ContractError
from team_control.intents import (
    normalize_intent_request,
    request_hash,
    validate_intent_request,
)


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


if __name__ == "__main__":
    unittest.main()
