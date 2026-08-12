import json
import re
import secrets
import stat
import hmac
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .dashboard_read_model import (
    DashboardError,
    DashboardInputError,
    DashboardNotFoundError,
    DashboardUnavailableError,
)
from .intents import IntentService, safe_intent_summary
from .service import ControlPlane
from .errors import ContractError, ReconciliationError


STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
TASK_PATH_RE = re.compile(r"^/api/tasks/([A-Za-z0-9][A-Za-z0-9._-]*)$")
TASK_EVENTS_RE = re.compile(
    r"^/api/tasks/([A-Za-z0-9][A-Za-z0-9._-]*)/events$"
)
TASK_EVIDENCE_RE = re.compile(
    r"^/api/tasks/([A-Za-z0-9][A-Za-z0-9._-]*)/evidence$"
)
MAX_INTENT_BODY_BYTES = 8192


class RouteNotFoundError(DashboardError):
    code = "NOT_FOUND"


def success_envelope(model, data):
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_head_sha": model.source_head_sha(),
        "data": data,
    }


def create_server(model, assets_dir, host="127.0.0.1", port=0):
    if host != "127.0.0.1":
        raise ValueError("dashboard host must be 127.0.0.1")
    assets = Path(assets_dir).resolve(strict=True)
    handler = make_handler(model, assets, secrets.token_urlsafe(32))
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def make_handler(model, assets_dir, intent_token):
    repository_id = model.repository_id()
    intent_service = IntentService(
        model.context, model.store, ControlPlane(model.context, model.store)
    )

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "TeamDashboard/1"
        sys_version = ""

        def log_message(self, format, *args):
            return

        def _allowed_hosts(self):
            port = self.server.server_address[1]
            return {"localhost:%d" % port, "127.0.0.1:%d" % port}

        def _validate_host(self, head_only=False):
            hosts = self.headers.get_all("Host", failobj=[])
            if len(hosts) != 1 or hosts[0] not in self._allowed_hosts():
                self._send_error(
                    400,
                    "HOST_REJECTED",
                    "Host is not allowed",
                    head_only=head_only,
                )
                return False
            return True

        def _validate_origin(self, required=False, head_only=False):
            origins = self.headers.get_all("Origin", failobj=[])
            if len(origins) > 1:
                self._send_error(
                    403,
                    "ORIGIN_REJECTED",
                    "Origin is not allowed",
                    head_only=head_only,
                )
                return False
            origin = origins[0] if origins else None
            if origin is None and not required:
                return True
            allowed = {"http://" + host for host in self._allowed_hosts()}
            if origin not in allowed:
                self._send_error(
                    403,
                    "ORIGIN_REJECTED",
                    "Origin is not allowed",
                    head_only=head_only,
                )
                return False
            return True

        @staticmethod
        def _json_bytes(payload):
            return json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")

        def _send_bytes(self, status, body, content_type, head_only=False, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Team-Repository-ID", repository_id)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head_only and body:
                self.wfile.write(body)

        def _send_json(self, status, payload, head_only=False, extra=None):
            self._send_bytes(
                status,
                self._json_bytes(payload),
                "application/json; charset=utf-8",
                head_only=head_only,
                extra=extra,
            )

        def _error_payload(self, code, message):
            try:
                source_head_sha = model.source_head_sha()
            except Exception:
                source_head_sha = None
            return {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_head_sha": source_head_sha,
                "error": {"code": code, "message": message},
            }

        def _send_error(self, status, code, message, head_only=False):
            self._send_json(
                status,
                self._error_payload(code, message),
                head_only=head_only,
            )

        @staticmethod
        def _single(query, name):
            values = query.get(name)
            if values is None:
                return None
            if len(values) != 1 or values[0] == "":
                raise DashboardInputError("query parameter is invalid")
            return values[0]

        def _query(self, allowed):
            split = urlsplit(self.path)
            try:
                query = parse_qs(
                    split.query,
                    keep_blank_values=True,
                    max_num_fields=20,
                )
            except ValueError as error:
                raise DashboardInputError("query is too large") from error
            unknown = set(query) - set(allowed)
            if unknown:
                raise DashboardInputError("unknown query parameter")
            return query

        def _api_data(self, path):
            if path == "/api/session":
                self._query(())
                return {"intent_token": intent_token}
            if path == "/api/health":
                self._query(())
                return model.health()
            if path == "/api/project":
                self._query(())
                return model.project()
            if path == "/api/tasks":
                query = self._query(("limit", "offset", "state", "risk", "attention", "q"))
                pagination = {
                    key: value
                    for key, value in query.items()
                    if key in ("limit", "offset")
                }
                return model.tasks(
                    pagination,
                    state=self._single(query, "state"),
                    risk=self._single(query, "risk"),
                    attention=self._single(query, "attention"),
                    search=self._single(query, "q"),
                )
            if path == "/api/approvals":
                query = self._query(("limit", "offset"))
                return model.approvals(query)
            matched = TASK_EVENTS_RE.fullmatch(path)
            if matched:
                query = self._query(("limit", "offset"))
                return model.events(matched.group(1), query)
            matched = TASK_EVIDENCE_RE.fullmatch(path)
            if matched:
                query = self._query(("limit", "offset"))
                return model.evidence(matched.group(1), query)
            matched = TASK_PATH_RE.fullmatch(path)
            if matched:
                self._query(())
                return model.task(matched.group(1))
            raise RouteNotFoundError("route was not found")

        def _intent_request(self):
            content_types = self.headers.get_all("Content-Type", failobj=[])
            if len(content_types) != 1 or content_types[0] != "application/json":
                self._send_error(415, "CONTENT_TYPE_REJECTED", "Content-Type is not allowed")
                return None
            tokens = self.headers.get_all("X-Team-Intent-Token", failobj=[])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0], intent_token):
                self._send_error(403, "TOKEN_REJECTED", "Intent token is not allowed")
                return None
            lengths = self.headers.get_all("Content-Length", failobj=[])
            if len(lengths) != 1 or not lengths[0].isdigit():
                self._send_error(400, "CONTENT_LENGTH_REJECTED", "Content-Length is invalid")
                return None
            length = int(lengths[0])
            if length > MAX_INTENT_BODY_BYTES:
                self._send_error(413, "BODY_TOO_LARGE", "Intent request is too large")
                return None
            try:
                raw = self.rfile.read(length)
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                self._send_error(400, "INVALID_JSON", "Intent request must be JSON")
                return None
            if type(value) is not dict:
                self._send_error(400, "INVALID_JSON", "Intent request must be an object")
                return None
            return value

        def _submit_intent(self):
            if not self._validate_host() or not self._validate_origin(required=True):
                return
            if self._safe_path() != "/api/intents" or urlsplit(self.path).query:
                self._send_error(404, "NOT_FOUND", "Route was not found")
                return
            request = self._intent_request()
            if request is None:
                return
            try:
                intent = intent_service.submit(request)
            except ContractError as error:
                self._send_error(400, "INVALID_INTENT", str(error))
            except ReconciliationError as error:
                self._send_error(409, "INTENT_CONFLICT", str(error))
            except KeyError:
                self._send_error(404, "TASK_NOT_FOUND", "Task was not found")
            except Exception:
                self._send_error(500, "INTERNAL_ERROR", "Intent request failed")
            else:
                self._send_json(
                    202, success_envelope(model, safe_intent_summary(intent))
                )

        def _safe_path(self):
            raw = urlsplit(self.path).path
            lowered = raw.lower()
            if (
                "%" in raw
                or "\\" in raw
                or "%2f" in lowered
                or "%5c" in lowered
            ):
                return None
            decoded = unquote(raw)
            if ".." in decoded.split("/"):
                return None
            return decoded

        def _static(self, path, head_only):
            mapping = STATIC_FILES.get(path)
            if mapping is None:
                return False
            filename, content_type = mapping
            candidate = assets_dir / filename
            try:
                info = candidate.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise OSError("asset is not a regular file")
                if candidate.resolve(strict=True).parent != assets_dir:
                    raise OSError("asset escapes assets directory")
                body = candidate.read_bytes()
            except OSError:
                self._send_error(
                    503,
                    "ASSET_UNAVAILABLE",
                    "Asset is unavailable",
                    head_only=head_only,
                )
                return True
            self._send_bytes(200, body, content_type, head_only=head_only)
            return True

        def _read(self, head_only=False):
            if not self._validate_host(head_only=head_only) or not self._validate_origin(
                head_only=head_only
            ):
                return
            path = self._safe_path()
            if path is None:
                self._send_error(404, "NOT_FOUND", "Route was not found", head_only)
                return
            if self._static(path, head_only):
                return
            if not path.startswith("/api/"):
                self._send_error(404, "NOT_FOUND", "Route was not found", head_only)
                return
            try:
                data = self._api_data(path)
                self._send_json(
                    200,
                    success_envelope(model, data),
                    head_only=head_only,
                )
            except DashboardNotFoundError as error:
                self._send_error(404, error.code, str(error), head_only)
            except DashboardInputError as error:
                self._send_error(400, error.code, str(error), head_only)
            except DashboardUnavailableError as error:
                self._send_error(503, error.code, str(error), head_only)
            except RouteNotFoundError as error:
                self._send_error(404, error.code, str(error), head_only)
            except DashboardError as error:
                self._send_error(500, error.code, "Dashboard request failed", head_only)
            except Exception:
                self._send_error(500, "INTERNAL_ERROR", "Dashboard request failed", head_only)

        def do_GET(self):
            self._read(head_only=False)

        def do_HEAD(self):
            self._read(head_only=True)

        def do_OPTIONS(self):
            if not self._validate_host() or not self._validate_origin(required=True):
                return
            path = self._safe_path()
            if path is None:
                self._send_error(404, "NOT_FOUND", "Route was not found")
                return
            try:
                if path in STATIC_FILES:
                    self._query(())
                elif path in ("/api/health", "/api/project", "/api/session"):
                    self._query(())
                elif path == "/api/tasks":
                    self._query(("limit", "offset", "state", "risk", "attention", "q"))
                elif path == "/api/approvals":
                    self._query(("limit", "offset"))
                elif TASK_EVENTS_RE.fullmatch(path) or TASK_EVIDENCE_RE.fullmatch(path):
                    self._query(("limit", "offset"))
                elif TASK_PATH_RE.fullmatch(path):
                    self._query(())
                else:
                    self._send_error(404, "NOT_FOUND", "Route was not found")
                    return
            except DashboardInputError as error:
                self._send_error(400, error.code, str(error))
                return
            self._send_bytes(
                204,
                b"",
                "application/json; charset=utf-8",
                extra={"Allow": "GET, HEAD, OPTIONS"},
            )

        def do_POST(self):
            if self._safe_path() == "/api/intents":
                self._submit_intent()
            else:
                self._reject_write()

        def _reject_write(self):
            if not self._validate_host() or not self._validate_origin():
                return
            self._send_error(405, "READ_ONLY", "Dashboard is read-only")

        do_PUT = _reject_write
        do_PATCH = _reject_write
        do_DELETE = _reject_write
        do_CONNECT = _reject_write
        do_TRACE = _reject_write

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._reject_write
            raise AttributeError(name)

    return DashboardHandler
