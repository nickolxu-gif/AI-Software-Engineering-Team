"""Recommendation, authorization, and help-verified argv construction.

A recommendation never dispatches. Numeric limits are taken only from the
approval package. Missing limits stay unbound instead of becoming defaults.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(secret|token|password|passwd|credential|cookie|session|api[_-]?key|private[_-]?key|access[_-]?key|authorization)",
    re.IGNORECASE,
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENV = {"PATH"}

from execution_adapter.catalog import RESOURCES

SCHEMA_VERSION = "2026-11-08"
CONTROL_KEYS = {
    "agent_file",
    "argv",
    "binary",
    "codex_home",
    "config_path",
    "env",
    "executable",
    "permissions",
    "shell",
    "skills_dir",
}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class BindingError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def recommend(task: dict, config: dict) -> dict:
    alias = task.get("preferred_resource")
    result = {
        "schema_version": SCHEMA_VERSION,
        "recommended": False,
        "dispatch_approved": False,
        "autonomous_dispatch": False,
        "quota": "UNKNOWN",
        "per_request_cost": "UNKNOWN",
        "metering_completeness": "gap",
        "resource_alias": alias,
        "generation_binding": None,
    }
    resources = config.get("resources") or {}
    resource = resources.get(alias)
    if resource is None:
        result["reason"] = "unknown_resource"
        return result
    kind = resource.get("kind")
    declared = RESOURCES.get(alias, {})
    if kind == "paused" or declared.get("kind") == "paused":
        result["reason"] = "paused"
        result["globally_unavailable"] = False
        return result
    if kind == "disabled" or declared.get("kind") == "disabled":
        result["reason"] = "disabled_not_probed"
        return result
    if kind == "unsupported_config_needed" or declared.get("kind") == "unsupported_config_needed":
        result["reason"] = "unsupported_config_needed"
        return result
    if kind == "external_orchestrator" or declared.get("kind") == "external_orchestrator":
        result["reason"] = "external_to_local_runner"
        return result
    if not resource.get("enabled"):
        result["reason"] = "not_enabled"
        return result
    result.update(
        {
            "recommended": True,
            "tool": kind,
            "requested_model_alias": resource.get("model_alias"),
            "generation_binding": declared.get("generation_binding"),
            "reason": "enabled_in_trusted_config",
            "distinct_model_or_account": declared.get("distinct_model_or_account", False),
        }
    )
    return result


def _reject_controls(payload: dict, where: str) -> None:
    found = sorted(CONTROL_KEYS.intersection(payload))
    if found:
        raise BindingError("task_control_override", f"{where}:{','.join(found)}")


def authorize(task: dict, recommendation: dict, approval: dict | None) -> None:
    _reject_controls(task, "task")
    if approval:
        _reject_controls(approval, "approval")
    if not recommendation.get("recommended") or recommendation.get("dispatch_approved") is not False:
        raise BindingError("not_recommended", str(recommendation.get("reason")))
    if not approval or approval.get("approved") is not True:
        raise BindingError("recommendation_not_approved", "approved")
    if approval.get("task_id") != task.get("task_id"):
        raise BindingError("approval_mismatch", "task_id")
    if approval.get("resource_alias") != recommendation.get("resource_alias"):
        raise BindingError("approval_mismatch", "resource_alias")
    if not isinstance(approval.get("authority_ref"), str) or not approval.get("authority_ref"):
        raise BindingError("authority_missing", "authority_ref")
    if task.get("private_data") is True or task.get("data_scope") != "public_or_synthetic":
        raise BindingError("private_data_prohibited", "data_scope")
    if approval.get("covers_private_data") is True:
        raise BindingError("private_data_prohibited", "approval")
    versions = task.get("versions") or {}
    for key in ("task", "source", "plan"):
        if not versions.get(key):
            raise BindingError("version_missing", key)
    if versions.get("correction_of") and versions.get("prior_write_outcome") != "known":
        raise BindingError("unknown_write_outcome_not_replayed", "prior_write_outcome")
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or prompt == "":
        raise BindingError("prompt_missing", "prompt")
    digest = sha256_text(prompt)
    if approval.get("prompt_sha256") != digest:
        raise BindingError("approval_mismatch", "prompt_sha256")
    if not task.get("author_role") or not task.get("review_role"):
        raise BindingError("role_missing", "author_or_review")
    if task.get("author_role") == task.get("review_role") and task.get("review_session") in (None, "", "same"):
        raise BindingError("author_review_not_separated", "review_role")


def _workspace(approval: dict) -> str:
    raw = approval.get("workspace")
    if not isinstance(raw, str) or not raw or raw.startswith("-") or "\n" in raw or "\x00" in raw:
        raise BindingError("workspace_invalid", "workspace")
    real = os.path.realpath(raw)
    if not os.path.isdir(real):
        raise BindingError("workspace_invalid", "not_a_directory")
    return real


def _positive_int(value: Any, code: str) -> str:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise BindingError(code, "missing_or_invalid")
    return str(value)


def _optional_name(value: Any, code: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not NAME_RE.fullmatch(value) or value.startswith("-"):
        raise BindingError(code, "invalid_name")
    return value


def _resolve_binary(resource: dict, config: dict) -> str:
    binary = resource.get("binary")
    allow = set(config.get("binary_allowlist") or [])
    if not isinstance(binary, str) or binary not in allow:
        raise BindingError("binary_not_allowlisted", "binary")
    if os.path.isabs(binary):
        if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
            raise BindingError("binary_not_allowlisted", "not_executable")
        return os.path.realpath(binary)
    if "/" in binary or binary.startswith("."):
        raise BindingError("binary_not_allowlisted", "relative_binary")
    if not config.get("allow_path_lookup"):
        raise BindingError("binary_not_allowlisted", "path_lookup_disabled")
    import shutil

    found = shutil.which(binary)
    if not found:
        raise BindingError("binary_not_allowlisted", "not_found")
    return os.path.realpath(found)


def _artifact_path(approval: dict, key: str) -> str | None:
    raw = approval.get(key)
    if raw in (None, ""):
        return None
    root = approval.get("artifact_root")
    if not isinstance(raw, str) or not isinstance(root, str):
        raise BindingError("artifact_scope_required", key)
    path = os.path.realpath(raw)
    base = os.path.realpath(root)
    try:
        inside = os.path.commonpath([path, base]) == base
    except ValueError:
        inside = False
    if not inside:
        raise BindingError("artifact_outside_scope", key)
    return path


def _looks_like_secret(key: str) -> bool:
    return _SECRET_KEY.search(key) is not None


def _declared_env(resource: dict) -> tuple[dict, list[str]]:
    declared = resource.get("env") or {}
    if not isinstance(declared, dict):
        raise BindingError("env_invalid", "env")
    extra = {}
    for key, raw in declared.items():
        if not isinstance(key, str) or not _ENV_NAME.fullmatch(key) or key in _RESERVED_ENV:
            raise BindingError("env_key_rejected", str(key))
        marked_secret = False
        if isinstance(raw, dict):
            if set(raw) != {"value", "secret"} or raw.get("secret") is not True:
                raise BindingError("env_secret_unmarked", key)
            marked_secret = True
            value = raw.get("value")
        else:
            value = raw
        if not isinstance(value, str) or value == "" or "\n" in value or "\x00" in value:
            raise BindingError("env_value_rejected", key)
        if _looks_like_secret(key) and not marked_secret:
            raise BindingError("env_secret_unmarked", key)
        extra[key] = value
    return extra, sorted(extra)


def _env_for(alias: str, resource: dict) -> tuple[dict, list[str]]:
    env = {}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    injected = []
    home = resource.get("codex_home")
    if alias == "codex2":
        if not isinstance(home, str) or not os.path.isabs(home) or not os.path.isdir(home):
            raise BindingError("configuration_home_required", "codex2")
        env["CODEX_HOME"] = os.path.realpath(home)
        injected.append("CODEX_HOME")
    elif alias == "codex" and home:
        if not isinstance(home, str) or not os.path.isabs(home) or not os.path.isdir(home):
            raise BindingError("configuration_home_required", "codex")
        env["CODEX_HOME"] = os.path.realpath(home)
        injected.append("CODEX_HOME")
    extra, names = _declared_env(resource)
    overlap = sorted(set(extra).intersection(env))
    if overlap:
        raise BindingError("env_key_rejected", ",".join(overlap))
    env.update(extra)
    injected.extend(names)
    return env, injected


def _skills_directory(resource: dict, config: dict) -> tuple[str | None, bool | None]:
    raw = resource.get("skills_dir")
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, str) or not os.path.isabs(raw):
        raise BindingError("skills_dir_invalid", "skills_dir")
    root = config.get("instance_root")
    if not isinstance(root, str) or not os.path.isabs(root) or not os.path.isdir(root):
        raise BindingError("instance_root_required", "instance_root")
    path = os.path.realpath(raw)
    base = os.path.realpath(root)
    if not os.path.isdir(path):
        raise BindingError("skills_dir_invalid", "not_a_directory")
    try:
        inside = os.path.commonpath([path, base]) == base
    except ValueError:
        inside = False
    if not inside:
        raise BindingError("skills_dir_outside_root", "skills_dir")
    empty = next(os.scandir(path), None) is None
    return path, empty


def build_invocation(alias: str, config: dict, approval: dict, prompt: str) -> dict:
    resources = config.get("resources") or {}
    resource = resources.get(alias)
    declared = RESOURCES.get(alias)
    if resource is None or declared is None:
        raise BindingError("unknown_resource", str(alias))
    kind = declared["kind"]
    if kind == "disabled":
        raise BindingError("disabled_not_probed", alias)
    if kind == "paused":
        raise BindingError("paused", alias)
    if kind == "unsupported_config_needed":
        raise BindingError("unsupported_config_needed", alias)
    if kind == "external_orchestrator":
        raise BindingError("external_to_local_runner", alias)
    if not resource.get("enabled"):
        raise BindingError("not_enabled", alias)
    if declared.get("generation_binding") != "help_verified":
        raise BindingError("generation_binding_null", alias)
    executable = _resolve_binary(resource, config)
    workspace = _workspace(approval)
    child_env, injected_keys = _env_for(alias, resource)
    skills_path, skills_empty = _skills_directory(resource, config)
    argv = _argv_for(alias, executable, resource, approval, prompt, workspace, skills_path)
    return {
        "alias": alias,
        "argv": argv,
        "stdin": prompt if declared.get("stdin") == "approved_prompt" else "",
        "env": child_env,
        "injected_env_keys": injected_keys,
        "skills_dir_empty": skills_empty,
        "cwd": workspace,
        "timeout_seconds": _optional_timeout(approval),
        "max_output_bytes": _optional_output_cap(approval),
        "tool": kind,
        "declared_version": declared.get("supported_version_declared"),
    }


def _optional_timeout(approval: dict):
    if "process_timeout_seconds" not in approval or approval.get("process_timeout_seconds") is None:
        return None
    return int(_positive_int(approval.get("process_timeout_seconds"), "process_timeout_seconds"))


def _optional_output_cap(approval: dict):
    if "max_output_bytes" not in approval or approval.get("max_output_bytes") is None:
        return None
    return int(_positive_int(approval.get("max_output_bytes"), "max_output_bytes"))


def _argv_for(
    alias: str,
    executable: str,
    resource: dict,
    approval: dict,
    prompt: str,
    workspace: str,
    skills_path: str | None,
) -> list[str]:
    if alias in {"codex", "codex2"}:
        return _codex_argv(executable, approval, workspace)
    if alias == "hermes":
        return _hermes_argv(executable, approval, workspace)
    if alias == "kimi":
        return _kimi_argv(executable, resource, approval, prompt, skills_path)
    raise BindingError("generation_binding_null", alias)


def _codex_argv(executable: str, approval: dict, workspace: str) -> list[str]:
    argv = [
        executable,
        "-c",
        "check_for_update_on_startup=false",
        "exec",
    ]
    if approval.get("authorize_model_alias"):
        model = _optional_name(approval.get("model_alias"), "model_alias")
        if not model:
            raise BindingError("model_alias_missing", "model_alias")
        argv.extend(["-m", model])
    if approval.get("authorize_profile"):
        profile = _optional_name(approval.get("profile"), "profile")
        if not profile:
            raise BindingError("profile_missing", "profile")
        argv.extend(["-p", profile])
    argv.extend(["--json", "--color", "never", "--ephemeral", "-s", "read-only", "-C", workspace])
    final_message = _artifact_path(approval, "final_message_path")
    schema = _artifact_path(approval, "output_schema_path")
    if final_message:
        argv.extend(["-o", final_message])
    if schema:
        argv.extend(["--output-schema", schema])
    argv.append("-")
    return argv


def _hermes_argv(executable: str, approval: dict, workspace: str) -> list[str]:
    turns = _positive_int(approval.get("max_turns"), "approved_limits_missing")
    budget = _positive_int(approval.get("run_budget_seconds"), "approved_limits_missing")
    argv = [executable]
    if approval.get("authorize_profile"):
        profile = _optional_name(approval.get("profile"), "profile")
        if not profile:
            raise BindingError("profile_missing", "profile")
        argv.extend(["-p", profile])
    argv.append("chat")
    if approval.get("authorize_model_alias"):
        model = _optional_name(approval.get("model_alias"), "model_alias")
        if not model:
            raise BindingError("model_alias_missing", "model_alias")
        argv.extend(["-m", model])
    if approval.get("authorize_provider"):
        provider = _optional_name(approval.get("provider"), "provider")
        if not provider:
            raise BindingError("provider_missing", "provider")
        argv.extend(["--provider", provider])
    argv.extend(
        [
            "--query-file",
            "-",
            "--oneshot",
            "--format",
            "stream-json",
            "--in",
            workspace,
            "--max-turns",
            turns,
            "--run-budget",
            budget,
        ]
    )
    return argv


def _kimi_argv(executable: str, resource: dict, approval: dict, prompt: str, skills_path: str | None) -> list[str]:
    argv = [executable]
    if approval.get("authorize_model_alias"):
        model = _optional_name(approval.get("model_alias"), "model_alias")
        if not model:
            raise BindingError("model_alias_missing", "model_alias")
        argv.extend(["-m", model])
    argv.extend(["-p", prompt, "--output-format", "stream-json"])
    if skills_path:
        argv.extend(["--skills-dir", skills_path])
    raw = resource.get("agent_file")
    if raw not in (None, ""):
        if not isinstance(raw, str) or not os.path.isabs(raw) or not os.path.isfile(raw):
            raise BindingError("kimi_profile_path_invalid", "agent_file")
        argv.extend(["--agent-file", os.path.realpath(raw)])
    return argv
