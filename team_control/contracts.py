import re
from collections.abc import Mapping

from .errors import ContractError


TASK_STATES = frozenset({
    "PLANNED", "NEEDS_CLARIFICATION", "DISPATCHED", "IN_PROGRESS",
    "PAUSE_REQUESTED", "PAUSED", "BLOCKED", "NEEDS_DIRECTION",
    "NEEDS_HUMAN_APPROVAL", "REVIEWING", "ACCEPTED", "INTEGRATED",
    "RELEASED", "CLOSED", "UNKNOWN",
})
RISK_LEVELS = frozenset({"L1", "L2", "L3"})
EVIDENCE_KINDS = frozenset({"commit", "diff", "test", "review", "approval", "artifact"})
AGENT_STATES = frozenset({"IN_PROGRESS", "COMPLETED", "BLOCKED", "NEEDS_DIRECTION"})
REVIEW_DISPOSITIONS = frozenset({"ACCEPT", "MODIFY", "BLOCK", "ESCALATE"})
BLOCKER_STATUSES = frozenset({"OPEN", "RESOLVED"})

DISPATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED = {
    "task": ("schema_version", "dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("schema_version", "dispatch_id", "sequence", "event_type", "created_at"),
    "approval": ("schema_version", "approval_id", "dispatch_id", "action", "target_sha", "request_hash", "nonce_hash", "expires_at", "consumed_at", "idempotency_key"),
    "evidence": ("schema_version", "evidence_id", "dispatch_id", "kind", "path", "sha256", "created_at"),
    "agent_status": ("schema_version", "dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("schema_version", "review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "created_at"),
    "blocker": ("schema_version", "blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}

STRING_FIELDS = {
    "task": ("dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("dispatch_id", "event_type", "created_at"),
    "approval": ("approval_id", "dispatch_id", "action", "target_sha", "request_hash", "nonce_hash", "expires_at", "idempotency_key"),
    "evidence": ("evidence_id", "dispatch_id", "kind", "path", "sha256", "created_at"),
    "agent_status": ("dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "created_at"),
    "blocker": ("blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}

NULLABLE_STRING_FIELDS = {
    "approval": ("consumed_at",),
    "evidence": ("source_sha",),
    "agent_status": ("model",),
    "review": ("report_path",),
    "blocker": ("resolution_condition",),
}

NON_EMPTY_FIELDS = {
    "task": ("title", "objective"),
    "event": ("event_type", "created_at"),
    "approval": ("expires_at", "idempotency_key"),
    "evidence": ("path", "created_at"),
    "agent_status": ("updated_at",),
    "review": ("created_at",),
    "blocker": ("reason", "owner", "created_at"),
}


def _validate_string(record, field, nullable=False):
    value = record[field]
    if nullable and value is None:
        return
    if not isinstance(value, str):
        raise ContractError("%s must be a string" % field)


def _validate_pattern(record, field, pattern, description):
    if not pattern.fullmatch(record[field]):
        raise ContractError("%s must be %s" % (field, description))


def _validate_integer(record, field, minimum, maximum=None):
    value = record[field]
    if type(value) is not int:
        raise ContractError("%s must be an integer" % field)
    if value < minimum or (maximum is not None and value > maximum):
        raise ContractError("%s is outside the allowed range" % field)


def validate_record(kind, record):
    if not isinstance(kind, str) or kind not in REQUIRED:
        raise ContractError("unknown contract kind: %s" % kind)
    if not isinstance(record, Mapping):
        raise ContractError("%s record must be a mapping" % kind)

    missing = [key for key in REQUIRED[kind] if key not in record]
    if missing:
        raise ContractError("missing %s fields: %s" % (kind, ", ".join(missing)))
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ContractError("schema_version must be 1")

    for field in STRING_FIELDS[kind]:
        _validate_string(record, field)
    for field in NULLABLE_STRING_FIELDS.get(kind, ()):
        if field in record:
            _validate_string(record, field, nullable=True)
    for field in NON_EMPTY_FIELDS[kind]:
        if not record[field]:
            raise ContractError("%s must not be empty" % field)

    if kind == "task":
        _validate_pattern(record, "dispatch_id", DISPATCH_RE, "a valid dispatch identifier")
        if record["risk_level"] not in RISK_LEVELS:
            raise ContractError("unknown risk level: %s" % record["risk_level"])
        if record["state"] not in TASK_STATES:
            raise ContractError("unknown task state: %s" % record["state"])
        _validate_pattern(record, "task_base_sha", SHA_RE, "a full hexadecimal SHA")
        if record["owner"] != "Codex":
            raise ContractError("task owner must be Codex")
    elif kind == "event":
        _validate_integer(record, "sequence", 1)
    elif kind == "approval":
        _validate_pattern(record, "target_sha", SHA_RE, "a full hexadecimal SHA")
        _validate_pattern(record, "request_hash", HASH_RE, "a 64-character hexadecimal hash")
        _validate_pattern(record, "nonce_hash", HASH_RE, "a 64-character hexadecimal hash")
        if record["consumed_at"] == "":
            raise ContractError("consumed_at must be null or a non-empty string")
    elif kind == "evidence":
        if record["kind"] not in EVIDENCE_KINDS:
            raise ContractError("unknown evidence kind: %s" % record["kind"])
        _validate_pattern(record, "sha256", HASH_RE, "a 64-character hexadecimal hash")
    elif kind == "agent_status":
        if record["state"] not in AGENT_STATES:
            raise ContractError("unknown agent state: %s" % record["state"])
        if "progress" in record:
            _validate_integer(record, "progress", 0, 100)
    elif kind == "review":
        if record["disposition"] not in REVIEW_DISPOSITIONS:
            raise ContractError("unknown review disposition: %s" % record["disposition"])
        _validate_pattern(record, "source_sha", SHA_RE, "a full hexadecimal SHA")
    elif kind == "blocker":
        if record["status"] not in BLOCKER_STATUSES:
            raise ContractError("unknown blocker status: %s" % record["status"])
    return record
