import fcntl
import multiprocessing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from team_control import store as store_module
from team_control.errors import BoundaryError, ReconciliationError
from team_control.git_context import RepoContext
from tests.helpers import make_repo, run


ControlStore = store_module.ControlStore


EXPECTED_COLUMNS = {
    "tasks": (
        "dispatch_id", "schema_version", "title", "objective", "risk_level",
        "state", "resume_state", "task_base_sha", "current_head_sha", "owner",
        "agent", "slug", "branch", "worktree_path", "created_at", "updated_at",
    ),
    "events": (
        "dispatch_id", "sequence", "event_type", "payload_json", "created_at",
    ),
    "approvals": (
        "approval_id", "dispatch_id", "action", "target_sha", "request_hash",
        "nonce_hash", "expires_at", "consumed_at", "status", "idempotency_key",
    ),
    "operations": (
        "operation_id", "dispatch_id", "action", "request_hash", "target_sha",
        "phase", "result_json", "idempotency_key", "created_at", "updated_at",
    ),
    "intents": (
        "intent_id", "dispatch_id", "action", "target_sha", "request_hash",
        "confirmation_hash", "status", "result_code", "idempotency_key",
        "created_at", "updated_at",
    ),
    "task_intake_requests": (
        "intake_id", "title", "objective", "context", "request_hash",
        "status", "result_code", "idempotency_key", "created_at", "updated_at",
    ),
    "task_intake_handlings": (
        "intake_id", "dispatch_id", "disposition", "handled_at",
    ),
    "evidence": (
        "evidence_id", "dispatch_id", "kind", "path", "sha256", "source_sha",
        "created_at",
    ),
    "agents": (
        "dispatch_id", "agent_id", "role", "model", "state", "progress",
        "report_json", "updated_at",
    ),
    "reviews": (
        "review_id", "dispatch_id", "reviewer", "disposition", "source_sha",
        "report_path", "report_sha256", "created_at",
    ),
    "blockers": (
        "blocker_id", "dispatch_id", "reason", "owner", "status",
        "resolution_condition", "created_at", "updated_at",
    ),
}

EXPECTED_PRIMARY_KEYS = {
    "tasks": {"dispatch_id": 1},
    "events": {"dispatch_id": 1, "sequence": 2},
    "approvals": {"approval_id": 1},
    "operations": {"operation_id": 1},
    "intents": {"intent_id": 1},
    "task_intake_requests": {"intake_id": 1},
    "task_intake_handlings": {"intake_id": 1},
    "evidence": {"evidence_id": 1},
    "agents": {"dispatch_id": 1, "agent_id": 2},
    "reviews": {"review_id": 1},
    "blockers": {"blocker_id": 1},
}

EXPECTED_NULLABLE = {
    "tasks": {"resume_state", "agent", "slug", "branch", "worktree_path"},
    "events": set(),
    "approvals": {"consumed_at"},
    "operations": {"result_json"},
    "intents": {"confirmation_hash"},
    "task_intake_requests": {"context"},
    "task_intake_handlings": set(),
    "evidence": {"source_sha"},
    "agents": {"model"},
    "reviews": set(),
    "blockers": {"resolution_condition"},
}


def _contend_for_store(db_path, lock_path, probe_done, saw_contention, entered):
    store = ControlStore(db_path, lock_path)
    with store.lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            saw_contention.set()
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            probe_done.set()

    with store.mutation() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS writer_probe (value TEXT)")
        connection.execute("INSERT INTO writer_probe VALUES ('child')")
        entered.set()


def _hold_store_lock(lock_path, locked, release):
    with Path(lock_path).open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        locked.set()
        try:
            release.wait(2.0)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _insert_task(connection, dispatch_id="task-1"):
    connection.execute(
        """INSERT INTO tasks (
               dispatch_id, schema_version, title, objective, risk_level, state,
               task_base_sha, current_head_sha, owner, created_at, updated_at
           ) VALUES (?, 1, 'Title', 'Objective', 'L1', 'PLANNED', ?, ?, 'Codex', ?, ?)""",
        (dispatch_id, "a" * 40, "a" * 40, "2026-08-08T00:00:00Z",
         "2026-08-08T00:00:00Z"),
    )


