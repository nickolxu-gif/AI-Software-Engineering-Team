"""Durable Git operations and the reusable MVP 0 control-lock boundary."""

import re
from copy import deepcopy
from pathlib import Path

from .errors import BoundaryError, GitStateError, ReconciliationError
from .git_context import (
    RepoContext,
    canonical_under,
    run_argv,
    validate_component,
)


GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
MVP0_CONTROL_LOCK_THREAT_MODEL = (
    "MVP 0 uses the repository common-directory control lock for "
    "Orchestrator and ControlPlane SQLite writes and for Codex-managed "
    "Worktree, branch, and control-plane Git mutations. OperationCoordinator "
    "holds that lock continuously from fresh preflight through PREPARED, the "
    "Git command, verification, and durable terminal state. An execution "
    "Agent's ordinary add and commit inside its assigned Worktree are "
    "isolated by the one Worktree, one writer rule and do not use the "
    "common-directory control lock. This is a cooperative-writer guarantee; "
    "it does not claim to defend against a local process with same-user "
    "filesystem access that deliberately bypasses the lock."
)
ALLOWED_GIT_SUBCOMMANDS = frozenset(
    {
        "status",
        "rev-parse",
        "merge",
        "worktree",
        "branch",
        "commit",
        "cherry-pick",
        "rebase",
        "reset",
        "checkout",
        "switch",
        "restore",
        "tag",
        "update-ref",
    }
)
BLOCKED_GIT_GLOBAL_OPTIONS = frozenset(
    {
        "-C",
        "-c",
        "--bare",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--no-replace-objects",
        "--super-prefix",
        "--work-tree",
    }
)
BLOCKED_GIT_GLOBAL_PREFIXES = (
    "--config-env=",
    "--exec-path=",
    "--git-dir=",
    "--namespace=",
    "--super-prefix=",
    "--work-tree=",
)


def _validated_argv(argv):
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise ReconciliationError(
            "Git arguments must be a non-empty list or tuple of non-empty strings"
        )
    command = list(argv)
    if command[0] != "git":
        raise ReconciliationError("controlled command executable must be exactly git")
    if len(command) < 2 or command[1] not in ALLOWED_GIT_SUBCOMMANDS:
        raise ReconciliationError("Git subcommand is not allowed")
    for argument in command[1:]:
        if argument in BLOCKED_GIT_GLOBAL_OPTIONS or argument.startswith(
            BLOCKED_GIT_GLOBAL_PREFIXES
        ):
            raise ReconciliationError("Git global redirection is not allowed")
    return command


def _validated_verifier_result(result):
    if not isinstance(result, dict):
        raise ReconciliationError("operation verifier must return a dict")
    if "verified" not in result:
        raise ReconciliationError("operation verifier must return verified")
    verified = result["verified"]
    if verified is not True and verified is not False and verified is not None:
        raise ReconciliationError("operation verified must be true, false, or null")
    return dict(result)


def _terminal_phase(verified):
    if verified is True:
        return "COMMITTED"
    if verified is False:
        return "FAILED"
    return "BLOCKED"


