import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from team_control.dashboard_read_model import DashboardReadModel
from team_control.dashboard_server import create_server
from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo


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

    def database_digest(self):
        digest = hashlib.sha256()
        for suffix in ("", "-wal"):
            candidate = Path(str(self.store.path) + suffix)
            if candidate.exists():
                digest.update(candidate.read_bytes())
        return digest.hexdigest()

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

    def test_business_write_methods_are_rejected_without_side_effects(self):
        before = self.database_digest()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response, payload, body = self.request(method, "/api/tasks")
                self.assertEqual(response.status, 405)
                self.assertEqual(payload["error"]["code"], "READ_ONLY")
        self.assertEqual(self.database_digest(), before)

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
        for method in ("CONNECT", "TRACE"):
            with self.subTest(method=method):
                response, payload, body = self.request(method, "/api/health")
                self.assertEqual(response.status, 405)
                self.assertEqual(payload["error"]["code"], "READ_ONLY")


if __name__ == "__main__":
    unittest.main()
