import re

from .errors import GitStateError, ReconciliationError
from .git_context import run_argv


GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _validated_argv(argv):
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise ReconciliationError(
            "Git arguments must be a non-empty list or tuple of non-empty strings"
        )
    return list(argv)


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

    def _trusted_head(self):
        completed = run_argv(
            ["git", "rev-parse", "HEAD"], self.context.root
        )
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
        return _validated_verifier_result(verifier(operation))

    def reconcile_one(self, operation_id, verifier):
        with self.store.controlled_operation() as controlled:
            operation = self.store.get_operation(operation_id)
            if operation is None or operation["phase"] != "PREPARED":
                raise ReconciliationError("operation is not PREPARED")
            result = self._verify(operation, verifier)
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
    ):
        command_argv = _validated_argv(argv)
        if not callable(verifier):
            raise ReconciliationError("operation verifier must be callable")
        if on_verified is not None and not callable(on_verified):
            raise ReconciliationError("on_verified must be callable")

        with self.store.controlled_operation() as controlled:
            existing = controlled.operation_for_idempotency(idempotency_key)
            if existing is not None:
                operation = controlled.prepare_operation(
                    dispatch_id,
                    action,
                    request_hash,
                    target_sha,
                    idempotency_key,
                )
                if operation["phase"] != "PREPARED":
                    return operation

            actual_sha = self._trusted_head()
            if target_sha != actual_sha:
                raise GitStateError("operation target SHA does not match Git HEAD")

            if existing is None:
                operation = controlled.prepare_operation(
                    dispatch_id,
                    action,
                    request_hash,
                    target_sha,
                    idempotency_key,
                )

            completed = run_argv(
                command_argv, self.context.root, check=False
            )
            result = self._verify(operation, verifier)
            result.update(
                {
                    "command_returncode": completed.returncode,
                    "stderr": completed.stderr.strip(),
                }
            )
            if result["verified"] is True and on_verified is not None:
                on_verified(result)
            return controlled.finish_operation(
                operation["operation_id"],
                _terminal_phase(result["verified"]),
                result,
            )