class OperationCoordinator:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def _trusted_git_cwd(self, task):
        registered_common = Path(self.context.common_dir).resolve(strict=True)
        if task["worktree_path"]:
            for field in ("dispatch_id", "agent", "slug"):
                value = task[field]
                if not isinstance(value, str):
                    raise BoundaryError(
                        "task %s is not a trusted component" % field
                    )
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
            if not isinstance(task["worktree_path"], str):
                raise BoundaryError("stored worktree path is invalid")
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
            raise BoundaryError(
                "trusted Git cwd is unavailable: %s" % cwd
            ) from error
        if discovered.root != resolved_cwd:
            raise BoundaryError(
                "trusted Git cwd must be a repository root: %s" % cwd
            )
        if discovered.common_dir != registered_common:
            raise BoundaryError("trusted Git cwd belongs to another repository")
        return resolved_cwd

    def _trusted_head(self, cwd):
        completed = run_argv(["git", "rev-parse", "HEAD"], cwd)
        actual_sha = completed.stdout.strip()
        if GIT_SHA_RE.fullmatch(actual_sha) is None:
            raise GitStateError("Git HEAD is not a full lowercase hexadecimal SHA")
        return actual_sha

    @staticmethod
    def _verify(operation, verifier):
        if verifier is None:
            return {"verified": None, "reason": "no verifier"}
        if not callable(verifier):
            raise ReconciliationError("operation verifier must be callable")
        # Verifiers execute under the repository control lock and must only
        # inspect Git postconditions. Cooperative store writes fail on the
        # same lock and leave the operation PREPARED for later reconciliation.
        return _validated_verifier_result(verifier(operation))

    @staticmethod
    def _terminal_result(operation, result):
        result.pop("callback_status", None)
        prepared_result = operation["result"]
        if (
            result["verified"] is True
            and prepared_result is not None
            and prepared_result.get("callback_status") == "PENDING"
        ):
            result["callback_status"] = "PENDING"
        return result

    def reconcile_one(self, operation_id, verifier):
        with self.store.controlled_operation() as controlled:
            operation = self.store.get_operation(operation_id)
            if operation is None:
                raise ReconciliationError("operation is missing")
            if operation["phase"] != "PREPARED":
                return operation
            result = self._verify(operation, verifier)
            result = self._terminal_result(operation, result)
            return controlled.finish_operation(
                operation_id,
                _terminal_phase(result["verified"]),
                result,
            )

    def reconcile_all(self, verifiers):
        if not isinstance(verifiers, dict):
            raise ReconciliationError("operation verifiers must be a dict")
        results = []
        for operation in self.store.prepared_operations():
            results.append(
                self.reconcile_one(
                    operation["operation_id"],
                    verifiers.get(operation["action"]),
                )
            )
        return results

    def _run_pending_callback(self, operation, on_verified):
        if (
            on_verified is None
            or operation["phase"] != "COMMITTED"
            or operation["result"].get("verified") is not True
            or operation["result"].get("callback_status") != "PENDING"
        ):
            return operation
        # A crash after callback success but before the guarded marker leaves
        # PENDING and causes an at-least-once retry. Callbacks must therefore
        # be idempotent; exactly-once delivery is not claimed across processes.
        on_verified(deepcopy(operation["result"]))
        return self.store.complete_operation_callback(operation["operation_id"])

    def execute_git(
        self,
        dispatch_id,
        action,
        request_hash,
        target_sha,
        idempotency_key,
        argv,
        verifier,
        on_verified=None,
        preflight=None,
    ):
        command_argv = _validated_argv(argv)
        if not callable(verifier):
            raise ReconciliationError("operation verifier must be callable")
        if on_verified is not None and not callable(on_verified):
            raise ReconciliationError("on_verified must be callable")
        if preflight is not None and not callable(preflight):
            raise ReconciliationError("operation preflight must be callable")

        prepared_result = (
            {"callback_status": "PENDING"} if on_verified is not None else None
        )
        terminal = None
        with self.store.controlled_operation() as controlled:
            existing = controlled.operation_for_idempotency(idempotency_key)
            if existing is not None:
                operation = controlled.prepare_operation(
                    dispatch_id,
                    action,
                    request_hash,
                    target_sha,
                    idempotency_key,
                    result=prepared_result,
                )
                if operation["phase"] == "PREPARED":
                    raise ReconciliationError(
                        "operation is PREPARED; reconcile before executing again"
                    )
                terminal = operation
            else:
                if controlled.prepared_operations():
                    raise ReconciliationError(
                        "prepared operations must be reconciled before new execution"
                    )

                task = controlled.get_task(dispatch_id)
                if task is None:
                    raise KeyError(dispatch_id)
                cwd = self._trusted_git_cwd(task)
                actual_sha = self._trusted_head(cwd)
                # Keep fresh preflight as close as durable PREPARED permits to
                # the command. See MVP0_CONTROL_LOCK_THREAT_MODEL.
                if preflight is not None:
                    preflight(deepcopy(task))
                if not (
                    target_sha == task["current_head_sha"] == actual_sha
                ):
                    raise GitStateError(
                        "operation target, task head, and Git HEAD do not match"
                    )

                operation = controlled.prepare_operation(
                    dispatch_id,
                    action,
                    request_hash,
                    target_sha,
                    idempotency_key,
                    result=prepared_result,
                )

                completed = run_argv(command_argv, cwd, check=False)
                result = self._verify(operation, verifier)
                result = self._terminal_result(operation, result)
                result.update(
                    {
                        "command_returncode": completed.returncode,
                        "stderr": completed.stderr.strip(),
                    }
                )
                terminal = controlled.finish_operation(
                    operation["operation_id"],
                    _terminal_phase(result["verified"]),
                    result,
                )

        return self._run_pending_callback(terminal, on_verified)
