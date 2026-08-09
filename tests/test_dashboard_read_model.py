import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from team_control.dashboard_read_model import (
    DashboardInputError,
    DashboardNotFoundError,
    DashboardReadModel,
    DashboardUnavailableError,
    parse_pagination,
)
from team_control.errors import GitStateError
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class DashboardReadModelTests(unittest.TestCase):
    def make_model(self, root):
        repo = make_repo(root / "repo")
        context = RepoContext.discover(repo)
        store = ControlStore.for_repo(context)
        store.initialize()
        return repo, store, DashboardReadModel(context, store)

    def test_parse_pagination_applies_defaults_and_caps(self):
        self.assertEqual(parse_pagination({}, 50, 100), (50, 0))
        self.assertEqual(
            parse_pagination({"limit": ["100"], "offset": ["25"]}, 50, 100),
            (100, 25),
        )
        for query in (
            {"limit": ["101"]},
            {"limit": ["-1"]},
            {"offset": ["10001"]},
            {"offset": ["text"]},
        ):
            with self.subTest(query=query):
                with self.assertRaises(DashboardInputError):
                    parse_pagination(query, 50, 100)

    def test_parse_pagination_rejects_ambiguous_shapes_with_stable_code(self):
        invalid_queries = (
            {"limit": ["10", "11"]},
            {"limit": True},
            {"limit": "10"},
            {"limit": [10]},
            {"unknown": ["1"]},
            None,
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises(DashboardInputError) as caught:
                    parse_pagination(query, 50, 100)
                self.assertEqual(caught.exception.code, "INVALID_PAGINATION")

    def test_non_empty_wal_requires_readable_regular_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            wal = Path(str(store.path) + "-wal")
            shm = Path(str(store.path) + "-shm")
            wal.write_bytes(b"active")
            if shm.exists():
                shm.unlink()
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "WAL_SIDECAR_UNAVAILABLE")

    def test_database_missing_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            store.path.unlink()
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "DATABASE_UNAVAILABLE")

    def test_database_and_sidecar_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in ("database", "wal", "shm"):
                with self.subTest(target=target):
                    case_root = root / target
                    case_root.mkdir()
                    repo, store, model = self.make_model(case_root)
                    candidate = (
                        store.path
                        if target == "database"
                        else Path(str(store.path) + "-" + target)
                    )
                    external = root / (target + "-external")
                    external.write_bytes(b"active")
                    if candidate.exists() or candidate.is_symlink():
                        candidate.unlink()
                    candidate.symlink_to(external)
                    expected = (
                        "DATABASE_UNAVAILABLE"
                        if target == "database"
                        else "WAL_SIDECAR_UNAVAILABLE"
                    )
                    with self.assertRaises(DashboardUnavailableError) as caught:
                        model.health()
                    self.assertEqual(caught.exception.code, expected)

    def test_preexisting_database_symlink_is_not_hidden_by_store_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            lexical_database = (
                model.context.common_dir / "team" / "runtime" / "team.db"
            )
            connection = sqlite3.connect(str(lexical_database))
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("PRAGMA journal_mode=DELETE")
            finally:
                connection.close()
            target = lexical_database.with_name("team-target.db")
            lexical_database.replace(target)
            lexical_database.symlink_to(target)
            resolved_store = ControlStore.for_repo(model.context)
            resolved_model = DashboardReadModel(model.context, resolved_store)
            with self.assertRaises(DashboardUnavailableError) as caught:
                resolved_model.health()
            self.assertEqual(caught.exception.code, "DATABASE_UNAVAILABLE")

    def test_active_wal_sidecars_must_be_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            wal = Path(str(store.path) + "-wal")
            shm = Path(str(store.path) + "-shm")
            wal.write_bytes(b"active")
            shm.write_bytes(b"locks")
            real_access = os.access

            def access(candidate, mode):
                if Path(candidate) == wal:
                    return False
                return real_access(candidate, mode)

            with patch(
                "team_control.dashboard_read_model.os.access",
                side_effect=access,
            ):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    model.health()
            self.assertEqual(caught.exception.code, "WAL_SIDECAR_UNAVAILABLE")

    def test_reader_may_create_a_regular_shm_coordination_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            expected = model._validate_storage_files()
            expected["wal"] = None
            expected["shm"] = None
            observed = dict(expected)
            observed["wal"] = (1, 3, stat.S_IFREG | 0o600, 0, 1)
            observed["shm"] = (1, 2, stat.S_IFREG | 0o600, 32768, 1)
            with patch.object(
                model,
                "_validate_storage_files",
                return_value=observed,
            ):
                model._verify_storage_identity(expected)
            observed["wal"] = (1, 3, stat.S_IFREG | 0o600, 1, 1)
            with patch.object(
                model,
                "_validate_storage_files",
                return_value=observed,
            ):
                with self.assertRaises(DashboardUnavailableError):
                    model._verify_storage_identity(expected)

    def test_snapshot_reports_real_sqlite_busy_as_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))

            @contextmanager
            def busy_connection():
                error = sqlite3.OperationalError("database is locked")
                raise error
                yield

            with patch.object(store, "read_connection", busy_connection):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    with model.snapshot():
                        pass
            self.assertEqual(caught.exception.code, "DATABASE_BUSY")

    def test_snapshot_does_not_misclassify_caller_sql_errors_as_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with self.assertRaises(sqlite3.OperationalError):
                with model.snapshot() as connection:
                    connection.execute("SELECT missing_column FROM tasks")

    def test_health_checks_git_instead_of_claiming_it_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with patch.object(
                model,
                "source_head_sha",
                side_effect=GitStateError("git failed"),
            ):
                with self.assertRaises(DashboardUnavailableError) as caught:
                    model.health()
            self.assertEqual(caught.exception.code, "GIT_UNAVAILABLE")

    def test_git_observation_ignores_parent_git_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, store, model = self.make_model(root)
            other = make_repo(root / "other")
            expected = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            hostile = {
                "GIT_DIR": str(other / ".git"),
                "GIT_WORK_TREE": str(other),
                "GIT_INDEX_FILE": str(other / ".git" / "index"),
                "PATH": str(root / "missing-bin"),
            }
            with patch.dict(os.environ, hostile):
                self.assertEqual(model.source_head_sha(), expected)
                self.assertEqual(os.environ["GIT_DIR"], hostile["GIT_DIR"])

    def test_git_observation_rejects_unregistered_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, store, model = self.make_model(root)
            other = make_repo(root / "other")
            with self.assertRaises(DashboardInputError):
                model._git(("rev-parse", "HEAD"), cwd=other)

    def test_git_observation_does_not_refresh_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            index = repo / ".git" / "index"
            before_hash = hashlib.sha256(index.read_bytes()).hexdigest()
            before_mtime = index.stat().st_mtime_ns
            observed = model.project()
            self.assertEqual(
                observed["head_sha"],
                run(["git", "rev-parse", "HEAD"], repo).stdout.strip(),
            )
            self.assertEqual(
                hashlib.sha256(index.read_bytes()).hexdigest(),
                before_hash,
            )
            self.assertEqual(index.stat().st_mtime_ns, before_mtime)

    def test_tasks_are_exception_first_and_field_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            control.create_task("20260809-010", "Normal", "Normal task", "L1")
            control.create_task("20260809-011", "Blocked", "Blocked task", "L2")
            store.transition("20260809-011", "DISPATCHED", "dispatch")
            store.transition("20260809-011", "IN_PROGRESS", "start")
            store.add_blocker(
                "20260809-011",
                "test blocker",
                "Codex",
                "fix test",
            )

            result = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search=None,
            )
            self.assertEqual(result["items"][0]["dispatch_id"], "20260809-011")
            self.assertEqual(
                set(result["items"][0]),
                {
                    "dispatch_id",
                    "title",
                    "objective",
                    "risk_level",
                    "state",
                    "effective_state",
                    "owner",
                    "agent",
                    "updated_at",
                    "current_head_sha",
                    "attention_reasons",
                },
            )

    def test_approvals_expose_only_the_public_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.create_task(
                "20260809-012",
                "Approval",
                "Approval task",
                "L2",
            )
            control.request_approval(
                task["dispatch_id"],
                "external_action",
                task["current_head_sha"],
                {"scope": "test"},
                "dashboard-test-nonce-000001",
                10,
            )
            item = model.approvals({})["items"][0]
            self.assertEqual(
                set(item),
                {
                    "approval_id",
                    "dispatch_id",
                    "action",
                    "target_sha",
                    "expires_at",
                    "consumed_at",
                    "status",
                },
            )
            task_item = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search=task["dispatch_id"],
            )["items"][0]
            self.assertEqual(
                task_item["effective_state"],
                "NEEDS_HUMAN_APPROVAL",
            )
            self.assertIn("PENDING_APPROVAL", task_item["attention_reasons"])
            project = model.project()
            self.assertEqual(
                set(project),
                {
                    "repository_name",
                    "branch",
                    "head_sha",
                    "remote_configured",
                    "worktree_count",
                    "health",
                    "counts",
                    "attention_items",
                },
            )
            self.assertEqual(project["counts"]["pending_approvals"], 1)

    def test_task_filters_pagination_and_search_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            control.create_task("20260809-020", "Alpha", "One", "L1")
            control.create_task("20260809-021", "Beta", "Two", "L2")
            page = model.tasks(
                {"limit": ["1"]},
                state=None,
                risk=None,
                attention=None,
                search=None,
            )
            self.assertEqual(len(page["items"]), 1)
            self.assertTrue(page["has_more"])
            injected = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search="' OR 1=1 --",
            )
            self.assertEqual(injected["items"], [])
            for filters in (
                {"state": "NOT_A_STATE"},
                {"risk": "L9"},
                {"attention": 1},
            ):
                arguments = {
                    "state": None,
                    "risk": None,
                    "attention": None,
                    "search": None,
                }
                arguments.update(filters)
                with self.subTest(filters=filters):
                    with self.assertRaises(DashboardInputError) as caught:
                        model.tasks({}, **arguments)
                    self.assertEqual(caught.exception.code, "INVALID_FILTER")

    def test_expired_offset_approval_is_not_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.create_task("20260809-022", "Expiry", "Offset", "L2")
            with store.mutation() as connection:
                connection.execute(
                    """INSERT INTO approvals VALUES (
                           ?, ?, ?, ?, ?, ?, ?, NULL, 'PENDING', ?
                       )""",
                    (
                        "approval-offset",
                        task["dispatch_id"],
                        "external_action",
                        task["current_head_sha"],
                        "a" * 64,
                        "b" * 64,
                        "2026-08-09T19:30:00+08:00",
                        "approval-offset-key",
                    ),
                )
            observed_now = datetime(
                2026,
                8,
                9,
                12,
                0,
                tzinfo=timezone.utc,
            )
            with patch.object(model, "_now", return_value=observed_now):
                item = model.tasks(
                    {},
                    state=None,
                    risk=None,
                    attention=None,
                    search=None,
                )["items"][0]
                self.assertEqual(item["effective_state"], "PLANNED")
                self.assertEqual(model.project()["counts"]["pending_approvals"], 0)

    def test_schema_validation_covers_task3_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with store.mutation() as connection:
                connection.execute("DROP TABLE agents")
                connection.execute(
                    """CREATE TABLE agents (
                           dispatch_id TEXT,
                           agent_id TEXT,
                           role TEXT,
                           state TEXT,
                           report_json TEXT,
                           updated_at TEXT
                       )"""
                )
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.health()
            self.assertEqual(caught.exception.code, "SCHEMA_UNSUPPORTED")

    def test_task_listing_observes_git_worktrees_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            control.create_task("20260809-023", "One", "One", "L1")
            control.create_task("20260809-024", "Two", "Two", "L2")
            with patch.object(model, "_git", wraps=model._git) as observed:
                model.tasks(
                    {},
                    state=None,
                    risk=None,
                    attention=None,
                    search=None,
                )
            worktree_calls = [
                call
                for call in observed.call_args_list
                if call.args[0] == ("worktree", "list", "--porcelain")
            ]
            self.assertEqual(len(worktree_calls), 1)

    def test_bounded_candidates_do_not_hide_an_old_blocked_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            rows = [
                (
                    "old-blocked",
                    1,
                    "Blocked",
                    "Needs attention",
                    "L3",
                    "BLOCKED",
                    head,
                    head,
                    "Codex",
                    "2020-01-01T00:00:00+00:00",
                    "2020-01-01T00:00:00+00:00",
                )
            ]
            rows.extend(
                (
                    "normal-%05d" % index,
                    1,
                    "Normal",
                    "Routine",
                    "L1",
                    "IN_PROGRESS",
                    head,
                    head,
                    "Codex",
                    "2030-01-01T00:00:00+00:00",
                    "2030-01-01T00:00:00+00:00",
                )
                for index in range(10101)
            )
            with store.mutation() as connection:
                connection.executemany(
                    """INSERT INTO tasks (
                           dispatch_id, schema_version, title, objective,
                           risk_level, state, task_base_sha, current_head_sha,
                           owner, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            page = model.tasks(
                {"limit": ["1"]},
                state=None,
                risk=None,
                attention=True,
                search=None,
            )
            self.assertEqual(page["items"][0]["dispatch_id"], "old-blocked")

    def test_valid_acceptance_uses_indexed_metadata_without_reading_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.start_write_task(
                "20260809-025",
                "Review",
                "Verify",
                "L2",
                "codex",
                "review",
            )
            report = Path(task["worktree_path"]) / "review.md"
            report.write_text("accepted\n", encoding="utf-8")
            store.add_review(
                task["dispatch_id"],
                "Claude",
                "ACCEPT",
                task["current_head_sha"],
                report,
            )
            self.assertTrue(model.task(task["dispatch_id"])["valid_acceptance"])
            report.write_text("tampered\n", encoding="utf-8")
            with patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("dashboard must not read report body"),
            ):
                self.assertTrue(
                    model.task(task["dispatch_id"])["valid_acceptance"]
                )

    def test_invalid_registered_worktree_is_not_hidden_by_normal_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            now = "2030-01-01T00:00:00+00:00"
            rows = [
                (
                    "normal-%05d" % index,
                    1,
                    "Normal",
                    "Routine",
                    "L1",
                    "IN_PROGRESS",
                    head,
                    head,
                    "Codex",
                    now,
                    now,
                )
                for index in range(10101)
            ]
            with store.mutation() as connection:
                connection.executemany(
                    """INSERT INTO tasks (
                           dispatch_id, schema_version, title, objective,
                           risk_level, state, task_base_sha, current_head_sha,
                           owner, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                connection.execute(
                    """INSERT INTO tasks (
                           dispatch_id, schema_version, title, objective,
                           risk_level, state, task_base_sha, current_head_sha,
                           owner, agent, slug, branch, worktree_path,
                           created_at, updated_at
                       ) VALUES (?, 1, ?, ?, 'L2', 'IN_PROGRESS', ?, ?, ?, ?,
                                 ?, ?, ?, ?, ?)""",
                    (
                        "missing-worktree",
                        "Missing",
                        "Needs attention",
                        head,
                        head,
                        "Codex",
                        "codex",
                        "missing",
                        "agent/codex/missing-worktree-missing",
                        str(
                            repo
                            / ".worktrees"
                            / "missing-worktree-codex-missing"
                        ),
                        "2020-01-01T00:00:00+00:00",
                        "2020-01-01T00:00:00+00:00",
                    ),
                )
            page = model.tasks(
                {"limit": ["1"]},
                state=None,
                risk=None,
                attention=True,
                search=None,
            )
            self.assertEqual(page["items"][0]["dispatch_id"], "missing-worktree")
            self.assertIn(
                "WORKTREE_UNAVAILABLE",
                page["items"][0]["attention_reasons"],
            )

    def test_closed_task_does_not_require_a_live_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
            now = "2030-01-01T00:00:00+00:00"
            with store.mutation() as connection:
                connection.execute(
                    """INSERT INTO tasks (
                           dispatch_id, schema_version, title, objective,
                           risk_level, state, task_base_sha, current_head_sha,
                           owner, agent, slug, branch, worktree_path,
                           created_at, updated_at
                       ) VALUES (?, 1, ?, ?, 'L1', 'CLOSED', ?, ?, ?, ?,
                                 ?, ?, ?, ?, ?)""",
                    (
                        "closed-task",
                        "Closed",
                        "Worktree lifecycle is complete",
                        head,
                        head,
                        "Codex",
                        "codex",
                        "closed",
                        "agent/codex/closed-task-closed",
                        str(repo / ".worktrees" / "closed-task-codex-closed"),
                        now,
                        now,
                    ),
                )

            task = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search="closed-task",
            )["items"][0]
            self.assertEqual(task["attention_reasons"], [])
            self.assertEqual(model.project()["health"], "HEALTHY")

    def test_closed_task_head_drift_is_historical_not_attention(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.start_write_task(
                "20260810-002",
                "Drift lifecycle",
                "Close after implementation",
                "L1",
                "codex",
                "drift-lifecycle",
            )
            worktree = Path(task["worktree_path"])
            (worktree / "drift.txt").write_text("drift\n", encoding="utf-8")
            run(["git", "add", "drift.txt"], worktree)
            run(["git", "commit", "-m", "advance task head"], worktree)
            store.transition(task["dispatch_id"], "IN_PROGRESS", "started")

            active = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search=task["dispatch_id"],
            )["items"][0]
            self.assertIn("HEAD_DRIFT", active["attention_reasons"])
            self.assertEqual(model.project()["health"], "ATTENTION")

            for target in (
                "REVIEWING",
                "ACCEPTED",
                "INTEGRATED",
                "RELEASED",
                "CLOSED",
            ):
                store.transition(task["dispatch_id"], target, target.lower())

            closed = model.tasks(
                {},
                state=None,
                risk=None,
                attention=None,
                search=task["dispatch_id"],
            )["items"][0]
            self.assertNotIn("HEAD_DRIFT", closed["attention_reasons"])
            self.assertEqual(model.project()["health"], "HEALTHY")
            self.assertTrue(model.task(task["dispatch_id"])["head_drift"])

    def test_detail_limit_fails_closed_instead_of_silently_truncating(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.create_task("20260809-026", "Limit", "Bound", "L2")
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                (
                    "blocker-%03d" % index,
                    task["dispatch_id"],
                    "reason",
                    "Codex",
                    "OPEN",
                    "resolve",
                    now,
                    now,
                )
                for index in range(201)
            ]
            with store.mutation() as connection:
                connection.executemany(
                    "INSERT INTO blockers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            with self.assertRaises(DashboardUnavailableError) as caught:
                model.task(task["dispatch_id"])
            self.assertEqual(caught.exception.code, "DETAIL_LIMIT_EXCEEDED")

    def test_unknown_event_payload_is_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            with store.mutation() as connection:
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """INSERT INTO tasks VALUES (
                           ?, 1, ?, ?, 'L1', 'PLANNED', NULL, ?, ?, 'Codex',
                           NULL, NULL, NULL, NULL, ?, ?
                       )""",
                    (
                        "20260809-013",
                        "Unknown event",
                        "Test",
                        "a" * 40,
                        "a" * 40,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO events VALUES (?, 1, ?, ?, ?)",
                    (
                        "20260809-013",
                        "UNKNOWN_PRIVATE",
                        json.dumps({"secret": "hidden"}),
                        now,
                    ),
                )
            item = model.events("20260809-013", {})["items"][0]
            self.assertEqual(item["summary"], "未识别事件")
            self.assertEqual(item["details"], {})
            self.assertNotIn("hidden", json.dumps(item))

    def test_task_detail_and_evidence_use_fixed_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            control = ControlPlane(RepoContext.discover(repo), store)
            task = control.create_task("20260809-014", "Detail", "Inspect", "L2")
            now = datetime.now(timezone.utc).isoformat()
            with store.mutation() as connection:
                connection.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "evidence-1",
                        task["dispatch_id"],
                        "test",
                        "artifacts/dispatches/20260809-014/result.txt",
                        "b" * 64,
                        task["current_head_sha"],
                        now,
                    ),
                )
            detail = model.task(task["dispatch_id"])
            self.assertEqual(
                set(detail),
                {
                    "task",
                    "actual_head_sha",
                    "head_drift",
                    "valid_acceptance",
                    "agents",
                    "blockers",
                    "reviews",
                    "pending_approval_count",
                    "evidence_count",
                    "latest_event",
                },
            )
            self.assertEqual(
                set(detail["task"]),
                {
                    "dispatch_id",
                    "title",
                    "objective",
                    "risk_level",
                    "state",
                    "effective_state",
                    "owner",
                    "agent",
                    "updated_at",
                    "task_base_sha",
                    "current_head_sha",
                    "branch",
                    "worktree_path",
                    "attention_reasons",
                },
            )
            evidence = model.evidence(task["dispatch_id"], {})["items"][0]
            self.assertEqual(
                set(evidence),
                {
                    "evidence_id",
                    "kind",
                    "relative_path",
                    "sha256",
                    "source_sha",
                    "created_at",
                    "stale",
                },
            )
            self.assertFalse(evidence["stale"])

    def test_unknown_task_is_not_found_for_all_subresources(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, store, model = self.make_model(Path(tmp))
            for operation in (
                lambda: model.task("missing"),
                lambda: model.events("missing", {}),
                lambda: model.evidence("missing", {}),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(DashboardNotFoundError):
                        operation()


if __name__ == "__main__":
    unittest.main()
