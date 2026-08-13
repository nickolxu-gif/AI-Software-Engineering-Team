import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from team_control.dashboard_read_model import (
    DashboardReadModel,
    DashboardUnavailableError,
)
from team_control.dashboard_main import (
    DashboardMainError,
    _default_port,
    _probe_existing,
)
from team_control.dashboard_server import create_server
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repo = make_repo(root / "repo")
        context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(context)
        self.store.initialize()
        self.model = DashboardReadModel(context, self.store)
        assets = root / "assets"
        assets.mkdir()
        (assets / "index.html").write_text("<!doctype html><title>Team</title>")
        (assets / "styles.css").write_text("body { color: #111; }")
        (assets / "app.js").write_text("document.body.dataset.ready = '1';")
        self.server = create_server(self.model, assets, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def port(self):
        return self.server.server_address[1]

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {"Host": "127.0.0.1:%d" % self.port}
        request_headers.update(headers or {})
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        payload = json.loads(body) if body else None
        connection.close()
        return response, payload, body

    def request_json(self, method, path, payload, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {
            "Host": "127.0.0.1:%d" % self.port,
            "Origin": "http://127.0.0.1:%d" % self.port,
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        connection.request(method, path, body=json.dumps(payload), headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        decoded = json.loads(body) if body else None
        connection.close()
        return response, decoded, body

    def database_digest(self):
        digest = hashlib.sha256()
        for suffix in ("", "-wal"):
            candidate = Path(str(self.store.path) + suffix)
            if candidate.exists():
                digest.update(candidate.read_bytes())
        return digest.hexdigest()

    def session_token(self):
        origin = "http://127.0.0.1:%d" % self.port
        response, payload, body = self.request(
            "GET", "/api/session", headers={"Origin": origin}
        )
        self.assertEqual(response.status, 200)
        return payload["data"]["intent_token"]

    @staticmethod
    def task_intake_request():
        return {
            "title": "Create a task request",
            "objective": "Let Codex prepare a seven-question dispatch",
            "context": "No browser Git execution",
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174999",
        }

    def raw_request(self, request):
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            connection.sendall(request)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            connection.close()
        response = b"".join(chunks)
        head, body = response.split(b"\r\n\r\n", 1)
        status = int(head.splitlines()[0].split()[1])
        return status, head, body

    def test_get_health_has_envelope_and_security_headers(self):
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertRegex(payload["generated_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(payload["source_head_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn(
            "frame-ancestors 'none'",
            response.getheader("Content-Security-Policy"),
        )
        self.assertRegex(response.getheader("X-Team-Repository-ID"), r"^[0-9a-f]{64}$")

    def test_health_reports_schema_migration_required_for_missing_intents_table(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE intents")
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_MIGRATION_REQUIRED")

    def test_health_reports_schema_migration_required_for_missing_task_intake_table(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_requests")
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_MIGRATION_REQUIRED")

    def test_health_reports_schema_migration_required_for_missing_task_intake_handling_table(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_MIGRATION_REQUIRED")

    def test_health_reports_schema_unsupported_for_incompatible_task_intake_handling_table(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
            connection.execute(
                "CREATE TABLE task_intake_handlings (intake_id TEXT PRIMARY KEY)"
            )
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_UNSUPPORTED")

    def test_business_write_methods_are_rejected_without_side_effects(self):
        before = self.database_digest()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response, payload, body = self.request(method, "/api/tasks")
                self.assertEqual(response.status, 405)
                self.assertEqual(payload["error"]["code"], "READ_ONLY")
        self.assertEqual(self.database_digest(), before)

    def test_dashboard_assets_expose_only_bounded_intent_controls(self):
        app = (PROJECT_ROOT / "apps" / "dashboard" / "app.js").read_text(
            encoding="utf-8"
        )
        page = (PROJECT_ROOT / "apps" / "dashboard" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("受控意图", page)
        self.assertIn("/api/session", app)
        self.assertIn("/api/intents", app)
        self.assertIn("/api/task-intakes", app)
        self.assertIn("method: 'POST'", app)
        self.assertIn("待处理意图", app)
        self.assertIn("提交新工程需求", app)
        for action in ("PAUSE_REQUEST", "RESUME_REQUEST", "APPROVAL_REQUEST"):
            self.assertIn(action, app)
        for forbidden in (
            "process-intent", "process-pending-intents", "git merge",
            "git push", "nonce", "localStorage",
        ):
            self.assertNotIn(forbidden, app)

    def test_intent_session_and_submission_require_loopback_origin_and_token(self):
        control = ControlPlane(RepoContext.discover(self.repo), self.store)
        task = control.create_task("20260812-101", "Intent", "Submit", "L2")
        control.transition(task["dispatch_id"], "DISPATCHED", "start")
        control.transition(task["dispatch_id"], "IN_PROGRESS", "start")
        origin = "http://127.0.0.1:%d" % self.port

        response, payload, body = self.request(
            "GET", "/api/session", headers={"Origin": origin}
        )
        self.assertEqual(response.status, 200)
        token = payload["data"]["intent_token"]
        self.assertIsInstance(token, str)
        request = {
            "dispatch_id": task["dispatch_id"],
            "action": "PAUSE_REQUEST",
            "target_sha": task["current_head_sha"],
            "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
            "parameters": {},
        }
        response, payload, body = self.request_json("POST", "/api/intents", request)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "TOKEN_REJECTED")
        response, payload, body = self.request_json(
            "POST", "/api/intents", request,
            headers={"X-Team-Intent-Token": token},
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(payload["data"]["status"], "PENDING")
        self.assertEqual(set(payload["data"]), {
            "intent_id", "dispatch_id", "action", "target_sha", "status",
            "result_code", "created_at", "updated_at",
        })
        self.assertNotIn("confirmation", body.decode("utf-8"))

    def test_task_intake_requires_token_and_returns_safe_summary(self):
        request = self.task_intake_request()
        before = self.database_digest()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", request
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "TOKEN_REJECTED")
        self.assertEqual(self.database_digest(), before)

        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", request,
            headers={"X-Team-Intent-Token": self.session_token()},
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(set(payload["data"]), {
            "intake_id", "title", "objective", "status", "result_code",
            "created_at", "updated_at",
        })
        self.assertEqual(payload["data"]["status"], "PENDING")
        self.assertNotIn("context", body.decode("utf-8"))
        self.assertNotIn("request_hash", body.decode("utf-8"))
        response, project, body = self.request("GET", "/api/project")
        self.assertEqual(response.status, 200)
        self.assertEqual(project["data"]["counts"]["pending_task_intakes"], 1)

    def test_task_intake_wrong_origin_has_no_side_effect(self):
        before = self.database_digest()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", self.task_intake_request(),
            headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "ORIGIN_REJECTED")
        self.assertEqual(self.database_digest(), before)

    def test_dashboard_has_no_task_intake_acknowledgement_route(self):
        request = self.task_intake_request()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", request,
            headers={"X-Team-Intent-Token": self.session_token()},
        )
        self.assertEqual(response.status, 202)
        before = self.database_digest()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes/%s/acknowledge" % payload["data"]["intake_id"],
            request, headers={"X-Team-Intent-Token": self.session_token()},
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["error"]["code"], "READ_ONLY")
        self.assertEqual(self.database_digest(), before)

    def test_task_intake_rejects_wrong_content_type_and_oversized_body(self):
        origin = "http://127.0.0.1:%d" % self.port
        token = self.session_token()
        before = self.database_digest()
        response, payload, body = self.request(
            "POST", "/api/task-intakes",
            headers={"Origin": origin, "X-Team-Intent-Token": token},
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(payload["error"]["code"], "CONTENT_TYPE_REJECTED")
        self.assertEqual(self.database_digest(), before)

        oversized_length = 8193
        status, headers, body = self.raw_request(
            (
                "POST /api/task-intakes HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                "Origin: %s\r\nContent-Type: application/json\r\n"
                "X-Team-Intent-Token: %s\r\nContent-Length: %d\r\n\r\n"
                % (self.port, origin, token, oversized_length)
            ).encode("ascii")
        )
        self.assertEqual(status, 413)
        self.assertIn(b"BODY_TOO_LARGE", body)
        self.assertEqual(self.database_digest(), before)

    def test_task_intake_reports_schema_migration_required_without_side_effect(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
        before = self.database_digest()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", self.task_intake_request(),
            headers={"X-Team-Intent-Token": self.session_token()},
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_MIGRATION_REQUIRED")
        self.assertEqual(self.database_digest(), before)

    def test_task_intake_reports_schema_unsupported_without_side_effect(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_handlings")
            connection.execute(
                "CREATE TABLE task_intake_handlings (intake_id TEXT PRIMARY KEY)"
            )
        before = self.database_digest()
        response, payload, body = self.request_json(
            "POST", "/api/task-intakes", self.task_intake_request(),
            headers={"X-Team-Intent-Token": self.session_token()},
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_UNSUPPORTED")
        self.assertEqual(self.database_digest(), before)

    def test_health_rejects_task_intake_request_schema_missing_private_columns(self):
        with self.store.mutation() as connection:
            connection.execute("DROP TABLE task_intake_requests")
            connection.execute(
                """CREATE TABLE task_intake_requests (
                       intake_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                       objective TEXT NOT NULL, status TEXT NOT NULL,
                       result_code TEXT NOT NULL, created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
        response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "SCHEMA_UNSUPPORTED")

    def test_intent_submission_rejects_oversized_or_malformed_requests(self):
        origin = "http://127.0.0.1:%d" % self.port
        response, payload, body = self.request(
            "GET", "/api/session", headers={"Origin": origin}
        )
        token = payload["data"]["intent_token"]
        oversized_length = 8193
        status, headers, body = self.raw_request(
            (
                "POST /api/intents HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                "Origin: %s\r\nContent-Type: application/json\r\n"
                "X-Team-Intent-Token: %s\r\nContent-Length: %d\r\n\r\n"
                % (self.port, origin, token, oversized_length)
            ).encode("ascii")
        )
        self.assertEqual(status, 413)
        self.assertIn(b"BODY_TOO_LARGE", body)

    def test_origin_and_host_policy(self):
        response, payload, body = self.request(
            "GET",
            "/api/health",
            headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "ORIGIN_REJECTED")
        response, payload, body = self.request(
            "GET",
            "/api/health",
            headers={"Host": "example.invalid"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "HOST_REJECTED")

    def test_static_map_rejects_unknown_and_encoded_paths(self):
        for path in ("/secret", "/../handoff.md", "/%2e%2e/handoff.md", "/a%2fb"):
            with self.subTest(path=path):
                response, payload, body = self.request("GET", path)
                self.assertEqual(response.status, 404)

    def test_head_and_options_are_read_only(self):
        response, payload, body = self.request("HEAD", "/api/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"")
        origin = "http://127.0.0.1:%d" % self.port
        response, payload, body = self.request(
            "OPTIONS",
            "/api/health",
            headers={"Origin": origin},
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD, OPTIONS")

    def test_task_query_rejects_unknown_parameters(self):
        response, payload, body = self.request("GET", "/api/tasks?sort=title")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_REQUEST")

    def test_all_collection_routes_and_missing_task_contract(self):
        for path in ("/api/project", "/api/tasks", "/api/approvals"):
            with self.subTest(path=path):
                response, payload, body = self.request("GET", path)
                self.assertEqual(response.status, 200)
                self.assertIn("data", payload)
        for path in (
            "/api/tasks/missing",
            "/api/tasks/missing/events",
            "/api/tasks/missing/evidence",
        ):
            with self.subTest(path=path):
                response, payload, body = self.request("GET", path)
                self.assertEqual(response.status, 404)
                self.assertEqual(payload["error"]["code"], "TASK_NOT_FOUND")
        response, payload, body = self.request("GET", "/api/not-a-route")
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_duplicate_and_oversized_queries_fail_closed(self):
        response, payload, body = self.request(
            "GET",
            "/api/tasks?state=PLANNED&state=BLOCKED",
        )
        self.assertEqual(response.status, 400)
        query = "&".join("q=x%d" % index for index in range(21))
        response, payload, body = self.request("GET", "/api/tasks?" + query)
        self.assertEqual(response.status, 400)

    def test_other_unsafe_methods_are_read_only(self):
        for method in ("CONNECT", "TRACE", "PROPFIND", "BREW"):
            with self.subTest(method=method):
                response, payload, body = self.request(method, "/api/health")
                self.assertEqual(response.status, 405)
                self.assertEqual(payload["error"]["code"], "READ_ONLY")

    def test_duplicate_authority_headers_are_rejected(self):
        valid_host = "127.0.0.1:%d" % self.port
        status, head, body = self.raw_request(
            (
                "GET /api/health HTTP/1.0\r\n"
                "Host: %s\r\nHost: example.invalid\r\n\r\n" % valid_host
            ).encode("ascii")
        )
        self.assertEqual(status, 400)
        status, head, body = self.raw_request(
            (
                "GET /api/health HTTP/1.0\r\nHost: %s\r\n"
                "Origin: http://%s\r\nOrigin: https://example.invalid\r\n\r\n"
                % (valid_host, valid_host)
            ).encode("ascii")
        )
        self.assertEqual(status, 403)

    def test_head_errors_never_emit_a_body(self):
        valid_host = "127.0.0.1:%d" % self.port
        for path, host in (("/secret", valid_host), ("/api/health", "bad.invalid")):
            with self.subTest(path=path, host=host):
                status, head, body = self.raw_request(
                    (
                        "HEAD %s HTTP/1.0\r\nHost: %s\r\n\r\n"
                        % (path, host)
                    ).encode("ascii")
                )
                self.assertEqual(body, b"")

    def test_options_rejects_unknown_and_encoded_routes(self):
        origin = "http://127.0.0.1:%d" % self.port
        for path in ("/unknown", "/%2e%2e/secret"):
            with self.subTest(path=path):
                response, payload, body = self.request(
                    "OPTIONS",
                    path,
                    headers={"Origin": origin},
                )
                self.assertEqual(response.status, 404)

    def test_all_seven_api_routes_have_success_contracts(self):
        control = ControlPlane(RepoContext.discover(self.repo), self.store)
        task = control.create_task("20260809-100", "HTTP", "Contract", "L1")
        paths = (
            "/api/health",
            "/api/project",
            "/api/tasks",
            "/api/tasks/%s" % task["dispatch_id"],
            "/api/tasks/%s/events" % task["dispatch_id"],
            "/api/tasks/%s/evidence" % task["dispatch_id"],
            "/api/approvals",
        )
        for path in paths:
            with self.subTest(path=path):
                response, payload, body = self.request("GET", path)
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["schema_version"], 1)
                self.assertIn("data", payload)

    def test_unavailable_and_unexpected_errors_are_mapped_and_sanitized(self):
        with patch.object(
            self.model,
            "health",
            side_effect=DashboardUnavailableError(
                "database unavailable",
                code="DATABASE_UNAVAILABLE",
            ),
        ):
            response, payload, body = self.request("GET", "/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "DATABASE_UNAVAILABLE")
        with patch.object(
            self.model,
            "project",
            side_effect=RuntimeError("secret-internal-detail"),
        ):
            response, payload, body = self.request("GET", "/api/project")
        self.assertEqual(response.status, 500)
        self.assertEqual(payload["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret-internal-detail", body.decode("utf-8"))


class DashboardLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repo = make_repo(root / "repo")
        shutil.copytree(PROJECT_ROOT / "apps", self.repo / "apps")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.model = DashboardReadModel(self.context, self.store)

    def start_dashboard(self, *arguments, repo=None):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "team_control.dashboard_main",
                "--repo",
                str(repo or self.repo),
                *arguments,
            ],
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

    def test_dashboard_main_emits_one_structured_startup_line(self):
        process = self.start_dashboard("--no-open", "--port", "0")
        self.addCleanup(lambda: process.poll() is None and process.kill())
        payload = json.loads(process.stdout.readline())
        self.assertEqual(payload["status"], "started")
        self.assertEqual(payload["host"], "127.0.0.1")
        self.assertRegex(payload["url"], r"^http://127\.0\.0\.1:\d+$")
        self.assertNotIn("database", payload)
        connection = http.client.HTTPConnection(
            "127.0.0.1", payload["port"], timeout=5
        )
        connection.request(
            "GET",
            "/api/health",
            headers={"Host": "127.0.0.1:%d" % payload["port"]},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()
        process.terminate()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(process.stdout.read(), "")
        self.assertEqual(process.stderr.read(), "")
        process.stdout.close()
        process.stderr.close()

    def test_non_loopback_host_argument_is_rejected(self):
        process = self.start_dashboard("--host", "0.0.0.0", "--no-open")
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENTS")

    def test_missing_database_is_not_initialized(self):
        missing_repo = make_repo(Path(self.temporary.name) / "missing")
        shutil.copytree(PROJECT_ROOT / "apps", missing_repo / "apps")
        context = RepoContext.discover(missing_repo)
        database = context.common_dir / "team" / "runtime" / "team.db"
        process = self.start_dashboard("--no-open", "--port", "0", repo=missing_repo)
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertEqual(payload["error"]["code"], "DATABASE_UNAVAILABLE")
        self.assertFalse(database.exists())

    def test_repository_wrapper_rejects_repo_override(self):
        wrapper = PROJECT_ROOT / "scripts" / "open-team-dashboard"
        result = subprocess.run(
            [str(wrapper), "--repo", str(self.repo)],
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("WRAPPER_REPO_OVERRIDE", result.stderr)

    def test_default_port_and_existing_service_identity_are_deterministic(self):
        port = _default_port(self.context.common_dir)
        self.assertGreaterEqual(port, 49152)
        self.assertLessEqual(port, 65534)
        self.assertEqual(port, _default_port(self.context.common_dir))
        server = create_server(
            self.model,
            self.repo / "apps" / "dashboard",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            actual_port = server.server_address[1]
            self.assertTrue(
                _probe_existing(actual_port, self.model.repository_id())
            )
            with self.assertRaises(DashboardMainError) as caught:
                _probe_existing(actual_port, "0" * 64)
            self.assertEqual(caught.exception.code, "PORT_IN_USE")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
