import hashlib
import json
import uuid

from .contracts import (
    TASK_INTAKE_REQUEST_FIELDS,
    UUID_RE,
    validate_task_intake_text,
)
from .errors import ContractError


TASK_INTAKE_REQUEST_HASH_DOMAIN = b"team-control/task-intake-request/v1\n"
SAFE_TASK_INTAKE_FIELDS = (
    "intake_id", "title", "objective", "status", "result_code",
    "created_at", "updated_at",
)
_TEXT_LIMITS = {"title": 120, "objective": 2000, "context": 2000}


def _require_exact_fields(value):
    if type(value) is not dict or set(value) != TASK_INTAKE_REQUEST_FIELDS:
        raise ContractError("task intake request fields do not match the contract")


def _bounded_text(value, field, allow_none=False):
    return validate_task_intake_text(
        value, field, _TEXT_LIMITS[field], allow_none=allow_none,
    )


def normalize_task_intake_request(request):
    _require_exact_fields(request)
    idempotency_key = request["idempotency_key"]
    if type(idempotency_key) is not str or UUID_RE.fullmatch(idempotency_key) is None:
        raise ContractError("idempotency_key must be a UUID")
    return {
        "title": _bounded_text(request["title"], "title"),
        "objective": _bounded_text(request["objective"], "objective"),
        "context": _bounded_text(request["context"], "context", allow_none=True),
        "idempotency_key": str(uuid.UUID(idempotency_key)),
    }


def task_intake_request_hash(normalized):
    value = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(
        TASK_INTAKE_REQUEST_HASH_DOMAIN + value.encode("utf-8")
    ).hexdigest()


def safe_task_intake_summary(intake):
    return {field: intake[field] for field in SAFE_TASK_INTAKE_FIELDS}


class TaskIntakeSubmissionService:
    """Browser capability: submit a bounded request without execution power."""

    def __init__(self, store):
        self.store = store

    def submit(self, request):
        normalized = normalize_task_intake_request(request)
        intake = self.store.create_task_intake(
            normalized["title"],
            normalized["objective"],
            normalized["context"],
            task_intake_request_hash(normalized),
            normalized["idempotency_key"],
        )
        return safe_task_intake_summary(intake)


class CodexTaskIntakeService:
    """Codex capability: read and durably link handled requests to a dispatch."""

    def __init__(self, store):
        self.store = store

    def list_pending(self, limit=10):
        return self.store.list_pending_task_intakes(limit)

    def acknowledge(self, intake_id, dispatch_id, disposition="DISPATCHED"):
        return self.store.acknowledge_task_intake(
            intake_id, dispatch_id, disposition,
        )
