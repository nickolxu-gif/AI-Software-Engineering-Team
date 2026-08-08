import unittest

from team_control.contracts import TASK_STATES, validate_record
from team_control.errors import ContractError


class ContractTests(unittest.TestCase):
    def test_state_vocabulary_contains_pause_and_fail_closed_states(self):
        self.assertIn("PAUSE_REQUESTED", TASK_STATES)
        self.assertIn("PAUSED", TASK_STATES)
        self.assertIn("UNKNOWN", TASK_STATES)

    def test_task_requires_dispatch_and_git_identity(self):
        with self.assertRaises(ContractError):
            validate_record("task", {"dispatch_id": "20260808-003"})

    def test_valid_task_is_accepted(self):
        record = {
            "schema_version": 1,
            "dispatch_id": "20260808-003",
            "title": "Example",
            "objective": "Prove contract",
            "risk_level": "L1",
            "state": "PLANNED",
            "task_base_sha": "a" * 40,
            "owner": "Codex",
        }
        self.assertEqual(validate_record("task", record), record)
