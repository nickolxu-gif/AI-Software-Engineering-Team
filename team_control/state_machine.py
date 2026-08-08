from typing import Dict, Optional, Set, Tuple

from team_control.errors import TransitionError


ALLOWED: Dict[str, Set[str]] = {
    "PLANNED": {"DISPATCHED", "NEEDS_CLARIFICATION", "BLOCKED"},
    "NEEDS_CLARIFICATION": {"PLANNED", "BLOCKED"},
    "DISPATCHED": {"IN_PROGRESS", "BLOCKED"},
    "IN_PROGRESS": {"REVIEWING", "BLOCKED", "NEEDS_DIRECTION", "PAUSE_REQUESTED"},
    "NEEDS_DIRECTION": {"IN_PROGRESS", "BLOCKED"},
    "REVIEWING": {"ACCEPTED", "IN_PROGRESS", "BLOCKED", "PAUSE_REQUESTED"},
    "BLOCKED": {"IN_PROGRESS", "REVIEWING", "PAUSE_REQUESTED", "UNKNOWN"},
    "PAUSE_REQUESTED": {"PAUSED", "BLOCKED"},
    "PAUSED": {"IN_PROGRESS", "REVIEWING", "BLOCKED"},
    "ACCEPTED": {"INTEGRATED", "BLOCKED"},
    "INTEGRATED": {"RELEASED", "BLOCKED"},
    "RELEASED": {"CLOSED", "BLOCKED"},
    "UNKNOWN": {"BLOCKED"},
    "CLOSED": set(),
}


def next_state(
    current: str,
    target: str,
    resume_state: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    if target not in ALLOWED.get(current, set()):
        raise TransitionError("illegal transition: %s -> %s" % (current, target))

    if target == "PAUSE_REQUESTED":
        return target, current

    if current == "PAUSED":
        if target != resume_state:
            raise TransitionError("illegal transition: %s -> %s" % (current, target))
        return target, None

    return target, resume_state
