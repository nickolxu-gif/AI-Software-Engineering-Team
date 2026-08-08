import hashlib
import json
import math
import re
import uuid

from .contracts import validate_record
from .errors import ApprovalError, BoundaryError, ContractError
from .git_context import canonical_under, run_argv, validate_component


SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _validated_component(value, label):
    if not isinstance(value, str):
        raise ContractError("%s must be a string" % label)
    return validate_component(value, label)


def _validated_sha(value, label):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ContractError("%s must be a full lowercase hexadecimal SHA" % label)
    return value


def _validate_json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError("approval parameters must not contain NaN or infinity")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError("approval parameter keys must be strings")
            _validate_json_value(item)
        return
    raise ContractError("approval parameters must contain only JSON values")


class ControlPlane:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def current_head(self):
        return run_argv(
            ["git", "rev-parse", "HEAD"], self.context.root
        ).stdout.strip()

    def create_task(self, dispatch_id, title, objective, risk_level):
        validate_component(dispatch_id, "dispatch-id")
        record = {
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "title": title,
            "objective": objective,
            "risk_level": risk_level,
            "state": "PLANNED",
            "task_base_sha": self.current_head(),
            "owner": "Codex",
        }
        validate_record("task", record)
        return self.store.create_task(record)

    def request_approval(
        self,
        dispatch_id,
        action,
        target_sha,
        parameters,
        nonce,
        ttl_minutes,
    ):
        _validated_component(dispatch_id, "dispatch-id")
        _validated_component(action, "approval-action")
        _validated_sha(target_sha, "target_sha")
        if type(parameters) is not dict:
            raise ContractError("approval parameters must be a JSON object")
        _validate_json_value(parameters)
        if not isinstance(nonce, str) or not nonce.strip():
            raise ContractError("approval nonce must be a non-empty string")
        if (
            isinstance(ttl_minutes, bool)
            or not isinstance(ttl_minutes, (int, float))
            or not math.isfinite(ttl_minutes)
            or ttl_minutes < 1
            or ttl_minutes > 1440
        ):
            raise ContractError(
                "ttl_minutes must be a finite number from 1 to 1440"
            )
        try:
            request_json = json.dumps(
                {
                    "dispatch_id": dispatch_id,
                    "action": action,
                    "target_sha": target_sha,
                    "parameters": parameters,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ContractError("approval parameters must be valid JSON") from error
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        return self.store.create_approval(
            dispatch_id,
            action,
            target_sha,
            request_hash,
            nonce,
            ttl_minutes,
            str(uuid.uuid4()),
        )

    def consume_approval(self, approval_id, nonce, actual_sha):
        _validated_component(approval_id, "approval-id")
        _validated_sha(actual_sha, "actual_sha")
        return self.store.consume_approval(approval_id, nonce, actual_sha)

    def transition(self, dispatch_id, target, reason):
        validate_component(dispatch_id, "dispatch-id")
        validate_component(target, "target-state")
        if not isinstance(reason, str) or not reason.strip():
            raise ContractError("transition reason must be a non-empty string")
        return self.store.transition(dispatch_id, target, reason)

    def attach_worktree(self, dispatch_id, agent, slug, branch, path):
        validate_component(dispatch_id, "dispatch-id")
        validate_component(agent, "agent")
        validate_component(slug, "slug")
        expected_branch = "agent/%s/%s-%s" % (agent, dispatch_id, slug)
        if branch != expected_branch:
            raise BoundaryError(
                "branch must equal normative task branch: %s" % expected_branch
            )
        repo_root = self.context.common_dir.parent
        worktree_root = repo_root / ".worktrees"
        if worktree_root.is_symlink():
            raise BoundaryError(
                "worktree root must not be a symlink: %s" % worktree_root
            )
        candidate = worktree_root / (
            "%s-%s-%s" % (dispatch_id, agent, slug)
        )
        if candidate.is_symlink():
            raise BoundaryError(
                "worktree path must not be a symlink: %s" % candidate
            )
        expected_path = canonical_under(worktree_root, candidate)
        actual_path = canonical_under(worktree_root, path)
        if actual_path != expected_path:
            raise BoundaryError(
                "worktree path must equal normative task path: %s" % expected_path
            )
        return self.store.attach_worktree(
            dispatch_id, agent, slug, branch, str(actual_path)
        )

    def status(self, dispatch_id):
        _validated_component(dispatch_id, "dispatch-id")
        task, events, approvals = self.store.status_snapshot(dispatch_id)
        if task is None:
            raise KeyError(dispatch_id)
        git_cwd = task["worktree_path"] or self.context.root
        actual_head_sha = run_argv(
            ["git", "rev-parse", "HEAD"], git_cwd
        ).stdout.strip()
        return {
            "task": task,
            "events": events,
            "pending_approvals": approvals,
            "effective_state": (
                "NEEDS_HUMAN_APPROVAL" if approvals else task["state"]
            ),
            "actual_head_sha": actual_head_sha,
            "head_drift": actual_head_sha != task["current_head_sha"],
        }
