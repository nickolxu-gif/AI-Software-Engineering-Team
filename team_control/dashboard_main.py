import argparse
import errno
import hashlib
import http.client
import json
import signal
import sys
import webbrowser
from pathlib import Path

from .dashboard_read_model import (
    DashboardReadModel,
    DashboardUnavailableError,
)
from .dashboard_server import create_server
from .errors import TeamControlError
from .git_context import RepoContext
from .store import ControlStore


HOST = "127.0.0.1"


class DashboardMainError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class DashboardArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DashboardMainError(
            "INVALID_ARGUMENTS",
            "invalid dashboard arguments",
        )


def build_parser():
    parser = DashboardArgumentParser(
        prog="open-team-dashboard",
        allow_abbrev=False,
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-open", action="store_true")
    return parser


def _emit(payload, stream):
    stream.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    stream.flush()


def _default_port(common_dir):
    digest = hashlib.sha256(str(common_dir).encode("utf-8")).digest()
    return 49152 + int.from_bytes(digest[:2], "big") % 16383


def _probe_existing(port, repository_id):
    connection = http.client.HTTPConnection(HOST, port, timeout=1)
    try:
        connection.request(
            "GET",
            "/api/health",
            headers={"Host": "%s:%d" % (HOST, port)},
        )
        response = connection.getresponse()
        body = response.read()
    except OSError as error:
        if error.errno == errno.ECONNREFUSED:
            return False
        raise DashboardMainError(
            "PORT_IN_USE",
            "dashboard port is already used by another service",
        ) from error
    except http.client.HTTPException as error:
        raise DashboardMainError(
            "PORT_IN_USE",
            "dashboard port is already used by another service",
        ) from error
    finally:
        connection.close()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        payload = None
    matching = (
        response.status == 200
        and response.getheader("X-Team-Repository-ID") == repository_id
        and isinstance(payload, dict)
        and payload.get("data", {}).get("status") == "HEALTHY"
    )
    if not matching:
        raise DashboardMainError(
            "PORT_IN_USE",
            "dashboard port is already used by another service",
        )
    return True


def _canonical_repo(raw_repo):
    try:
        candidate = Path(raw_repo).resolve(strict=True)
    except OSError as error:
        raise DashboardMainError(
            "REPOSITORY_UNAVAILABLE",
            "repository is unavailable",
        ) from error
    if not candidate.is_dir():
        raise DashboardMainError(
            "REPOSITORY_UNAVAILABLE",
            "repository is unavailable",
        )
    return RepoContext.discover(candidate)


def _startup_payload(model, port, source_head_sha, reused):
    return {
        "status": "started",
        "host": HOST,
        "port": port,
        "url": "http://%s:%d" % (HOST, port),
        "repository_id": model.repository_id(),
        "source_head_sha": source_head_sha,
        "reused": reused,
    }


def execute(args):
    context = _canonical_repo(args.repo)
    if args.port is not None and not 0 <= args.port <= 65535:
        raise DashboardMainError(
            "INVALID_PORT",
            "port must be between 0 and 65535",
        )
    store = ControlStore.for_repo(context)
    model = DashboardReadModel(context, store)
    model.health()
    source_head_sha = model.source_head_sha()
    selected_port = args.port
    if selected_port is None:
        selected_port = _default_port(context.common_dir)
        if _probe_existing(selected_port, model.repository_id()):
            payload = _startup_payload(
                model,
                selected_port,
                source_head_sha,
                reused=True,
            )
            _emit(payload, sys.stdout)
            if not args.no_open:
                webbrowser.open(payload["url"])
            return 0

    assets = context.root / "apps" / "dashboard"
    try:
        assets = assets.resolve(strict=True)
    except OSError as error:
        raise DashboardMainError(
            "ASSET_UNAVAILABLE",
            "dashboard assets are unavailable",
        ) from error
    if not assets.is_dir():
        raise DashboardMainError(
            "ASSET_UNAVAILABLE",
            "dashboard assets are unavailable",
        )
    try:
        server = create_server(model, assets, host=HOST, port=selected_port)
    except OSError as error:
        raise DashboardMainError(
            "PORT_IN_USE",
            "dashboard port is already used by another service",
        ) from error
    actual_port = server.server_address[1]
    payload = _startup_payload(
        model,
        actual_port,
        source_head_sha,
        reused=False,
    )

    def stop_handler(signum, frame):
        raise KeyboardInterrupt

    previous_handlers = {
        signum: signal.signal(signum, stop_handler)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        _emit(payload, sys.stdout)
        if not args.no_open:
            webbrowser.open(payload["url"])
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
    return 0


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        return execute(args)
    except DashboardMainError as error:
        _emit(
            {"error": {"code": error.code, "message": str(error)}},
            sys.stderr,
        )
        return 2
    except DashboardUnavailableError as error:
        _emit(
            {
                "error": {
                    "code": error.code,
                    "message": "dashboard prerequisites are unavailable",
                }
            },
            sys.stderr,
        )
        return 3
    except TeamControlError:
        _emit(
            {
                "error": {
                    "code": "DASHBOARD_START_FAILED",
                    "message": "dashboard could not be started",
                }
            },
            sys.stderr,
        )
        return 3
    except Exception:
        _emit(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "dashboard could not be started",
                }
            },
            sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
