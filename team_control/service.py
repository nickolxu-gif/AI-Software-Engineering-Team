import hashlib
import json
import math
import re
import uuid
from pathlib import Path

from .contracts import validate_record
from .errors import (
    ApprovalError,
    BoundaryError,
    ContractError,
    GitStateError,
    ReconciliationError,
    TransitionError,
)
from .git_context import RepoContext, canonical_under, run_argv, validate_component
from .store import utc_now


SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
APPROVAL_REQUEST_DOMAIN = b"team-control/approval-request/v1\n"
JS_SAFE_INTEGER_MAX = 9007199254740991


def _validated_component(value, label):
    if not isinstance(value, str):
        raise ContractError("%s must be a string" % label)
    return validate_component(value, label)


def _validated_sha(value, label):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ContractError("%s must be a full lowercase hexadecimal SHA" % label)
    return value


def _validate_json_value(value):
    if value is None or type(value) in (str, bool):
        return
    if type(value) is int:
        if abs(value) > JS_SAFE_INTEGER_MAX:
            raise ContractError("approval integers must be within the JS safe range")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key.isascii():
                raise ContractError(
                    "approval parameter keys must be ASCII strings"
                )
            _validate_json_value(item)
        return
    raise ContractError("approval parameters must contain only JSON values")


def _canonical_approval_request_bytes(
    dispatch_id, action, target_sha, parameters
):
    payload = {
        "dispatch_id": dispatch_id,
        "action": action,
        "target_sha": target_sha,
        "parameters": parameters,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return APPROVAL_REQUEST_DOMAIN + encoded


class ControlPlane:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def current_head(self):
        return run_argv(
            ["git", "rev-parse", "HEAD"], self.context.root
        ).stdout.strip()

    def _trusted_git_cwd(self, task):
        registered_common = Path(self.context.common_dir).resolve(strict=True)
        if task["worktree_path"]:
            component_fields = ("dispatch_id", "agent", "slug")
            for field in component_fields:
                value = task[field]
                if not isinstance(value, str):
                    raise BoundaryError("task %s is not a trusted component" % field)
                validate_component(value, "task-%s" % field)
            expected_branch = "agent/%s/%s-%s" % (
                task["agent"],
                task["dispatch_id"],
                task["slug"],
            )
            if task["branch"] != expected_branch:
                raise BoundaryError(
                    "stored branch is not the normative task branch: %s"
                    % expected_branch
                )

            repo_root = registered_common.parent
            worktree_root = repo_root / ".worktrees"
            if worktree_root.is_symlink():
                raise BoundaryError(
                    "worktree root must not be a symlink: %s" % worktree_root
                )
            candidate = worktree_root / (
                "%s-%s-%s"
                % (task["dispatch_id"], task["agent"], task["slug"])
            )
            stored_path = Path(task["worktree_path"])
            if candidate.is_symlink() or stored_path.is_symlink():
                raise BoundaryError(
                    "worktree path must not be a symlink: %s" % stored_path
                )
            try:
                expected_path = canonical_under(worktree_root, candidate)
                cwd = canonical_under(worktree_root, stored_path)
            except OSError as error:
                raise BoundaryError(
                    "trusted worktree path is unavailable: %s" % stored_path
                ) from error
            if cwd != expected_path:
                raise BoundaryError(
                    "worktree path must equal normative task path: %s"
                    % expected_path
                )
        else:
            cwd = Path(self.context.root)
            if cwd.is_symlink():
                raise BoundaryError("repository root must not be a symlink: %s" % cwd)

        try:
            resolved_cwd = cwd.resolve(strict=True)
            discovered = RepoContext.discover(resolved_cwd)
        except (BoundaryError, GitStateError):
            raise
        except OSError as error:
            raise BoundaryError("trusted Git cwd is unavailable: %s" % cwd) from error
        if discovered.root != resolved_cwd:
            raise BoundaryError("trusted Git cwd must be a repository root: %s" % cwd)
        if discovered.common_dir != registered_common:
            raise BoundaryError("trusted Git cwd belongs to another repository")
        return resolved_cwd

    def _trusted_actual_head(self, task):
        cwd = self._trusted_git_cwd(task)
        actual_sha = run_argv(["git", "rev-parse", "HEAD"], cwd).stdout.strip()
        if SHA_RE.fullmatch(actual_sha) is None:
            raise GitStateError("Git HEAD is not a full lowercase hexadecimal SHA")
        return actual_sha, cwd

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

    def start_write_task(
        self, dispatch_id, title, objective, risk_level, agent, slug
    ):
        _validated_component(dispatch_id, "dispatch-id")
        _validated_component(agent, "agent")
        _validated_component(slug, "slug")
        main_root = Path(self.context.common_dir).resolve(strict=True).parent
        main_check = run_argv(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"],
            main_root,
            check=False,
        )
        if main_check.returncode != 0:
            raise ReconciliationError("main branch does not exist")
        main_head = run_argv(["git", "rev-parse", "main"], main_root).stdout.strip()
        _validated_sha(main_head, "main HEAD")
        expected_branch = "agent/%s/%s-%s" % (agent, dispatch_id, slug)
        expected_path = main_root / ".worktrees" / (
            "%s-%s-%s" % (dispatch_id, agent, slug)
        )
        record = {
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "title": title,
            "objective": objective,
            "risk_level": risk_level,
            "state": "PLANNED",
            "task_base_sha": main_head,
            "owner": "Codex",
        }
        validate_record("task", record)
        task = self.store.create_or_get_write_task(
            record, agent, slug, expected_branch, expected_path
        )
        from .doctor import WorktreeDoctor

        doctor = WorktreeDoctor(RepoContext.discover(main_root), self.store)
        report = doctor.inspect(
            dispatch_id, agent, slug, task["task_base_sha"]
        )
        if task["state"] == "DISPATCHED":
            if report["classification"] != "HEALTHY":
                raise ReconciliationError(
                    "dispatched write task Worktree is not healthy"
                )
            return task
        try:
            doctor.reconcile_prepared_create(report)
            report = doctor.inspect(
                dispatch_id, agent, slug, task["task_base_sha"]
            )
            if report["classification"] == "REPAIRABLE_BRANCH_ONLY":
                doctor.repair(report)
            else:
                doctor.create(report)
            return self.store.transition(
                dispatch_id, "DISPATCHED", "isolated Worktree verified"
            )
        except (ReconciliationError, TransitionError):
            latest = self.store.get_task(dispatch_id)
            if latest is not None and latest["state"] == "DISPATCHED":
                recovered = doctor.inspect(
                    dispatch_id, agent, slug, latest["task_base_sha"]
                )
                if recovered["classification"] == "HEALTHY":
                    return latest
            raise

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
        if (
            not isinstance(nonce, str)
            or not nonce.strip()
            or len(nonce) < 16
        ):
            raise ContractError(
                "approval nonce must be a non-empty string of at least 16 characters"
            )
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
            request_bytes = _canonical_approval_request_bytes(
                dispatch_id,
                action,
                target_sha,
                parameters,
            )
        except (TypeError, ValueError) as error:
            raise ContractError("approval parameters must be valid JSON") from error
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        return self.store.create_approval(
            dispatch_id,
            action,
            target_sha,
            request_hash,
            nonce,
            ttl_minutes,
            str(uuid.uuid4()),
        )

    def consume_approval(self, approval_id, nonce):
        _validated_component(approval_id, "approval-id")

        def head_observer(task):
            actual_sha, _ = self._trusted_actual_head(task)
            return actual_sha

        return self.store.consume_approval(approval_id, nonce, head_observer)

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

        def observe(snapshot):
            (
                task,
                events,
                approvals,
                agents,
                blockers,
                reviews,
                evidence,
            ) = snapshot
            if task is None:
                raise ReconciliationError("task is missing: %s" % dispatch_id)
            actual_head_sha, _ = self._trusted_actual_head(task)

            observed_reviews = []
            stale_reviews = []
            for original in reviews:
                review = dict(original)
                reasons = self.store.review_stale_reasons(
                    task, review, actual_head_sha
                )
                review["stale"] = bool(reasons)
                review["stale_reasons"] = reasons
                review["effective"] = not reasons
                if reasons:
                    stale_reviews.append(review["review_id"])
                observed_reviews.append(review)

            observed_evidence = []
            stale_evidence = []
            for original in evidence:
                record = dict(original)
                reasons = self.store.evidence_stale_reasons(
                    task, record, actual_head_sha
                )
                record["stale"] = bool(reasons)
                record["stale_reasons"] = reasons
                if reasons:
                    stale_evidence.append(record["evidence_id"])
                observed_evidence.append(record)

            return {
                "task": task,
                "events": events,
                "agents": agents,
                "blockers": blockers,
                "reviews": observed_reviews,
                "evidence": observed_evidence,
                "pending_approvals": approvals,
                "effective_state": (
                    "NEEDS_HUMAN_APPROVAL" if approvals else task["state"]
                ),
                "actual_head_sha": actual_head_sha,
                "head_drift": actual_head_sha != task["current_head_sha"],
                "review_stale": stale_reviews,
                "evidence_stale": stale_evidence,
                "valid_acceptance": any(
                    review["disposition"] == "ACCEPT" and review["effective"]
                    for review in observed_reviews
                ),
                "observed_at": utc_now(),
            }

        return self.store.observe_status(dispatch_id, observe)
