import argparse
import json
import sys
from pathlib import Path

from .doctor import WorktreeDoctor
from .errors import BoundaryError, ContractError, TeamControlError
from .git_context import RepoContext
from .service import ControlPlane
from .store import ControlStore


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ContractError("invalid command arguments: %s" % message)


def emit(value, stream=None):
    target = stream if stream is not None else sys.stdout
    target.write(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def build_parser():
    parser = JsonArgumentParser(prog="team-control")
    parser.add_argument("--repo", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    start = commands.add_parser("start")
    start.add_argument("--dispatch-id", required=True)
    start.add_argument("--title", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--risk", choices=("L1", "L2", "L3"), required=True)
    start.add_argument("--agent", required=True)
    start.add_argument("--slug", required=True)

    status = commands.add_parser("status")
    status.add_argument("--dispatch-id", required=True)

    transition = commands.add_parser("transition")
    transition.add_argument("--dispatch-id", required=True)
    transition.add_argument("--to", required=True)
    transition.add_argument("--reason", required=True)

    approvals = commands.add_parser("approvals")
    approvals.add_argument("--dispatch-id")

    doctor = commands.add_parser("doctor")
    doctor.add_argument("mode", choices=("inspect", "repair"))
    doctor.add_argument("--dispatch-id", required=True)
    doctor.add_argument("--agent", required=True)
    doctor.add_argument("--slug", required=True)
    doctor.add_argument("--base-sha", required=True)
    return parser


def _repo_context(raw_path):
    candidate = Path(raw_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BoundaryError("repository path does not exist") from error
    if not resolved.is_dir():
        raise BoundaryError("repository path must be a directory")
    return RepoContext.discover(resolved)


def execute(args):
    context = _repo_context(args.repo)
    store = ControlStore.for_repo(context)
    if args.command == "init":
        store.initialize()
        return {"database": str(store.path), "status": "initialized"}
    if not store.path.is_file():
        raise ContractError("control plane is not initialized; run init first")

    control = ControlPlane(context, store)
    if args.command == "start":
        return control.start_write_task(
            args.dispatch_id,
            args.title,
            args.objective,
            args.risk,
            args.agent,
            args.slug,
        )
    if args.command == "status":
        return control.status(args.dispatch_id)
    if args.command == "transition":
        return control.transition(args.dispatch_id, args.to, args.reason)
    if args.command == "approvals":
        return {"approvals": store.list_approvals(args.dispatch_id)}
    if args.command == "doctor":
        doctor = WorktreeDoctor(context, store)
        report = doctor.inspect(
            args.dispatch_id, args.agent, args.slug, args.base_sha
        )
        return report if args.mode == "inspect" else doctor.repair(report)
    raise ContractError("unknown command")


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        result = execute(args)
        emit(result)
        return 0
    except TeamControlError as error:
        emit(
            {"error": {"code": error.code, "message": str(error)}},
            stream=sys.stderr,
        )
        return 1
    except Exception:
        emit(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "unexpected internal error",
                }
            },
            stream=sys.stderr,
        )
        return 1
