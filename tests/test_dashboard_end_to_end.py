import hashlib
import http.client
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from team_control.dashboard_read_model import DashboardReadModel
from team_control.dashboard_server import create_server
from team_control.git_context import RepoContext
from team_control.project_registry import ProjectRegistryService
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def readonly_git(arguments, repo):
    return subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "maintenance.auto=false",
            *arguments,
        ],
        cwd=str(repo),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": os.defpath,
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


def repository_snapshot(repo, database):
    index = repo / ".git" / "index"
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    return {
        "database_sha256": file_sha256(database),
        "wal_sha256": hashlib.sha256(
            wal.read_bytes() if wal.exists() else b""
        ).hexdigest(),
        "index_sha256": file_sha256(index),
        "index_mtime_ns": index.stat().st_mtime_ns,
        "head": readonly_git(["rev-parse", "HEAD"], repo).stdout.strip(),
        "refs": readonly_git(["show-ref"], repo).stdout,
        "status": readonly_git(
            [
                "status",
                "--porcelain=v1",
                "--ignore-submodules=all",
            ],
            repo,
        ).stdout,
        "sidecars": {
            "wal_exists": wal.exists(),
            "wal_size": wal.stat().st_size if wal.exists() else 0,
            "shm": ({
                "exists": True,
                "is_symlink": shm.is_symlink(),
                "is_regular": stat.S_ISREG(shm.lstat().st_mode),
                "size": shm.lstat().st_size,
            }
            if shm.exists() or shm.is_symlink()
            else {"exists": False, "is_symlink": False, "is_regular": False, "size": 0}),
        },
    }


def business_snapshot(snapshot):
    return {key: value for key, value in snapshot.items() if key != "sidecars"}


class DashboardEndToEndTests(unittest.TestCase):
    def request(self, method, path):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            method,
            path,
            headers={"Host": "127.0.0.1:%d" % port},
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_real_repository_vertical_slice_has_no_business_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            store.initialize()
            control = ControlPlane(context, store)

            normal = control.create_task(
                "20260809-201", "Normal", "Normal task", "L1"
            )
            blocked = control.create_task(
                "20260809-202", "Blocked", "Blocked task", "L3"
            )
            pending = control.create_task(
                "20260809-203", "Approval", "Approval task", "L3"
            )
            store.add_blocker(
                blocked["dispatch_id"],
                "dependency unavailable",
                "Codex",
                "dependency restored",
            )
            control.request_approval(
                pending["dispatch_id"],
                "external_action",
                pending["current_head_sha"],
                {"scope": "end-to-end"},
                "dashboard-end-to-end-nonce",
                10,
            )
            unavailable_target = make_repo(Path(temporary) / "unavailable-target")
            ProjectRegistryService(context, store).register(
                "Unavailable target", unavailable_target
            )
            unavailable_database = (
                RepoContext.discover(unavailable_target).common_dir
                / "team" / "runtime" / "team.db"
            )
            unavailable_database.parent.mkdir(parents=True)
            unavailable_database.symlink_to(Path(temporary) / "missing-team.db")

            before = repository_snapshot(repo, store.path)
            model = DashboardReadModel(context, store)
            self.server = create_server(
                model,
                PROJECT_ROOT / "apps" / "dashboard",
                port=0,
            )
            thread = threading.Thread(target=self.server.serve_forever)
            thread.start()
            try:
                api_paths = (
                    "/api/health",
                    "/api/project",
                    "/api/projects",
                    "/api/tasks?limit=100&offset=0",
                    "/api/tasks/%s" % blocked["dispatch_id"],
                    "/api/tasks/%s/events?limit=100&offset=0"
                    % blocked["dispatch_id"],
                    "/api/tasks/%s/evidence?limit=100&offset=0"
                    % normal["dispatch_id"],
                    "/api/approvals?limit=100&offset=0",
                )
                payloads = {}
                for path in api_paths:
                    status, body = self.request("GET", path)
                    self.assertEqual(status, 200, path)
                    payloads[path] = json.loads(body)

                project = payloads["/api/project"]["data"]
                projects = payloads["/api/projects"]["data"]
                self.assertEqual(projects["count"], 1)
                self.assertEqual(
                    projects["items"][0]["control_status"], "UNAVAILABLE"
                )
                self.assertNotIn(str(unavailable_target), json.dumps(projects))
                leading = [
                    item["dispatch_id"]
                    for item in project["attention_items"][:2]
                ]
                self.assertEqual(
                    leading,
                    [pending["dispatch_id"], blocked["dispatch_id"]],
                )
                encoded = json.dumps(payloads, ensure_ascii=False)
                for forbidden in (
                    "request_hash",
                    "nonce_hash",
                    "idempotency_key",
                    "payload_json",
                    "report_json",
                    "report_path",
                ):
                    self.assertNotIn(forbidden, encoded)

                for path in ("/", "/styles.css", "/app.js"):
                    status, body = self.request("GET", path)
                    self.assertEqual(status, 200, path)
                    self.assertTrue(body, path)

                for method in (
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "CONNECT",
                    "TRACE",
                    "PROPFIND",
                ):
                    status, body = self.request(method, "/api/tasks")
                    self.assertEqual(status, 405, method)
                    self.assertEqual(json.loads(body)["error"]["code"], "READ_ONLY")

                with ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(
                        executor.map(
                            lambda path: self.request("GET", path),
                            ["/api/project", "/api/tasks", "/api/health"] * 4,
                        )
                    )
                self.assertTrue(all(status == 200 for status, _ in results))
            finally:
                self.server.shutdown()
                self.server.server_close()
                thread.join(timeout=5)
                after = repository_snapshot(repo, store.path)
                self.assertEqual(
                    business_snapshot(after),
                    business_snapshot(before),
                )
                self.assertFalse(after["sidecars"]["shm"]["is_symlink"])
                if after["sidecars"]["shm"]["exists"]:
                    self.assertTrue(after["sidecars"]["shm"]["is_regular"])
                if not before["sidecars"]["wal_exists"]:
                    self.assertEqual(after["sidecars"]["wal_size"], 0)


if __name__ == "__main__":
    unittest.main()
