import fcntl
import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from team_control.git_context import RepoContext
from team_control.store import ControlStore
from tests.helpers import make_repo, run


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
        "report_path", "created_at",
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
    "evidence": {"source_sha"},
    "agents": {"model"},
    "reviews": {"report_path"},
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

                for table in set(EXPECTED_COLUMNS) - {"tasks"}:
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

                for table in ("approvals", "operations"):
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
