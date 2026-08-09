import hashlib
import json
import os
import re
from pathlib import Path

from .errors import ReconciliationError, TeamControlError
from .git_context import RepoContext, run_argv, validate_component
from .operations import OperationCoordinator


GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def registered_worktrees(context):
    output = run_argv(
        ["git", "worktree", "list", "--porcelain"], context.root
    ).stdout
    entries = {}
    current = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current = os.path.abspath(line[len("worktree "):])
            entries[current] = {"branch": None, "head": None}
        elif current is not None and line.startswith("HEAD "):
            entries[current]["head"] = line[len("HEAD "):]
        elif current is not None and line.startswith("branch refs/heads/"):
            entries[current]["branch"] = line[len("branch refs/heads/"):]
    return entries


class WorktreeDoctor:
    def __init__(self, context, store):
        self.context = context
        self.store = store
        self.main_root = Path(context.common_dir).resolve(strict=True).parent
        self.main_context = RepoContext.discover(self.main_root)
        self.operations = OperationCoordinator(self.main_context, store)

    def expected(self, dispatch_id, agent, slug):
        for value, label in (
            (dispatch_id, "dispatch-id"),
            (agent, "agent"),
            (slug, "slug"),
        ):
            validate_component(value, label)
        repo_root = Path(self.context.common_dir).resolve(strict=True).parent
        worktree_root = repo_root / ".worktrees"
        path = worktree_root / ("%s-%s-%s" % (dispatch_id, agent, slug))
        branch = "agent/%s/%s-%s" % (agent, dispatch_id, slug)
        return branch, path

    @staticmethod
    def _blocked_report(classification, details):
        report = dict(details)
        report["classification"] = classification
        return report

    def inspect(self, dispatch_id, agent, slug, task_base_sha):
        if (
            not isinstance(task_base_sha, str)
            or GIT_SHA_RE.fullmatch(task_base_sha) is None
        ):
            raise ReconciliationError("task base SHA is invalid")
        branch, path = self.expected(dispatch_id, agent, slug)
        worktree_root = path.parent
        details = {
            "dispatch_id": dispatch_id,
            "agent": agent,
            "slug": slug,
            "branch": branch,
            "path": str(path),
            "task_base_sha": task_base_sha,
            "branch_sha": None,
            "registered": False,
            "registered_branch": None,
            "registered_head": None,
            "path_exists": os.path.lexists(str(path)),
            "head_sha": None,
            "common_dir": None,
            "clean": None,
        }

        task = self.store.get_task(dispatch_id)
        expected_identity = (agent, slug, branch)
        if task is None or task["task_base_sha"] != task_base_sha:
            return self._blocked_report("BLOCKED_TASK_MISMATCH", details)
        stored_identity = (task["agent"], task["slug"], task["branch"])
        if stored_identity not in ((None, None, None), expected_identity):
            return self._blocked_report("BLOCKED_TASK_MISMATCH", details)
        if task["worktree_path"] not in (None, str(path)):
            return self._blocked_report("BLOCKED_TASK_MISMATCH", details)

        if worktree_root.is_symlink():
            return self._blocked_report("BLOCKED_ROOT_SYMLINK", details)
        if path.is_symlink():
            return self._blocked_report("BLOCKED_PATH_SYMLINK", details)

        branch_check = run_argv(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch],
            self.context.root,
            check=False,
        )
        branch_exists = branch_check.returncode == 0
        if branch_exists:
            details["branch_sha"] = run_argv(
                ["git", "rev-parse", branch], self.context.root
            ).stdout.strip()

        registrations = registered_worktrees(self.main_context)
        expected_path = os.path.abspath(str(path))
        registration = registrations.get(expected_path)
        if registration is not None:
            details["registered"] = True
            details["registered_branch"] = registration["branch"]
            details["registered_head"] = registration["head"]

        branch_registered_elsewhere = any(
            registered_path != expected_path
            and registered["branch"] == branch
            for registered_path, registered in registrations.items()
        )
        if branch_registered_elsewhere:
            classification = "BLOCKED_REGISTRATION_MISMATCH"
        elif details["registered"] and details["registered_branch"] != branch:
            classification = "BLOCKED_REGISTRATION_MISMATCH"
        elif details["path_exists"] and not details["registered"]:
            classification = "BLOCKED_PATH_RESIDUE"
        elif (
            branch_exists
            and not details["registered"]
            and not details["path_exists"]
            and details["branch_sha"] != task_base_sha
        ):
            classification = "BLOCKED_BRANCH_ADVANCED"
        elif details["registered"] and not details["path_exists"]:
            classification = "BLOCKED_STALE_METADATA"
        elif (
            branch_exists
            and not details["registered"]
            and not details["path_exists"]
            and details["branch_sha"] == task_base_sha
        ):
            classification = "REPAIRABLE_BRANCH_ONLY"
        elif (
            not branch_exists
            and not details["registered"]
            and not details["path_exists"]
        ):
            classification = "NO_RESIDUE"
        elif details["registered"] and details["path_exists"] and branch_exists:
            classification = self._inspect_registered(path, details)
        else:
            classification = "BLOCKED_UNKNOWN"
        return self._blocked_report(classification, details)

    def _inspect_registered(self, path, details):
        try:
            discovered = RepoContext.discover(path)
        except (OSError, TeamControlError):
            return "BLOCKED_REPOSITORY_MISMATCH"
        expected_common = Path(self.context.common_dir).resolve(strict=True)
        if discovered.root != path or discovered.common_dir != expected_common:
            return "BLOCKED_REPOSITORY_MISMATCH"
        details["common_dir"] = str(discovered.common_dir)
        head_sha = run_argv(["git", "rev-parse", "HEAD"], path).stdout.strip()
        details["head_sha"] = head_sha
        if (
            GIT_SHA_RE.fullmatch(head_sha) is None
            or head_sha != details["branch_sha"]
            or head_sha != details["registered_head"]
        ):
            return "BLOCKED_HEAD_MISMATCH"
        status = run_argv(
            ["git", "status", "--porcelain", "--untracked-files=all"], path
        ).stdout
        details["clean"] = not status.strip()
        if not details["clean"]:
            return "BLOCKED_DIRTY_WORKTREE"
        return "HEALTHY"

    def _fresh_report(self, report):
        if not isinstance(report, dict):
            raise ReconciliationError("doctor report must be a dict")
        required = ("dispatch_id", "agent", "slug", "task_base_sha")
        if any(field not in report for field in required):
            raise ReconciliationError("doctor report is incomplete")
        return self.inspect(*(report[field] for field in required))

    @staticmethod
    def _request_hash(report):
        encoded = json.dumps(
            report, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _attach(self, report):
        self.store.attach_worktree(
            report["dispatch_id"],
            report["agent"],
            report["slug"],
            report["branch"],
            report["path"],
        )

    def _reserve(self, report):
        self.store.reserve_worktree_identity(
            report["dispatch_id"],
            report["agent"],
            report["slug"],
            report["branch"],
        )

    @staticmethod
    def _historical_report(report, classification):
        historical = dict(report)
        historical.update(
            {
                "classification": classification,
                "branch_sha": (
                    report["task_base_sha"]
                    if classification == "REPAIRABLE_BRANCH_ONLY"
                    else None
                ),
                "registered": False,
                "registered_branch": None,
                "registered_head": None,
                "path_exists": False,
                "head_sha": None,
                "common_dir": None,
                "clean": None,
            }
        )
        return historical

    def _is_committed_operation(self, idempotency_key, action, report):
        operation = self.store.operation_for_idempotency(idempotency_key)
        return (
            operation is not None
            and operation["action"] == action
            and operation["request_hash"] == self._request_hash(report)
            and operation["target_sha"] == report["task_base_sha"]
            and operation["phase"] == "COMMITTED"
            and operation["result"].get("verified") is True
        )

    def _retryable_idempotency_key(self, base_key, action, report):
        key = base_key
        request_hash = self._request_hash(report)
        while True:
            operation = self.store.operation_for_idempotency(key)
            if operation is None:
                return key
            if not (
                operation["action"] == action
                and operation["request_hash"] == request_hash
                and operation["target_sha"] == report["task_base_sha"]
            ):
                raise ReconciliationError(
                    "operation idempotency key belongs to another request"
                )
            if not (
                operation["phase"] == "FAILED"
                and operation["result"].get("verified") is False
            ):
                return key
            key = "%s:retry:%s" % (base_key, operation["operation_id"])

    def _assert_mutation_allowed(self, report):
        task = self.store.get_task(report["dispatch_id"])
        if task is None or task["state"] != "PLANNED":
            raise ReconciliationError(
                "doctor mutation requires a PLANNED task"
            )

    def _preflight(self, report, expected_classification, task):
        """Revalidate a new mutation while OperationCoordinator holds its lock."""
        fresh = self._fresh_report(report)
        if fresh["classification"] != expected_classification:
            raise ReconciliationError(
                "doctor preflight refuses %s: %s"
                % (expected_classification, fresh["classification"])
            )
        expected_identity = (
            fresh["agent"], fresh["slug"], fresh["branch"]
        )
        if (
            task["dispatch_id"] != fresh["dispatch_id"]
            or task["task_base_sha"] != fresh["task_base_sha"]
            or task["current_head_sha"] != fresh["task_base_sha"]
            or task["state"] != "PLANNED"
            or (task["agent"], task["slug"], task["branch"])
            not in ((None, None, None), expected_identity)
            or task["worktree_path"] is not None
        ):
            raise ReconciliationError(
                "doctor preflight task identity or state changed"
            )
        if self.main_root.is_symlink():
            raise ReconciliationError("main repository root must not be a symlink")
        worktree_root = Path(fresh["path"]).parent
        target = Path(fresh["path"])
        if worktree_root.is_symlink():
            raise ReconciliationError("worktree root must not be a symlink")
        if target.is_symlink():
            raise ReconciliationError("worktree target must not be a symlink")
        if worktree_root.exists() and not worktree_root.is_dir():
            raise ReconciliationError("worktree root must be a directory")
        discovered = RepoContext.discover(self.main_root)
        expected_common = Path(self.context.common_dir).resolve(strict=True)
        if (
            discovered.root != self.main_root
            or discovered.common_dir != expected_common
        ):
            raise ReconciliationError(
                "main repository common-dir does not match"
            )
        main_exists = run_argv(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"],
            self.main_root,
            check=False,
        )
        if main_exists.returncode != 0:
            raise ReconciliationError("main branch does not exist")
        current_branch = run_argv(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            self.main_root,
            check=False,
        )
        if current_branch.returncode != 0 or current_branch.stdout.strip() != "main":
            raise ReconciliationError(
                "main repository current branch must be main"
            )
        status = run_argv(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            self.main_root,
        ).stdout
        if status.strip():
            raise ReconciliationError("main root must be clean")
        ignored = run_argv(
            ["git", "check-ignore", "--quiet", "--", ".worktrees/"],
            self.main_root,
            check=False,
        )
        if ignored.returncode != 0:
            raise ReconciliationError(".worktrees must be ignored")
        main_head = run_argv(
            ["git", "rev-parse", "main"], self.main_root
        ).stdout.strip()
        if fresh["task_base_sha"] != main_head:
            raise ReconciliationError("task base must equal fresh main HEAD")

    def _reconcile_prepared(self, idempotency_key, action, report):
        operation = self.store.operation_for_idempotency(idempotency_key)
        if operation is None:
            return None
        expected = (
            action,
            self._request_hash(report),
            report["task_base_sha"],
        )
        actual = (
            operation["action"],
            operation["request_hash"],
            operation["target_sha"],
        )
        if actual != expected:
            raise ReconciliationError(
                "operation idempotency key belongs to another request"
            )
        if operation["phase"] == "PREPARED":
            return self.operations.reconcile_one(
                operation["operation_id"],
                lambda ignored: self._verified(report),
            )
        return operation

    def reconcile_prepared_create(self, report):
        """Safely terminalize this dispatch's interrupted create before routing."""
        fresh = self._fresh_report(report)
        request_report = self._historical_report(fresh, "NO_RESIDUE")
        request_hash = self._request_hash(request_report)
        prepared = [
            operation
            for operation in self.store.prepared_operations()
            if operation["dispatch_id"] == fresh["dispatch_id"]
            and operation["action"] == "create-worktree"
        ]
        reconciled = []
        for operation in prepared:
            if (
                operation["request_hash"] != request_hash
                or operation["target_sha"] != fresh["task_base_sha"]
            ):
                raise ReconciliationError(
                    "prepared create operation belongs to another request"
                )
            reconciled.append(
                self.operations.reconcile_one(
                    operation["operation_id"],
                    lambda ignored: self._verified(fresh),
                )
            )
        return reconciled

    def _verified(self, report):
        inspected = self.inspect(
            report["dispatch_id"],
            report["agent"],
            report["slug"],
            report["task_base_sha"],
        )
        return {
            "verified": inspected["classification"] == "HEALTHY",
            "classification": inspected["classification"],
        }

    def repair(self, report):
        fresh = self._fresh_report(report)
        request_report = fresh
        base_key = "doctor:%s:%s:%s" % (
            fresh["dispatch_id"], fresh["branch"], fresh["task_base_sha"]
        )
        if fresh["classification"] == "HEALTHY":
            request_report = self._historical_report(
                fresh, "REPAIRABLE_BRANCH_ONLY"
            )
        idempotency_key = self._retryable_idempotency_key(
            base_key,
            "doctor-reconstruct-worktree",
            request_report,
        )
        self._reconcile_prepared(
            idempotency_key, "doctor-reconstruct-worktree", request_report
        )
        idempotency_key = self._retryable_idempotency_key(
            base_key,
            "doctor-reconstruct-worktree",
            request_report,
        )
        if fresh["classification"] == "HEALTHY":
            if not self._is_committed_operation(
                idempotency_key,
                "doctor-reconstruct-worktree",
                request_report,
            ):
                raise ReconciliationError("doctor refuses repair: HEALTHY")
        elif fresh["classification"] != "REPAIRABLE_BRANCH_ONLY":
            raise ReconciliationError(
                "doctor refuses repair: %s" % fresh["classification"]
            )
        self._assert_mutation_allowed(fresh)
        self._reserve(fresh)
        operation = self.operations.execute_git(
            fresh["dispatch_id"],
            "doctor-reconstruct-worktree",
            self._request_hash(request_report),
            fresh["task_base_sha"],
            idempotency_key,
            ["git", "worktree", "add", fresh["path"], fresh["branch"]],
            lambda ignored: self._verified(fresh),
            lambda ignored: self._attach(fresh),
            preflight=lambda task: self._preflight(
                fresh, "REPAIRABLE_BRANCH_ONLY", task
            ),
        )
        if operation["phase"] != "COMMITTED":
            raise ReconciliationError(
                "worktree reconstruction could not be verified"
            )
        repaired = self._fresh_report(fresh)
        if repaired["classification"] != "HEALTHY":
            raise ReconciliationError(
                "repair did not produce a healthy Worktree"
            )
        return repaired

    def create(self, report):
        fresh = self._fresh_report(report)
        request_report = fresh
        base_key = "create:%s:%s" % (
            fresh["dispatch_id"], fresh["task_base_sha"]
        )
        if fresh["classification"] == "HEALTHY":
            request_report = self._historical_report(fresh, "NO_RESIDUE")
        idempotency_key = self._retryable_idempotency_key(
            base_key, "create-worktree", request_report
        )
        self._reconcile_prepared(
            idempotency_key, "create-worktree", request_report
        )
        idempotency_key = self._retryable_idempotency_key(
            base_key, "create-worktree", request_report
        )
        if fresh["classification"] == "HEALTHY":
            if not self._is_committed_operation(
                idempotency_key, "create-worktree", request_report
            ):
                raise ReconciliationError("doctor refuses create: HEALTHY")
        elif fresh["classification"] != "NO_RESIDUE":
            raise ReconciliationError(
                "doctor refuses create: %s" % fresh["classification"]
            )
        self._assert_mutation_allowed(fresh)
        self._reserve(fresh)
        operation = self.operations.execute_git(
            fresh["dispatch_id"],
            "create-worktree",
            self._request_hash(request_report),
            fresh["task_base_sha"],
            idempotency_key,
            [
                "git", "worktree", "add", "-b", fresh["branch"],
                fresh["path"], fresh["task_base_sha"],
            ],
            lambda ignored: self._verified(fresh),
            lambda ignored: self._attach(fresh),
            preflight=lambda task: self._preflight(
                fresh, "NO_RESIDUE", task
            ),
        )
        if operation["phase"] != "COMMITTED":
            raise ReconciliationError("worktree creation could not be verified")
        created = self._fresh_report(fresh)
        if created["classification"] != "HEALTHY":
            raise ReconciliationError(
                "create did not produce a healthy Worktree"
            )
        return created
