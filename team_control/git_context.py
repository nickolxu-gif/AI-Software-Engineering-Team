import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import BoundaryError, GitStateError


COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_component(value, label):
    if not COMPONENT_RE.fullmatch(value) or ".." in value:
        raise BoundaryError("%s must match %s and not contain '..'" % (label, COMPONENT_RE.pattern))
    return value


def canonical_under(root, candidate):
    resolved_root = Path(root).resolve(strict=True)
    resolved_candidate = Path(candidate).resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise BoundaryError("path escapes registered root: %s" % resolved_candidate) from exc
    return resolved_candidate


def run_argv(argv, cwd, check=True, env_overrides=None):
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not all(isinstance(value, str) for value in argv)
    ):
        raise BoundaryError(
            "subprocess arguments must be a non-empty string argv"
        )
    overrides = {} if env_overrides is None else env_overrides
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in overrides.items()
    ):
        raise BoundaryError("environment overrides must be string pairs")
    environment = dict(os.environ)
    environment.update(overrides)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise GitStateError("command failed to start: %s" % exc) from exc
    if check and completed.returncode != 0:
        raise GitStateError(
            "command failed (%s): %s"
            % (completed.returncode, completed.stderr.strip())
        )
    return completed


@dataclass(frozen=True)
class RepoContext:
    root: Path
    common_dir: Path

    @classmethod
    def discover(cls, candidate):
        start = Path(candidate).resolve(strict=True)
        top = Path(run_argv(["git", "rev-parse", "--show-toplevel"], start).stdout.strip()).resolve(strict=True)
        common_raw = Path(run_argv(["git", "rev-parse", "--git-common-dir"], start).stdout.strip())
        common = common_raw if common_raw.is_absolute() else start / common_raw
        return cls(root=top, common_dir=common.resolve(strict=True))

    @property
    def runtime_dir(self):
        return self.common_dir / "team" / "runtime"
