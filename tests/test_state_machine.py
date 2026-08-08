import unittest

from team_control.errors import TransitionError
from team_control.state_machine import next_state


class StateMachineTests(unittest.TestCase):
    def test_normal_delivery_path(self):
        self.assertEqual(next_state("PLANNED", "DISPATCHED"), ("DISPATCHED", None))
        self.assertEqual(next_state("REVIEWING", "ACCEPTED"), ("ACCEPTED", None))

    def test_pause_saves_and_restores_resume_state(self):
        self.assertEqual(
            next_state("IN_PROGRESS", "PAUSE_REQUESTED"),
            ("PAUSE_REQUESTED", "IN_PROGRESS"),
        )
        self.assertEqual(
            next_state("PAUSED", "IN_PROGRESS", resume_state="IN_PROGRESS"),
            ("IN_PROGRESS", None),
        )

    def test_pause_requested_to_paused_preserves_resume_state(self):
        self.assertEqual(
            next_state("PAUSE_REQUESTED", "PAUSED", resume_state="REVIEWING"),
            ("PAUSED", "REVIEWING"),
        )

    def test_pause_request_uses_current_state_not_caller_resume_state(self):
        self.assertEqual(
            next_state("IN_PROGRESS", "PAUSE_REQUESTED", resume_state="REVIEWING"),
            ("PAUSE_REQUESTED", "IN_PROGRESS"),
        )

    def test_paused_requires_matching_resume_state(self):
        for resume_state in (None, "REVIEWING"):
            with self.subTest(resume_state=resume_state):
                with self.assertRaisesRegex(
                    TransitionError,
                    "^illegal transition: PAUSED -> IN_PROGRESS$",
                ):
                    next_state("PAUSED", "IN_PROGRESS", resume_state=resume_state)

    def test_pause_cannot_skip_safe_checkpoint(self):
        with self.assertRaises(TransitionError):
            next_state("IN_PROGRESS", "PAUSED")

    def test_illegal_release_is_rejected(self):
        with self.assertRaises(TransitionError):
            next_state("PLANNED", "RELEASED")

    def test_unknown_source_state_is_rejected(self):
        with self.assertRaisesRegex(
            TransitionError,
            "^illegal transition: NOT_A_STATE -> BLOCKED$",
        ):
            next_state("NOT_A_STATE", "BLOCKED")


if __name__ == "__main__":
    unittest.main()
