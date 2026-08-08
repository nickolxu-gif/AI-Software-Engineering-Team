import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from team_control.contracts import validate_record
from team_control.errors import BoundaryError, ContractError, TransitionError
from team_control.git_context import RepoContext
from team_control.service import ControlPlane
from team_control.store import ControlStore
from tests.helpers import make_repo, run


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")
        self.context = RepoContext.discover(self.repo)
        self.store = ControlStore.for_repo(self.context)
        self.store.initialize()
        self.control = ControlPlane(self.context, self.store)
        self.head = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, dispatch_id="20260808-003"):
        return self.control.create_task(
            dispatch_id, "Example", "Exercise lifecycle", "L1"
        )

    def worktree_identity(
        self, dispatch_id="20260808-003", agent="codex", slug="safe-slug"
    ):
        branch = "agent/%s/%s-%s" % (agent, dispatch_id, slug)
        path = self.context.common_dir.parent / ".worktrees" / (
            "%s-%s-%s" % (dispatch_id, agent, slug)
        )
        return branch, path

    def prepare_worktree_root(self):
        worktree_root = self.context.common_dir.parent / ".worktrees"
        worktree_root.mkdir(exist_ok=True)
        return worktree_root

    def linked_control(self):
        linked = Path(self.tmp.name) / "linked"
        run(
            ["git", "worktree", "add", "-b", "linked-service", str(linked)],
            self.repo,
        )
        context = RepoContext.discover(linked)
        store = ControlStore.for_repo(context)
        return linked, context, ControlPlane(context, store)

    def assert_event_contract(self, event):
        self.assertEqual(
            set(event),
            {
                "schema_version", "dispatch_id", "sequence", "event_type",
                "payload", "created_at",
            },
        )
        self.assertNotIn("payload_json", event)
        self.assertIsInstance(event["payload"], dict)
        validate_record("event", event)

    def run_with_followup_writer(self, operation, followup):
        mutation_finished = threading.Event()
        writer_ready = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        main_thread = threading.current_thread()
        original_mutation = self.store.mutation
        original_get_task = self.store.get_task

        @contextmanager
        def observed_mutation():
            with original_mutation() as connection:
                yield connection
            if threading.current_thread() is main_thread:
                mutation_finished.set()

        def coordinated_get_task(dispatch_id):
            if threading.current_thread() is main_thread:
                if not writer_done.wait(5.0):
                    raise AssertionError("followup writer did not finish")
            return original_get_task(dispatch_id)

        def writer():
            writer_ready.set()
            try:
                if not mutation_finished.wait(5.0):
                    raise AssertionError("primary mutation did not finish")
                followup()
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        thread = threading.Thread(target=writer)
        with mock.patch.object(self.store, "mutation", observed_mutation), mock.patch.object(
            self.store, "get_task", coordinated_get_task
        ):
            thread.start()
            self.assertTrue(writer_ready.wait(5.0), "followup writer did not start")
            result = operation()
            thread.join(5.0)

        self.assertFalse(thread.is_alive(), "followup writer leaked")
        if writer_errors:
            raise writer_errors[0]
        return result

    def assert_utc_now_inside_mutation(self, operation):
        inside_mutation = {"value": False}
        original_mutation = self.store.mutation

        @contextmanager
        def observed_mutation():
            with original_mutation() as connection:
                inside_mutation["value"] = True
                try:
                    yield connection
                finally:
                    inside_mutation["value"] = False

        def checked_now():
            self.assertTrue(
                inside_mutation["value"], "utc_now called before mutation lock"
            )
            return "2026-08-09T00:00:00+00:00"

        with mock.patch.object(self.store, "mutation", observed_mutation), mock.patch(
            "team_control.store.utc_now", side_effect=checked_now
        ):
            operation()

    def assert_rfc3339(self, value):
        event = {
            "schema_version": 1,
            "dispatch_id": "timestamp-check",
            "sequence": 1,
            "event_type": "TIMESTAMP_CHECK",
            "created_at": value,
        }
        validate_record("event", event)
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        self.assertIsNotNone(datetime.fromisoformat(normalized).utcoffset())

    def test_current_head_uses_fixed_argv(self):
        completed = SimpleNamespace(stdout="abc123\n")
        with mock.patch("team_control.service.run_argv", return_value=completed) as called:
            self.assertEqual(self.control.current_head(), "abc123")
        called.assert_called_once_with(
            ["git", "rev-parse", "HEAD"], self.context.root
        )

    def test_create_task_records_planned_task_and_first_event(self):
        task = self.create()

        self.assertEqual(task["schema_version"], 1)
        self.assertEqual(task["state"], "PLANNED")
        self.assertIsNone(task["resume_state"])
        self.assertEqual(task["task_base_sha"], self.head)
        self.assertEqual(task["current_head_sha"], self.head)
        self.assertEqual(task["owner"], "Codex")
        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1])
        self.assert_event_contract(events[0])
        self.assertEqual(events[0]["event_type"], "TASK_CREATED")
        payload = events[0]["payload"]
        self.assertEqual(payload["dispatch_id"], "20260808-003")
        self.assertEqual(payload["task_base_sha"], self.head)

    def test_get_task_and_status_report_missing_and_existing_tasks(self):
        self.assertIsNone(self.store.get_task("missing"))
        with self.assertRaises(KeyError):
            self.control.status("missing")

        task = self.create()
        status = self.control.status("20260808-003")
        self.assertEqual(status["task"], task)
        self.assertEqual([event["sequence"] for event in status["events"]], [1])
        self.assert_event_contract(status["events"][0])

    def test_status_task_and_events_come_from_one_read_snapshot(self):
        self.create()
        self.control.transition("20260808-003", "DISPATCHED", "initial state")
        task_read = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        main_thread = threading.current_thread()
        original_read_connection = self.store.read_connection
        original_list_events = self.store.list_events

        @contextmanager
        def coordinated_read_connection():
            with original_read_connection() as connection:
                class CoordinatedConnection:
                    def execute(inner_self, statement, parameters=()):
                        cursor = connection.execute(statement, parameters)
                        normalized = " ".join(statement.split())
                        if (
                            threading.current_thread() is main_thread
                            and normalized.startswith("SELECT * FROM tasks")
                        ):
                            task_read.set()
                        return cursor

                yield CoordinatedConnection()

        def coordinated_list_events(dispatch_id):
            if not writer_done.wait(5.0):
                raise AssertionError("status writer did not finish")
            return original_list_events(dispatch_id)

        def writer():
            try:
                if not task_read.wait(5.0):
                    raise AssertionError("status did not start its read")
                self.control.transition(
                    "20260808-003", "IN_PROGRESS", "concurrent status writer"
                )
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        thread = threading.Thread(target=writer)
        with mock.patch.object(
            self.store, "read_connection", coordinated_read_connection
        ), mock.patch.object(
            self.store, "list_events", coordinated_list_events
        ):
            thread.start()
            status = self.control.status("20260808-003")
            thread.join(5.0)

        self.assertFalse(thread.is_alive(), "status writer leaked")
        if writer_errors:
            raise writer_errors[0]
        state_events = [
            event for event in status["events"]
            if event["event_type"] == "STATE_CHANGED"
        ]
        self.assertTrue(state_events)
        self.assertEqual(
            status["task"]["state"], state_events[-1]["payload"]["to"]
        )

    def test_transition_records_stable_payload_and_next_sequence(self):
        self.create()
        task = self.control.transition(
            "20260808-003", "DISPATCHED", "scope approved"
        )

        self.assertEqual(task["state"], "DISPATCHED")
        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        for event in events:
            self.assert_event_contract(event)
        self.assertEqual(events[1]["event_type"], "STATE_CHANGED")
        self.assertEqual(
            events[1]["payload"],
            {"from": "PLANNED", "reason": "scope approved", "to": "DISPATCHED"},
        )
        with self.store.read_connection() as connection:
            payload_json = connection.execute(
                "SELECT payload_json FROM events WHERE dispatch_id = ? AND sequence = 2",
                ("20260808-003",),
            ).fetchone()[0]
        self.assertEqual(
            payload_json,
            '{"from": "PLANNED", "reason": "scope approved", "to": "DISPATCHED"}',
        )

    def test_illegal_transition_rolls_back_task_and_event(self):
        original = self.create()

        with self.assertRaises(TransitionError):
            self.control.transition("20260808-003", "RELEASED", "invalid")

        self.assertEqual(self.store.get_task("20260808-003"), original)
        self.assertEqual(len(self.store.list_events("20260808-003")), 1)

    def test_duplicate_dispatch_id_is_atomic(self):
        original = self.create()

        with self.assertRaises(sqlite3.IntegrityError):
            self.control.create_task(
                "20260808-003", "Replacement", "Must not replace", "L2"
            )

        self.assertEqual(self.store.get_task("20260808-003"), original)
        self.assertEqual(len(self.store.list_events("20260808-003")), 1)

    def test_consecutive_transitions_have_gapless_sequences(self):
        self.create()
        for target, reason in (
            ("DISPATCHED", "dispatch"),
            ("IN_PROGRESS", "start"),
            ("REVIEWING", "review"),
            ("ACCEPTED", "accepted"),
        ):
            self.control.transition("20260808-003", target, reason)

        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3, 4, 5])

    def test_transition_missing_task_writes_no_event(self):
        with self.assertRaisesRegex(KeyError, "missing"):
            self.control.transition("missing", "DISPATCHED", "not present")
        self.assertEqual(self.store.list_events("missing"), [])

    def test_pause_path_preserves_and_restores_database_resume_state(self):
        self.create()
        for target in ("DISPATCHED", "IN_PROGRESS", "PAUSE_REQUESTED"):
            task = self.control.transition("20260808-003", target, target.lower())
        self.assertEqual(task["resume_state"], "IN_PROGRESS")

        task = self.control.transition("20260808-003", "PAUSED", "safe checkpoint")
        self.assertEqual(task["resume_state"], "IN_PROGRESS")
        task = self.control.transition("20260808-003", "IN_PROGRESS", "resume")
        self.assertIsNone(task["resume_state"])
        self.assertEqual(
            [event["sequence"] for event in self.store.list_events("20260808-003")],
            [1, 2, 3, 4, 5, 6],
        )

    def test_attach_worktree_accepts_normative_branch_and_path_types(self):
        self.prepare_worktree_root()
        for index, path_type in enumerate((Path, str), start=1):
            dispatch_id = "20260808-00%d" % index
            self.create(dispatch_id)
            branch, path = self.worktree_identity(dispatch_id)

            task = self.control.attach_worktree(
                dispatch_id, "codex", "safe-slug", branch, path_type(path)
            )

            self.assertEqual(task["agent"], "codex")
            self.assertEqual(task["slug"], "safe-slug")
            self.assertEqual(task["branch"], branch)
            self.assertEqual(task["worktree_path"], str(path.resolve()))

    def test_linked_context_accepts_and_stores_main_repo_worktree_path(self):
        self.create()
        self.prepare_worktree_root()
        linked, context, control = self.linked_control()
        branch, path = self.worktree_identity()

        task = control.attach_worktree(
            "20260808-003", "codex", "safe-slug", branch, path
        )

        self.assertNotEqual(context.root, context.common_dir.parent)
        self.assertEqual(context.root, linked.resolve())
        self.assertEqual(task["worktree_path"], str(path.resolve()))
        self.assertEqual(
            self.store.get_task("20260808-003")["worktree_path"],
            str(path.resolve()),
        )

    def test_linked_context_rejects_linked_checkout_worktree_path(self):
        original = self.create()
        self.prepare_worktree_root()
        linked, _, control = self.linked_control()
        branch, expected_path = self.worktree_identity()
        linked_path = linked / ".worktrees" / expected_path.name

        with self.assertRaises(BoundaryError):
            control.attach_worktree(
                "20260808-003", "codex", "safe-slug", branch, linked_path
            )

        self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_missing_task_raises_key_error(self):
        self.prepare_worktree_root()
        branch, path = self.worktree_identity("missing")
        with self.assertRaisesRegex(KeyError, "missing"):
            self.control.attach_worktree(
                "missing", "codex", "safe-slug", branch, path
            )

    def test_attach_worktree_rejects_noncanonical_branch_without_update(self):
        original = self.create()
        expected_branch, expected_path = self.worktree_identity()
        cases = (
            "safe-branch",
            "agent/other/20260808-003-safe-slug",
            "agent/codex/20260808-999-safe-slug",
            "agent/codex/20260808-003-other-slug",
            expected_branch + "/extra",
        )
        for branch in cases:
            with self.subTest(branch=branch):
                with self.assertRaises(BoundaryError):
                    self.control.attach_worktree(
                        "20260808-003", "codex", "safe-slug", branch,
                        expected_path,
                    )
                self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_rejects_unsafe_components_without_update(self):
        original = self.create()
        cases = (
            ("bad agent", "safe-slug"),
            ("codex", "../slug"),
        )
        for agent, slug in cases:
            branch, path = self.worktree_identity(agent=agent, slug=slug)
            with self.subTest(agent=agent, slug=slug):
                with self.assertRaises(BoundaryError):
                    self.control.attach_worktree(
                        "20260808-003", agent, slug, branch, path
                    )
                self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_rejects_path_outside_or_with_wrong_basename(self):
        original = self.create()
        self.prepare_worktree_root()
        branch, expected_path = self.worktree_identity()
        paths = (
            Path(self.tmp.name) / expected_path.name,
            self.context.root / ".worktrees" / "wrong-basename",
        )
        for path in paths:
            with self.subTest(path=path):
                with self.assertRaises(BoundaryError):
                    self.control.attach_worktree(
                        "20260808-003", "codex", "safe-slug", branch, path
                    )
                self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_rejects_worktrees_symlink_escape(self):
        original = self.create()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.context.root / ".worktrees").symlink_to(
            outside, target_is_directory=True
        )
        branch, path = self.worktree_identity()

        with self.assertRaises(BoundaryError):
            self.control.attach_worktree(
                "20260808-003", "codex", "safe-slug", branch, path
            )

        self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_rejects_worktrees_symlink_within_repo(self):
        original = self.create()
        redirect = self.context.root / "redirect"
        redirect.mkdir()
        (self.context.root / ".worktrees").symlink_to(
            redirect, target_is_directory=True
        )
        branch, path = self.worktree_identity()

        with self.assertRaises(BoundaryError):
            self.control.attach_worktree(
                "20260808-003", "codex", "safe-slug", branch, path
            )

        self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_attach_worktree_rejects_normative_path_symlink_within_repo(self):
        original = self.create()
        worktree_root = self.context.common_dir.parent / ".worktrees"
        worktree_root.mkdir()
        redirect = self.context.common_dir.parent / "redirect"
        redirect.mkdir()
        branch, path = self.worktree_identity()
        path.symlink_to(redirect, target_is_directory=True)

        with self.assertRaises(BoundaryError):
            self.control.attach_worktree(
                "20260808-003", "codex", "safe-slug", branch, path
            )

        self.assertEqual(self.store.get_task("20260808-003"), original)

    def test_service_rejects_invalid_create_and_transition_inputs(self):
        create_cases = (
            ("../task", "Title", "Objective", "L1", BoundaryError),
            ("safe-task", "", "Objective", "L1", ContractError),
            ("safe-task", "Title", "", "L1", ContractError),
            ("safe-task", "Title", "Objective", "L4", ContractError),
        )
        for dispatch_id, title, objective, risk, error in create_cases:
            with self.subTest(create=dispatch_id, title=title, objective=objective, risk=risk):
                with self.assertRaises(error):
                    self.control.create_task(dispatch_id, title, objective, risk)

        self.create()
        original = self.store.get_task("20260808-003")
        transition_cases = (
            ("../task", "DISPATCHED", "reason", BoundaryError),
            ("20260808-003", "DISPATCHED;touch", "reason", BoundaryError),
            ("20260808-003", "DISPATCHED", "", ContractError),
            ("20260808-003", "DISPATCHED", "   ", ContractError),
            ("20260808-003", "DISPATCHED", None, ContractError),
        )
        for dispatch_id, target, reason, error in transition_cases:
            with self.subTest(transition=dispatch_id, target=target, reason=reason):
                with self.assertRaises(error):
                    self.control.transition(dispatch_id, target, reason)
                self.assertEqual(self.store.get_task("20260808-003"), original)
                self.assertEqual(len(self.store.list_events("20260808-003")), 1)

    def test_create_rolls_back_insert_when_json_encoding_fails(self):
        record = {
            "schema_version": 1,
            "dispatch_id": "json-failure",
            "title": "Title",
            "objective": "Objective",
            "risk_level": "L1",
            "state": "PLANNED",
            "task_base_sha": self.head,
            "owner": "Codex",
            "not_json": object(),
        }

        with self.assertRaises(TypeError):
            self.store.create_task(record)

        self.assertIsNone(self.store.get_task("json-failure"))
        self.assertEqual(self.store.list_events("json-failure"), [])

    def test_transition_rolls_back_state_when_json_encoding_fails(self):
        original = self.create()

        with mock.patch("team_control.store.json.dumps", side_effect=TypeError("boom")):
            with self.assertRaisesRegex(TypeError, "boom"):
                self.control.transition("20260808-003", "DISPATCHED", "reason")

        self.assertEqual(self.store.get_task("20260808-003"), original)
        self.assertEqual(len(self.store.list_events("20260808-003")), 1)

    def test_create_returns_snapshot_before_followup_writer_transition(self):
        task = self.run_with_followup_writer(
            self.create,
            lambda: self.control.transition(
                "20260808-003", "DISPATCHED", "followup writer"
            ),
        )

        self.assertEqual(task["state"], "PLANNED")
        self.assertEqual(self.store.get_task("20260808-003")["state"], "DISPATCHED")

    def test_transition_returns_its_snapshot_before_followup_writer_transition(self):
        self.create()
        task = self.run_with_followup_writer(
            lambda: self.control.transition(
                "20260808-003", "DISPATCHED", "primary writer"
            ),
            lambda: self.control.transition(
                "20260808-003", "IN_PROGRESS", "followup writer"
            ),
        )

        self.assertEqual(task["state"], "DISPATCHED")
        self.assertEqual(self.store.get_task("20260808-003")["state"], "IN_PROGRESS")

    def test_concurrent_same_transition_is_serialized_without_duplicate_sequence(self):
        self.create()
        barrier = threading.Barrier(3)
        results = []
        results_lock = threading.Lock()

        def transition_once():
            barrier.wait()
            try:
                self.control.transition("20260808-003", "DISPATCHED", "concurrent")
            except Exception as error:
                result = type(error)
            else:
                result = None
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=transition_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(None), 1)
        self.assertEqual(results.count(TransitionError), 1)
        self.assertEqual(
            [event["sequence"] for event in self.store.list_events("20260808-003")],
            [1, 2],
        )

    def test_persisted_timestamps_follow_rfc3339_contract(self):
        created = self.create()
        self.prepare_worktree_root()
        branch, path = self.worktree_identity()
        attached = self.control.attach_worktree(
            "20260808-003", "codex", "safe-slug", branch, path
        )
        transitioned = self.control.transition(
            "20260808-003", "DISPATCHED", "timestamp check"
        )

        for value in (
            created["created_at"],
            created["updated_at"],
            attached["updated_at"],
            transitioned["updated_at"],
        ):
            self.assert_rfc3339(value)
        for event in self.store.list_events("20260808-003"):
            self.assert_rfc3339(event["created_at"])

    def test_mutation_timestamps_are_captured_after_lock_entry(self):
        self.assert_utc_now_inside_mutation(
            lambda: self.create("timestamp-create")
        )

        self.create("timestamp-transition")
        self.assert_utc_now_inside_mutation(
            lambda: self.control.transition(
                "timestamp-transition", "DISPATCHED", "timestamp"
            )
        )

        self.create("timestamp-attach")
        self.prepare_worktree_root()
        branch, path = self.worktree_identity("timestamp-attach")
        self.assert_utc_now_inside_mutation(
            lambda: self.control.attach_worktree(
                "timestamp-attach", "codex", "safe-slug", branch, path
            )
        )


if __name__ == "__main__":
    unittest.main()
