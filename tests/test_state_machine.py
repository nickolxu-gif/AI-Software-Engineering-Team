import unittest

from team_control.errors import TransitionError
from team_control.state_machine import ALLOWED, next_state


EXPECTED_ALLOWED = {
    "PLANNED": frozenset({"DISPATCHED", "NEEDS_CLARIFICATION", "BLOCKED"}),
    "NEEDS_CLARIFICATION": frozenset({"PLANNED", "BLOCKED"}),
    "DISPATCHED": frozenset({"IN_PROGRESS", "BLOCKED"}),
    "IN_PROGRESS": frozenset(
        {"REVIEWING", "BLOCKED", "NEEDS_DIRECTION", "PAUSE_REQUESTED"}
    ),
    "NEEDS_DIRECTION": frozenset({"IN_PROGRESS", "BLOCKED"}),
    "REVIEWING": frozenset(
        {"ACCEPTED", "IN_PROGRESS", "BLOCKED", "PAUSE_REQUESTED"}
    ),
    "BLOCKED": frozenset(
        {"IN_PROGRESS", "REVIEWING", "PAUSE_REQUESTED", "UNKNOWN"}
    ),
    "PAUSE_REQUESTED": frozenset({"PAUSED", "BLOCKED"}),
    "PAUSED": frozenset({"IN_PROGRESS", "REVIEWING", "BLOCKED"}),
    "ACCEPTED": frozenset({"INTEGRATED", "BLOCKED"}),
    "INTEGRATED": frozenset({"RELEASED", "BLOCKED"}),
    "RELEASED": frozenset({"CLOSED", "BLOCKED"}),
    "UNKNOWN": frozenset({"BLOCKED"}),
    "CLOSED": frozenset(),
}
PAUSABLE_STATES = frozenset({"IN_PROGRESS", "REVIEWING", "BLOCKED"})


class StateMachineTests(unittest.TestCase):
    def test_transition_matrix_matches_contract(self):
        self.assertEqual(dict(ALLOWED), EXPECTED_ALLOWED)

    def test_every_allowed_transition_applies_resume_rules(self):
        for current, targets in EXPECTED_ALLOWED.items():
            for target in targets:
                with self.subTest(current=current, target=target):
                    if target == "PAUSE_REQUESTED":
                        result = next_state(current, target, resume_state="FORGED")
                        expected = (target, current)
                    elif current == "PAUSE_REQUESTED" and target == "PAUSED":
                        result = next_state(
                            current,
                            target,
                            resume_state="IN_PROGRESS",
                        )
                        expected = (target, "IN_PROGRESS")
                    elif current == "PAUSED":
                        result = next_state(current, target, resume_state=target)
                        expected = (target, None)
                    else:
                        result = next_state(current, target, resume_state="STALE")
                        expected = (target, None)

                    self.assertEqual(result, expected)

    def test_every_disallowed_transition_between_known_states_is_rejected(self):
        states = set(EXPECTED_ALLOWED)
        for current, allowed_targets in EXPECTED_ALLOWED.items():
            for target in states - set(allowed_targets):
                with self.subTest(current=current, target=target):
                    with self.assertRaises(TransitionError) as caught:
                        next_state(current, target, resume_state=current)
                    self.assertEqual(
                        str(caught.exception),
                        "illegal transition: %s -> %s" % (current, target),
                    )

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
        for resume_state in PAUSABLE_STATES:
            with self.subTest(resume_state=resume_state):
                self.assertEqual(
                    next_state(
                        "PAUSE_REQUESTED",
                        "PAUSED",
                        resume_state=resume_state,
                    ),
                    ("PAUSED", resume_state),
                )

    def test_pause_requested_to_paused_rejects_invalid_resume_state(self):
        invalid_states = {None, "NOT_A_STATE"} | (
            set(EXPECTED_ALLOWED) - set(PAUSABLE_STATES)
        )
        for resume_state in invalid_states:
            with self.subTest(resume_state=resume_state):
                with self.assertRaises(TransitionError) as caught:
                    next_state(
                        "PAUSE_REQUESTED",
                        "PAUSED",
                        resume_state=resume_state,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "invalid pause resume state: %r" % resume_state,
                )

    def test_pause_requested_to_blocked_clears_resume_state(self):
        self.assertEqual(
            next_state("PAUSE_REQUESTED", "BLOCKED", resume_state="IN_PROGRESS"),
            ("BLOCKED", None),
        )

    def test_pause_request_uses_current_state_not_caller_resume_state(self):
        self.assertEqual(
            next_state("IN_PROGRESS", "PAUSE_REQUESTED", resume_state="REVIEWING"),
            ("PAUSE_REQUESTED", "IN_PROGRESS"),
        )

    def test_normal_transition_clears_stale_resume_state(self):
        self.assertEqual(
            next_state("IN_PROGRESS", "REVIEWING", resume_state="BLOCKED"),
            ("REVIEWING", None),
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

    def test_allowed_mapping_is_immutable(self):
        with self.assertRaises(TypeError):
            ALLOWED["PLANNED"] = ALLOWED["PLANNED"]

    def test_allowed_target_sets_are_immutable(self):
        with self.assertRaises(AttributeError):
            ALLOWED["PLANNED"].add("DISPATCHED")


if __name__ == "__main__":
    unittest.main()
