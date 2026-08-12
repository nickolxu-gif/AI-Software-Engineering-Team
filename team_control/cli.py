import argparse
import json
import sys
from pathlib import Path

from .doctor import WorktreeDoctor
from .errors import BoundaryError, ContractError, TeamControlError
from .git_context import RepoContext
from .intents import IntentService, safe_intent_summary
from .service import ControlPlane
from .store import ControlStore


COMMANDS = (
    "approvals", "doctor", "init", "intents", "process-intent",
    "process-pending-intents", "start", "status", "transition",
)
COMMAND_USAGE = {
    "approvals": "team-control --repo PATH approvals [--dispatch-id ID]",
    "doctor": (
        "team-control --repo PATH doctor {inspect,repair} --dispatch-id ID "
        "--agent AGENT --slug SLUG --base-sha SHA"
    ),
    "init": "team-control --repo PATH init",
    "intents": "team-control --repo PATH intents [--dispatch-id ID]",
    "process-intent": "team-control --repo PATH process-intent --intent-id UUID",
    "process-pending-intents": (
        "team-control --repo PATH process-pending-intents --limit 1..25"
    ),
    "start": (
        "team-control --repo PATH start --dispatch-id ID --title TITLE "
        "--objective OBJECTIVE --risk {L1,L2,L3} --agent AGENT --slug SLUG"
    ),
    "status": "team-control --repo PATH status --dispatch-id ID",
    "transition": (
        "team-control --repo PATH transition --dispatch-id ID --to STATE "
        "--reason REASON"
    ),
}


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

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

    intents = commands.add_parser("intents")
    intents.add_argument("--dispatch-id")

    process_intent = commands.add_parser("process-intent")
    process_intent.add_argument("--intent-id", required=True)

    pending_intents = commands.add_parser("process-pending-intents")
    pending_intents.add_argument("--limit", required=True, type=int)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("mode", choices=("inspect", "repair"))
    doctor.add_argument("--dispatch-id", required=True)
    doctor.add_argument("--agent", required=True)
    doctor.add_argument("--slug", required=True)
    doctor.add_argument("--base-sha", required=True)
    return parser


def _help_scope(argv):
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--repo":
            index += 2
            continue
        if argument.startswith("--repo="):
            index += 1
            continue
        if argument in COMMANDS:
            return argument
        index += 1
    return None


def help_payload(argv):
    command = _help_scope(argv)
    if command is None:
        return {
            "commands": list(COMMANDS),
            "program": "team-control",
            "status": "help",
            "usage": "team-control --repo PATH COMMAND [OPTIONS]",
        }
    payload = {
        "command": command,
        "program": "team-control",
        "status": "help",
        "usage": COMMAND_USAGE[command],
    }
    if command == "doctor":
        payload["modes"] = ["inspect", "repair"]
    return payload


def _repo_context(raw_path):
    candidate = Path(raw_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BoundaryError("repository path does not exist") from error
    if not resolved.is_dir():
        raise BoundaryError("repository path must be a directory")
    return RepoContext.discover(resolved)


def _safe_intent(intent):
    return safe_intent_summary(intent)


def execute(args):
    context = _repo_context(args.repo)
    store = ControlStore.for_repo(context)
    if args.command == "init":
        store.initialize()
        return {"database": str(store.path), "status": "initialized"}
    if not store.path.is_file():
        raise ContractError("control plane is not initialized; run init first")
    store.require_schema_compatible()

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
    if args.command == "intents":
        return {
            "intents": [
                _safe_intent(intent) for intent in store.list_intents(args.dispatch_id)
            ]
        }
    if args.command == "process-intent":
        return _safe_intent(IntentService(context, store, control).process(args.intent_id))
    if args.command == "process-pending-intents":
        result = IntentService(context, store, control).process_pending(args.limit)
        return {
            "attempted": result["attempted"],
            "results": [_safe_intent(intent) for intent in result["results"]],
        }
    if args.command == "doctor":
        doctor = WorktreeDoctor(context, store)
        report = doctor.inspect(
            args.dispatch_id, args.agent, args.slug, args.base_sha
        )
        return report if args.mode == "inspect" else doctor.repair(report)
    raise ContractError("unknown command")


def main(argv=None):
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        if any(argument in ("-h", "--help") for argument in arguments):
            emit(help_payload(arguments))
            return 0
        args = build_parser().parse_args(arguments)
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
