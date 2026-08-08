from types import MappingProxyType
from typing import FrozenSet, Mapping, Optional, Tuple

from team_control.errors import TransitionError


ALLOWED: Mapping[str, FrozenSet[str]] = MappingProxyType(
    {
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
)
PAUSABLE_STATES = frozenset({"IN_PROGRESS", "REVIEWING", "BLOCKED"})


def next_state(
    current: str,
    target: str,
    resume_state: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    if target not in ALLOWED.get(current, set()):
        raise TransitionError("illegal transition: %s -> %s" % (current, target))

    if target == "PAUSE_REQUESTED":
        return target, current

    if current == "PAUSE_REQUESTED" and target == "PAUSED":
        if resume_state not in PAUSABLE_STATES:
            raise TransitionError("invalid pause resume state: %r" % resume_state)
        return target, resume_state

    if current == "PAUSED":
        if resume_state is None or target != resume_state:
            raise TransitionError("illegal transition: %s -> %s" % (current, target))
        return target, None

    return target, None
