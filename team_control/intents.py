import hashlib
import json
import math
import uuid

from .contracts import (
    DISPATCH_RE,
    INTENT_ACTIONS,
    INTENT_REQUEST_FIELDS,
    SHA_RE,
    UUID_RE,
)
from .errors import ContractError


CONFIRMATION_HASH_DOMAIN = b"team-control/intent-confirmation/v1\n"
REQUEST_HASH_DOMAIN = b"team-control/intent-request/v1\n"
APPROVAL_PARAMETER_FIELDS = frozenset({
    "requested_action", "requested_parameters", "confirmation",
})
JS_SAFE_INTEGER_MAX = 9007199254740991
MAX_JSON_DEPTH = 32


def _utf8_text(value, label):
    if type(value) is not str:
        raise ContractError("%s must be a string" % label)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContractError("%s must be valid UTF-8 text" % label) from error
    return value


def _normalize_json_value(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ContractError("JSON value exceeds the maximum nesting depth")
    if type(value) is dict:
        normalized = {}
        for key, nested_value in value.items():
            if type(key) is not str or not key.isascii():
                raise ContractError("JSON object keys must be ASCII strings")
            normalized[key] = _normalize_json_value(nested_value, depth + 1)
        return normalized
    if type(value) is list:
        return [_normalize_json_value(item, depth + 1) for item in value]
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return _utf8_text(value, "JSON string")
    if type(value) is int:
        if abs(value) > JS_SAFE_INTEGER_MAX:
            raise ContractError("intent integers must be within the JS safe range")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractError("value must have strict JSON semantics")


def _require_exact_fields(value, expected, label):
    if type(value) is not dict:
        raise ContractError("%s must be an object" % label)
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing or unknown:
        raise ContractError("%s fields do not match the contract" % label)


def _normalize_approval_parameters(parameters):
    _require_exact_fields(parameters, APPROVAL_PARAMETER_FIELDS, "approval parameters")
    requested_action = parameters["requested_action"]
    confirmation = parameters["confirmation"]
    if type(requested_action) is not str or not requested_action:
        raise ContractError("requested_action must be a non-empty string")
    requested_action = _utf8_text(requested_action, "requested_action")
    if (
        type(confirmation) is not str
        or not 1 <= len(confirmation) <= 256
    ):
        raise ContractError("confirmation must contain 1 to 256 characters")
    confirmation = _utf8_text(confirmation, "confirmation")
    requested_parameters = parameters["requested_parameters"]
    if type(requested_parameters) is not dict:
        raise ContractError("requested_parameters must be an object")

    return {
        "requested_action": requested_action,
        "requested_parameters": _normalize_json_value(requested_parameters),
        "confirmation_hash": hashlib.sha256(
            CONFIRMATION_HASH_DOMAIN + confirmation.encode("utf-8")
        ).hexdigest(),
    }


def normalize_intent_request(request):
    _require_exact_fields(request, INTENT_REQUEST_FIELDS, "intent request")
    action = request["action"]
    dispatch_id = request["dispatch_id"]
    target_sha = request["target_sha"]
    idempotency_key = request["idempotency_key"]
    if type(action) is not str or action not in INTENT_ACTIONS:
        raise ContractError("action must be an allowed intent action")
    if type(dispatch_id) is not str or not DISPATCH_RE.fullmatch(dispatch_id):
        raise ContractError("dispatch_id must be a valid dispatch identifier")
    if type(target_sha) is not str or not SHA_RE.fullmatch(target_sha):
        raise ContractError("target_sha must be a full lowercase Git SHA")
    if type(idempotency_key) is not str or not UUID_RE.fullmatch(idempotency_key):
        raise ContractError("idempotency_key must be a UUID")
    idempotency_key = str(uuid.UUID(idempotency_key))

    parameters = request["parameters"]
    if action in {"PAUSE_REQUEST", "RESUME_REQUEST"}:
        if type(parameters) is not dict or parameters:
            raise ContractError("pause and resume parameters must be empty objects")
        normalized_parameters = {}
    else:
        normalized_parameters = _normalize_approval_parameters(parameters)

    return {
        "action": action,
        "dispatch_id": dispatch_id,
        "target_sha": target_sha,
        "idempotency_key": idempotency_key,
        "parameters": normalized_parameters,
    }


def validate_intent_request(request):
    return normalize_intent_request(request)


def request_hash(request):
    normalized = validate_intent_request(request)
    request_json = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(
        REQUEST_HASH_DOMAIN + request_json.encode("utf-8")
    ).hexdigest()
