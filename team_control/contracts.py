import re
from collections.abc import Mapping
from datetime import datetime

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
APPROVAL_STATUSES = frozenset({"PENDING", "CONSUMED"})
INTENT_ACTIONS = frozenset({
    "PAUSE_REQUEST", "RESUME_REQUEST", "APPROVAL_REQUEST",
})
INTENT_REQUEST_FIELDS = frozenset({
    "action", "dispatch_id", "target_sha", "idempotency_key", "parameters",
})

DISPATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
COMMIT_SHA_RE = SHA_RE
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
RFC3339_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])[Tt]"
    r"(?:[01]\d|2[0-3]):[0-5]\d:(?P<second>[0-5]\d|60)(?:\.\d+)?"
    r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)

REQUIRED = {
    "task": ("schema_version", "dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("schema_version", "dispatch_id", "sequence", "event_type", "created_at"),
    "approval": ("schema_version", "approval_id", "dispatch_id", "action", "target_sha", "request_hash", "expires_at", "consumed_at", "status", "idempotency_key"),
    "evidence": ("schema_version", "evidence_id", "dispatch_id", "kind", "path", "sha256", "source_sha", "created_at"),
    "agent_status": ("schema_version", "dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("schema_version", "review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "report_path", "report_sha256", "created_at"),
    "blocker": ("schema_version", "blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}

STRING_FIELDS = {
    "task": ("dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("dispatch_id", "event_type", "created_at"),
    "approval": ("approval_id", "dispatch_id", "action", "target_sha", "request_hash", "expires_at", "status", "idempotency_key"),
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

NON_EMPTY_FIELDS = {
    "task": ("title", "objective"),
    "event": ("event_type", "created_at"),
    "approval": ("expires_at", "idempotency_key"),
    "evidence": ("path", "created_at"),
    "agent_status": ("updated_at",),
    "review": ("report_path", "created_at"),
    "blocker": ("reason", "owner", "created_at"),
}

DATE_TIME_FIELDS = {
    "event": ("created_at",),
    "approval": ("expires_at", "consumed_at"),
    "evidence": ("created_at",),
    "agent_status": ("updated_at",),
    "review": ("created_at",),
    "blocker": ("created_at", "resolved_at"),
}

AGENT_STATUS_FIELDS = frozenset({
    "schema_version", "dispatch_id", "agent_id", "role", "model", "state",
    "progress", "updated_at", "current_subtask", "completed", "findings",
    "risks", "recommendation",
})
AGENT_LIST_FIELDS = ("completed", "findings", "risks")
MAX_AGENT_LIST_ITEMS = 20
MAX_AGENT_TOTAL_ITEMS = 40
MAX_AGENT_ITEM_LENGTH = 256


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


def _validate_datetime(record, field, nullable=False):
    value = record[field]
    if nullable and value is None:
        return
    match = RFC3339_RE.fullmatch(value)
    if match is None:
        raise ContractError("%s must be an RFC3339 date-time with a timezone" % field)

    normalized = value
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    if normalized[10] == "t":
        normalized = normalized[:10] + "T" + normalized[11:]
    if match.group("second") == "60":
        normalized = normalized[:17] + "59" + normalized[19:]
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ContractError("%s must be a valid RFC3339 date-time" % field)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("%s must include a timezone" % field)


def _validate_agent_list(record, field):
    value = record[field]
    if type(value) is not list:
        raise ContractError("%s must be an array" % field)
    if len(value) > MAX_AGENT_LIST_ITEMS:
        raise ContractError("%s has too many items" % field)
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > MAX_AGENT_ITEM_LENGTH
        ):
            raise ContractError(
                "%s items must contain 1 to %d characters"
                % (field, MAX_AGENT_ITEM_LENGTH)
            )


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
    for field in DATE_TIME_FIELDS.get(kind, ()):
        if field not in record:
            continue
        nullable = field in NULLABLE_STRING_FIELDS.get(kind, ())
        _validate_datetime(record, field, nullable=nullable)

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
        if record["status"] not in APPROVAL_STATUSES:
            raise ContractError("unknown approval status: %s" % record["status"])
        if record["status"] == "PENDING" and record["consumed_at"] is not None:
            raise ContractError("pending approval must not have a consumed_at value")
        if record["status"] == "CONSUMED" and record["consumed_at"] is None:
            raise ContractError("consumed approval must have a consumed_at value")
        if "nonce_hash" in record:
            _validate_string(record, "nonce_hash")
            _validate_pattern(
                record,
                "nonce_hash",
                HASH_RE,
                "a 64-character hexadecimal hash",
            )
    elif kind == "evidence":
        if record["kind"] not in EVIDENCE_KINDS:
            raise ContractError("unknown evidence kind: %s" % record["kind"])
        _validate_pattern(record, "sha256", HASH_RE, "a 64-character hexadecimal hash")
        _validate_pattern(record, "source_sha", COMMIT_SHA_RE, "a 40- or 64-character commit SHA")
    elif kind == "agent_status":
        unknown = set(record) - AGENT_STATUS_FIELDS
        if unknown:
            raise ContractError(
                "unknown agent_status fields: %s" % ", ".join(sorted(unknown))
            )
        for field in ("dispatch_id", "agent_id", "role"):
            if not record[field] or len(record[field]) > 128:
                raise ContractError("%s must contain 1 to 128 characters" % field)
        if record.get("model") is not None and (
            not record["model"] or len(record["model"]) > 128
        ):
            raise ContractError("model must contain 1 to 128 characters")
        if "current_subtask" in record and record["current_subtask"] is not None:
            value = record["current_subtask"]
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ContractError(
                    "current_subtask must contain 1 to 256 characters"
                )
        if "recommendation" in record and record["recommendation"] is not None:
            value = record["recommendation"]
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ContractError(
                    "recommendation must contain 1 to 512 characters"
                )
        total_items = 0
        for field in AGENT_LIST_FIELDS:
            if field in record:
                _validate_agent_list(record, field)
                total_items += len(record[field])
        if total_items > MAX_AGENT_TOTAL_ITEMS:
            raise ContractError("agent status has too many structured list items")
        _validate_pattern(record, "dispatch_id", DISPATCH_RE, "a valid dispatch identifier")
        if record["state"] not in AGENT_STATES:
            raise ContractError("unknown agent state: %s" % record["state"])
        if "progress" in record:
            _validate_integer(record, "progress", 0, 100)
    elif kind == "review":
        if record["disposition"] not in REVIEW_DISPOSITIONS:
            raise ContractError("unknown review disposition: %s" % record["disposition"])
        _validate_pattern(record, "source_sha", COMMIT_SHA_RE, "a 40- or 64-character commit SHA")
        _validate_pattern(record, "report_sha256", HASH_RE, "a 64-character hexadecimal hash")
    elif kind == "blocker":
        if record["status"] not in BLOCKER_STATUSES:
            raise ContractError("unknown blocker status: %s" % record["status"])
    return record
