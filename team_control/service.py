from .contracts import validate_record
from .errors import BoundaryError, ContractError
from .git_context import canonical_under, run_argv, validate_component


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
        validate_component(dispatch_id, "dispatch-id")
        task, events = self.store.status_snapshot(dispatch_id)
        return {
            "task": task,
            "events": events,
        }