class StoreTests(unittest.TestCase):
    def make_store(self, root):
        repo = make_repo(root / "repo")
        context = RepoContext.discover(repo)
        return ControlStore.for_repo(context), context

    def test_database_lives_under_git_common_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, context = self.make_store(Path(tmp))
            store.initialize()

            runtime = context.common_dir / "team" / "runtime"
            self.assertEqual(store.path, runtime / "team.db")
            self.assertEqual(store.lock_path, runtime / "control-plane.lock")
            self.assertTrue(store.path.is_file())

    def test_for_repo_rejects_team_symlink_escape_before_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            context = RepoContext.discover(repo)
            outside = root / "outside"
            outside.mkdir()
            (context.common_dir / "team").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(BoundaryError):
                ControlStore.for_repo(context)

            self.assertFalse((outside / "runtime").exists())
            self.assertEqual(list(outside.rglob("team.db")), [])
            self.assertEqual(list(outside.rglob("control-plane.lock")), [])

    def test_schema_matches_all_control_plane_tables_and_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()

            with store.read_connection() as connection:
                names = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                self.assertEqual(names, set(EXPECTED_COLUMNS))

                for table, expected_columns in EXPECTED_COLUMNS.items():
                    with self.subTest(table=table):
                        rows = connection.execute(
                            "PRAGMA table_info(%s)" % table
                        ).fetchall()
                        self.assertEqual(tuple(row["name"] for row in rows), expected_columns)
                        self.assertEqual(
                            {row["name"]: row["pk"] for row in rows if row["pk"]},
                            EXPECTED_PRIMARY_KEYS[table],
                        )
                        nullable = {
                            row["name"] for row in rows
                            if not row["notnull"] and not row["pk"]
                        }
                        self.assertEqual(nullable, EXPECTED_NULLABLE[table])

                for table in set(EXPECTED_COLUMNS) - {
                    "tasks", "task_intake_requests", "task_intake_handlings",
                }:
                    with self.subTest(foreign_key_table=table):
                        foreign_keys = connection.execute(
                            "PRAGMA foreign_key_list(%s)" % table
                        ).fetchall()
                        self.assertEqual(len(foreign_keys), 1)
                        self.assertEqual(
                            (foreign_keys[0]["table"], foreign_keys[0]["from"],
                             foreign_keys[0]["to"]),
                            ("tasks", "dispatch_id", "dispatch_id"),
                        )

                handling_keys = connection.execute(
                    "PRAGMA foreign_key_list(task_intake_handlings)"
                ).fetchall()
                self.assertEqual(
                    {(row["table"], row["from"], row["to"]) for row in handling_keys},
                    {
                        ("task_intake_requests", "intake_id", "intake_id"),
                        ("tasks", "dispatch_id", "dispatch_id"),
                    },
                )

                for table in ("approvals", "operations", "intents", "task_intake_requests"):
                    unique_columns = set()
                    for index in connection.execute(
                        "PRAGMA index_list(%s)" % table
                    ):
                        if index["origin"] == "u":
                            columns = connection.execute(
                                "PRAGMA index_info(%s)" % index["name"]
                            ).fetchall()
                            unique_columns.add(tuple(row["name"] for row in columns))
                    self.assertEqual(unique_columns, {("idempotency_key",)})

    def test_initialize_is_idempotent_and_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            with store.mutation() as connection:
                _insert_task(connection)

            store.initialize()

            with store.read_connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_create_intent_is_idempotent_and_records_one_safe_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            with store.mutation() as connection:
                _insert_task(connection, "intent-task")

            created = store.create_intent(
                "intent-task", "PAUSE_REQUEST", "a" * 40, "b" * 64,
                None, "123e4567-e89b-12d3-a456-426614174000",
            )
            replayed = store.create_intent(
                "intent-task", "PAUSE_REQUEST", "a" * 40, "b" * 64,
                None, "123e4567-e89b-12d3-a456-426614174000",
            )

            self.assertEqual(created, replayed)
            self.assertEqual(created["status"], "PENDING")
            self.assertEqual(created["result_code"], "PENDING")
            self.assertNotIn("confirmation_hash", {
                key for key in store.list_events("intent-task")[0]["payload"]
            })
            self.assertEqual(
                store.list_events("intent-task")[0]["event_type"],
                "INTENT_SUBMITTED",
            )

    def test_intent_idempotency_conflict_and_terminal_event_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            with store.mutation() as connection:
                _insert_task(connection, "intent-task")

            intent = store.create_intent(
                "intent-task", "PAUSE_REQUEST", "a" * 40, "b" * 64,
                None, "123e4567-e89b-12d3-a456-426614174000",
            )
            with self.assertRaises(ReconciliationError):
                store.create_intent(
                    "intent-task", "RESUME_REQUEST", "a" * 40, "c" * 64,
                    None, "123e4567-e89b-12d3-a456-426614174000",
                )

            finished = store.finish_intent(intent["intent_id"], "REJECTED", "STALE_HEAD")
            self.assertEqual(finished["status"], "REJECTED")
            self.assertEqual(finished["result_code"], "STALE_HEAD")
            events = store.list_events("intent-task")
            self.assertEqual([event["event_type"] for event in events], [
                "INTENT_SUBMITTED", "INTENT_REJECTED",
            ])
            self.assertEqual(
                store.finish_intent(intent["intent_id"], "REJECTED", "STALE_HEAD"),
                finished,
            )
            self.assertEqual(len(store.list_events("intent-task")), 2)

    def test_regular_repo_and_linked_worktree_share_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            linked = root / "linked"
            run(["git", "worktree", "add", "-b", "linked-store", str(linked)], repo)

            primary = ControlStore.for_repo(RepoContext.discover(repo))
            worktree = ControlStore.for_repo(RepoContext.discover(linked))

            self.assertEqual(primary.path, worktree.path)
            self.assertEqual(primary.lock_path, worktree.lock_path)

    def test_mutation_rolls_back_and_releases_resources_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            failed_connection = None

            with self.assertRaisesRegex(RuntimeError, "abort mutation"):
                with store.mutation() as failed_connection:
                    _insert_task(failed_connection, "rolled-back")
                    raise RuntimeError("abort mutation")

            with self.assertRaises(sqlite3.ProgrammingError):
                failed_connection.execute("SELECT 1")

            with store.mutation() as connection:
                _insert_task(connection, "committed")

            with store.read_connection() as connection:
                rows = connection.execute(
                    "SELECT dispatch_id FROM tasks ORDER BY dispatch_id"
                ).fetchall()
            self.assertEqual([row[0] for row in rows], ["committed"])

    def test_foreign_keys_are_enabled_for_every_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()

            with self.assertRaises(sqlite3.IntegrityError):
                with store.mutation() as connection:
                    self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                    connection.execute(
                        "INSERT INTO events VALUES ('missing', 1, 'TEST', '{}', 'now')"
                    )

            with store.read_connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_read_connection_is_read_only_and_closes_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()

            with store.read_connection() as connection:
                self.assertIs(connection.row_factory, sqlite3.Row)
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    connection.execute("CREATE TABLE forbidden (value TEXT)")

            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_control_store_connections_deny_attach_and_detach(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()

            with store.mutation() as connection:
                with self.assertRaisesRegex(sqlite3.DatabaseError, "not authorized"):
                    connection.execute("ATTACH DATABASE ':memory:' AS outside")
                with self.assertRaisesRegex(sqlite3.DatabaseError, "not authorized"):
                    connection.execute("DETACH DATABASE main")

            with store.read_connection() as connection:
                with self.assertRaisesRegex(sqlite3.DatabaseError, "not authorized"):
                    connection.execute("ATTACH DATABASE ':memory:' AS outside")
                with self.assertRaisesRegex(sqlite3.DatabaseError, "not authorized"):
                    connection.execute("DETACH DATABASE main")

    def test_authorizer_action_codes_have_sqlite_stable_fallbacks(self):
        self.assertEqual(store_module.SQLITE_ATTACH_ACTION, 24)
        self.assertEqual(store_module.SQLITE_DETACH_ACTION, 25)

    def test_read_connection_is_query_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            store.initialize()

            with store.read_connection() as connection:
                self.assertEqual(
                    connection.execute("PRAGMA query_only").fetchone()[0],
                    1,
                )
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE tasks SET title = 'changed' WHERE 1 = 0"
                    )

    def test_read_connection_uses_two_second_busy_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            store.initialize()
            with store.read_connection() as connection:
                self.assertEqual(
                    connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    2000,
                )

    def test_read_connection_closes_when_pragma_setup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            connection = mock.Mock()
            connection.execute.side_effect = sqlite3.OperationalError(
                "pragma failed"
            )

            with mock.patch.object(
                store_module.sqlite3,
                "connect",
                return_value=connection,
            ):
                with self.assertRaisesRegex(
                    sqlite3.OperationalError,
                    "pragma failed",
                ):
                    with store.read_connection():
                        self.fail("setup failure unexpectedly yielded")

            connection.close.assert_called_once_with()

    def test_store_connect_closes_when_authorizer_setup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            connection = mock.Mock()
            connection.set_authorizer.side_effect = RuntimeError("authorizer failed")

            with mock.patch.object(
                store_module.sqlite3,
                "connect",
                return_value=connection,
            ):
                with self.assertRaisesRegex(RuntimeError, "authorizer failed"):
                    store._connect()

            connection.close.assert_called_once_with()

    def test_read_connection_closes_when_authorizer_setup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            connection = mock.Mock()
            connection.set_authorizer.side_effect = RuntimeError("authorizer failed")

            with mock.patch.object(
                store_module.sqlite3,
                "connect",
                return_value=connection,
            ):
                with self.assertRaisesRegex(RuntimeError, "authorizer failed"):
                    with store.read_connection():
                        self.fail("setup failure unexpectedly yielded")

            connection.close.assert_called_once_with()

    def test_paths_are_absolute_normalized_path_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "nested" / ".." / "runtime"
            store = ControlStore(
                str(runtime / "team.db"),
                str(runtime / "control-plane.lock"),
            )

            self.assertIsInstance(store.path, Path)
            self.assertIsInstance(store.lock_path, Path)
            self.assertEqual(store.path, (Path(tmp) / "runtime" / "team.db").resolve())
            self.assertEqual(
                store.lock_path,
                (Path(tmp) / "runtime" / "control-plane.lock").resolve(),
            )

    def test_lock_timing_options_must_be_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for option in ("lock_timeout", "lock_poll_interval"):
                for invalid in (0, -0.1):
                    with self.subTest(option=option, invalid=invalid):
                        values = {"lock_timeout": 5.0, "lock_poll_interval": 0.01}
                        values[option] = invalid
                        with self.assertRaises(ValueError):
                            ControlStore(
                                root / "team.db",
                                root / "control-plane.lock",
                                **values
                            )

    def test_process_lock_times_out_with_stable_domain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            store = ControlStore(
                store.path,
                store.lock_path,
                lock_timeout=0.15,
                lock_poll_interval=0.01,
            )
            process_context = multiprocessing.get_context("spawn")
            locked = process_context.Event()
            release = process_context.Event()
            holder = process_context.Process(
                target=_hold_store_lock,
                args=(store.lock_path, locked, release),
            )

            try:
                holder.start()
                self.assertTrue(locked.wait(5.0), "lock holder failed to start")
                started = time.monotonic()
                with self.assertRaisesRegex(
                    store_module.StoreBusyError, r"^control store is busy$"
                ) as raised:
                    with store.mutation():
                        self.fail("writer entered while another process held the lock")
                elapsed = time.monotonic() - started

                self.assertEqual(raised.exception.code, "STORE_BUSY")
                self.assertLess(elapsed, 1.0, "lock timeout exceeded its bounded wait")
            finally:
                release.set()
                holder.join(5.0)
                if holder.is_alive():
                    holder.terminate()
                    holder.join(5.0)

            self.assertFalse(holder.is_alive(), "lock holder leaked after cleanup")
            self.assertEqual(holder.exitcode, 0)

    def test_process_lock_serializes_two_writers_with_bounded_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.make_store(Path(tmp))
            store.initialize()
            process_context = multiprocessing.get_context("spawn")
            probe_done = process_context.Event()
            saw_contention = process_context.Event()
            entered = process_context.Event()
            contender = process_context.Process(
                target=_contend_for_store,
                args=(store.path, store.lock_path, probe_done, saw_contention, entered),
            )

            try:
                with store.mutation():
                    contender.start()
                    self.assertTrue(probe_done.wait(5.0), "lock probe timed out")
                    self.assertTrue(saw_contention.is_set(), "child acquired the held lock")
                    self.assertFalse(entered.is_set(), "second writer entered while lock was held")

                self.assertTrue(entered.wait(5.0), "second writer stayed blocked after unlock")
                contender.join(5.0)
                self.assertFalse(contender.is_alive(), "second writer did not exit")
                self.assertEqual(contender.exitcode, 0)
            finally:
                if contender.is_alive():
                    contender.terminate()
                    contender.join(5.0)

            with store.read_connection() as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM writer_probe").fetchone()[0],
                    "child",
                )


if __name__ == "__main__":
    unittest.main()
