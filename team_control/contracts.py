import re

from .errors import ContractError


TASK_STATES = frozenset({
    "PLANNED", "NEEDS_CLARIFICATION", "DISPATCHED", "IN_PROGRESS",
    "PAUSE_REQUESTED", "PAUSED", "BLOCKED", "NEEDS_DIRECTION",
    "NEEDS_HUMAN_APPROVAL", "REVIEWING", "ACCEPTED", "INTEGRATED",
    "RELEASED", "CLOSED", "UNKNOWN",
})
RISK_LEVELS = frozenset({"L1", "L2", "L3"})
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

REQUIRED = {
    "task": ("schema_version", "dispatch_id", "title", "objective", "risk_level", "state", "task_base_sha", "owner"),
    "event": ("schema_version", "dispatch_id", "sequence", "event_type", "created_at"),
    "approval": ("schema_version", "approval_id", "dispatch_id", "action", "target_sha", "request_hash", "expires_at"),
    "evidence": ("schema_version", "evidence_id", "dispatch_id", "kind", "path", "sha256", "created_at"),
    "agent_status": ("schema_version", "dispatch_id", "agent_id", "role", "state", "updated_at"),
    "review": ("schema_version", "review_id", "dispatch_id", "reviewer", "disposition", "source_sha", "created_at"),
    "blocker": ("schema_version", "blocker_id", "dispatch_id", "reason", "owner", "status", "created_at"),
}


def validate_record(kind, record):
    if kind not in REQUIRED:
        raise ContractError("unknown contract kind: %s" % kind)
    missing = [key for key in REQUIRED[kind] if key not in record]
    if missing:
        raise ContractError("missing %s fields: %s" % (kind, ", ".join(missing)))
    if record.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    if kind == "task":
        if record["state"] not in TASK_STATES:
            raise ContractError("unknown task state: %s" % record["state"])
        if record["risk_level"] not in RISK_LEVELS:
            raise ContractError("unknown risk level: %s" % record["risk_level"])
        if not SHA_RE.fullmatch(record["task_base_sha"]):
            raise ContractError("task_base_sha must be a full hexadecimal SHA")
    return record
