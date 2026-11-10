"""Command line for offline smoke, hashing, and an explicit local dispatch."""

from __future__ import annotations

import json
import sys

from execution_adapter.bindings import BindingError, recommend
from execution_adapter.package_hash import source_hash
from execution_adapter.runner import dispatch, refusal
from execution_adapter.smoke import main as smoke_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: execution-adapter smoke|hash|run --config FILE --job FILE --approval FILE")
        print("smoke and hash do not call a model. run uses only the supplied private config.")
        return 0
    if args[0] == "smoke":
        return smoke_main()
    if args[0] == "hash":
        print(source_hash())
        return 0
    if args[0] != "run":
        print("unknown_command", file=sys.stderr)
        return 2
    values = {}
    rest = args[1:]
    while rest:
        flag = rest.pop(0)
        if flag not in {"--config", "--job", "--approval"} or not rest:
            print("invalid_arguments", file=sys.stderr)
            return 2
        values[flag] = rest.pop(0)
    if set(values) != {"--config", "--job", "--approval"}:
        print("invalid_arguments", file=sys.stderr)
        return 2
    config = json.loads(open(values["--config"], encoding="utf-8").read())
    task = json.loads(open(values["--job"], encoding="utf-8").read())
    approval = json.loads(open(values["--approval"], encoding="utf-8").read())
    recommendation = recommend(task, config)
    try:
        receipt = dispatch(task, recommendation, approval, config)
    except BindingError as exc:
        print(json.dumps(refusal(exc), ensure_ascii=False))
        return 2
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
