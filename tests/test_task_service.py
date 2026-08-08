import json
import sqlite3
import tempfile
import threading
import unittest
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
        self.assertEqual(events[0]["event_type"], "TASK_CREATED")
        payload = json.loads(events[0]["payload_json"])
        self.assertEqual(payload["dispatch_id"], "20260808-003")
        self.assertEqual(payload["task_base_sha"], self.head)

    def test_get_task_and_status_report_missing_and_existing_tasks(self):
        self.assertIsNone(self.store.get_task("missing"))
        self.assertEqual(
            self.control.status("missing"), {"task": None, "events": []}
        )

        task = self.create()
        status = self.control.status("20260808-003")
        self.assertEqual(status["task"], task)
        self.assertEqual([event["sequence"] for event in status["events"]], [1])

    def test_transition_records_stable_payload_and_next_sequence(self):
        self.create()
        task = self.control.transition(
            "20260808-003", "DISPATCHED", "scope approved"
        )

        self.assertEqual(task["state"], "DISPATCHED")
        events = self.store.list_events("20260808-003")
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(events[1]["event_type"], "STATE_CHANGED")
        self.assertEqual(
            events[1]["payload_json"],
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

    def test_attach_worktree_updates_fields_and_stores_path_as_text(self):
        self.create()
        injected_path = Path(self.tmp.name) / "$(touch should-not-exist)"

        task = self.control.attach_worktree(
            "20260808-003", "codex", "safe-slug", "safe-branch", injected_path
        )

        self.assertEqual(task["agent"], "codex")
        self.assertEqual(task["slug"], "safe-slug")
        self.assertEqual(task["branch"], "safe-branch")
        self.assertEqual(task["worktree_path"], str(injected_path))
        self.assertFalse((Path(self.tmp.name) / "should-not-exist").exists())

    def test_attach_worktree_missing_task_raises_key_error(self):
        with self.assertRaisesRegex(KeyError, "missing"):
            self.control.attach_worktree(
                "missing", "codex", "safe-slug", "safe-branch", self.repo
            )

    def test_attach_worktree_rejects_unsafe_components_without_update(self):
        original = self.create()
        cases = (
            ("bad agent", "safe-slug", "safe-branch"),
            ("codex", "../slug", "safe-branch"),
            ("codex", "safe-slug", "branch;touch-pwned"),
        )
        for agent, slug, branch in cases:
            with self.subTest(agent=agent, slug=slug, branch=branch):
                with self.assertRaises(BoundaryError):
                    self.control.attach_worktree(
                        "20260808-003", agent, slug, branch, self.repo
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
        attached = self.control.attach_worktree(
            "20260808-003", "codex", "safe-slug", "safe-branch", self.repo
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


if __name__ == "__main__":
    unittest.main()
