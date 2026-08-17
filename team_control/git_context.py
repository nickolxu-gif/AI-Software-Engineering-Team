import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import BoundaryError, GitStateError


COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SYSTEM_GIT_PATH = Path("/usr/bin/git")
GIT_DISCOVERY_ENV = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}


def system_git_executable():
    try:
        metadata = SYSTEM_GIT_PATH.lstat()
    except OSError as error:
        raise GitStateError("system git executable is unavailable") from error
    if (
        not metadata.st_mode & 0o111
        or not SYSTEM_GIT_PATH.is_file()
        or SYSTEM_GIT_PATH.is_symlink()
    ):
        raise GitStateError("system git executable is unavailable")
    return str(SYSTEM_GIT_PATH)


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


def run_argv(
    argv,
    cwd,
    check=True,
    env_overrides=None,
    inherit_env=True,
):
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
    if any(
        "=" in key or "\0" in key or "\0" in value
        for key, value in overrides.items()
    ):
        raise BoundaryError("environment overrides contain invalid characters")
    if not isinstance(inherit_env, bool):
        raise BoundaryError("inherit_env must be a boolean")
    environment = dict(os.environ) if inherit_env else {}
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
        executable = system_git_executable()
        top = Path(run_argv(
            [executable, "rev-parse", "--show-toplevel"], start,
            env_overrides=GIT_DISCOVERY_ENV, inherit_env=False,
        ).stdout.strip()).resolve(strict=True)
        common_raw = Path(run_argv(
            [executable, "rev-parse", "--git-common-dir"], start,
            env_overrides=GIT_DISCOVERY_ENV, inherit_env=False,
        ).stdout.strip())
        common = common_raw if common_raw.is_absolute() else start / common_raw
        return cls(root=top, common_dir=common.resolve(strict=True))

    @property
    def runtime_dir(self):
        return self.common_dir / "team" / "runtime"
