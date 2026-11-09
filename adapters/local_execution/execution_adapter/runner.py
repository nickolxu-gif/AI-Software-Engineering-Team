"""One-shot subprocess runner. No retries, no fallback, no schema invention."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from typing import Any

from execution_adapter.bindings import (
    SCHEMA_VERSION,
    BindingError,
    authorize,
    build_invocation,
    sha256_text,
)
from execution_adapter.catalog import RESOURCES
from execution_adapter.redact import redact


def observe_output(stdout: str) -> dict:
    """Record whether lines parse. Do not map unevidenced event fields."""

    json_lines = 0
    other_lines = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            other_lines += 1
        else:
            json_lines += 1
    return {
        "observed_model": "UNKNOWN",
        "usage": "UNKNOWN",
        "jsonl_lines": json_lines,
        "non_json_lines": other_lines,
        "schema_interpreted": False,
    }


def _bounded(text: str, limit: int | None) -> tuple[str, bool]:
    if limit is None or len(text.encode("utf-8")) <= limit:
        return text, False
    raw = text.encode("utf-8")[:limit]
    return raw.decode("utf-8", errors="ignore"), True


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = subprocess.check_output(["ps", "-p", str(pid), "-o", "state="], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return True
    return not state.startswith("Z")


def _descendants(pid: int) -> list[int] | str:
    try:
        listing = subprocess.check_output(["ps", "-ax", "-o", "pid=,ppid="], text=True)
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"
    children: dict[int, list[int]] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)
    found = []
    seen = {pid}
    stack = [pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            stack.append(child)
    return found


def _reap_group(pgid: int) -> list[int]:
    reaped = []
    while True:
        try:
            waited, _status = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            break
        if waited == 0:
            break
        reaped.append(waited)
    return reaped


def _kill_process_group(pid: int) -> dict:
    descendants = _descendants(pid)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    reaped = _reap_group(pid)
    known = [pid] if isinstance(descendants, list) else []
    if isinstance(descendants, list):
        known.extend(descendants)
    alive = [item for item in known if _alive(item)]
    return {
        "spawned_pid": pid,
        "descendants_before_kill": descendants,
        "reaped_pids": reaped,
        "alive_after_cleanup": alive,
    }


def _run_process(invocation: dict) -> dict:
    timeout = invocation["timeout_seconds"]
    try:
        proc = subprocess.Popen(
            invocation["argv"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=invocation["cwd"],
            env=invocation["env"],
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "functional_state": "spawn_failed",
            "exit_code": None,
            "stdout": "",
            "stderr": redact(str(exc)),
            "stdout_truncated": False,
            "stderr_truncated": False,
            "child_termination": "spawn_failed",
            "spawned_pid": None,
            "cleanup": {
                "spawned_pid": None,
                "descendants_before_kill": [],
                "reaped_pids": [],
                "alive_after_cleanup": [],
            },
            "write_outcome": "known",
            "duration_observed": "UNKNOWN",
        }
    spawned_pid = proc.pid
    stdin = invocation["stdin"].encode("utf-8")
    try:
        stdout_b, stderr_b = proc.communicate(input=stdin, timeout=timeout)
        termination = "exited"
        write_outcome = "known"
        cleanup = {
            "spawned_pid": spawned_pid,
            "descendants_before_kill": [],
            "reaped_pids": [spawned_pid],
            "alive_after_cleanup": [],
        }
    except subprocess.TimeoutExpired:
        cleanup = _kill_process_group(spawned_pid)
        try:
            stdout_b, stderr_b = proc.communicate()
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_b, stderr_b = proc.communicate()
        termination = "timed_out_killed"
        write_outcome = "unknown"
    stdout, out_cut = _bounded(stdout_b.decode("utf-8", errors="replace"), invocation["max_output_bytes"])
    stderr, err_cut = _bounded(stderr_b.decode("utf-8", errors="replace"), invocation["max_output_bytes"])
    return {
        "functional_state": "timed_out_killed" if termination == "timed_out_killed" else "exited",
        "exit_code": proc.returncode,
        "stdout": redact(stdout),
        "stderr": redact(stderr),
        "stdout_truncated": out_cut,
        "stderr_truncated": err_cut,
        "child_termination": termination,
        "spawned_pid": spawned_pid,
        "cleanup": cleanup,
        "write_outcome": write_outcome,
        "duration_observed": "UNKNOWN",
    }


def dispatch(task: dict, recommendation: dict, approval: dict | None, config: dict) -> dict:
    authorize(task, recommendation, approval)
    assert approval is not None
    prompt = task["prompt"]
    invocation = build_invocation(approval["resource_alias"], config, approval, prompt)
    observed_process = _run_process(invocation)
    parsed = observe_output(observed_process["stdout"])
    alias = invocation["alias"]
    declared = RESOURCES[alias]
    resource = config["resources"][alias]
    versions = task["versions"]
    return {
        "schema_version": SCHEMA_VERSION,
        "live_integration_claimed": False,
        "execution_label": config.get("execution_label", "unverified"),
        "simulated_fixture": config.get("execution_label") == "fixture",
        "request_count": 1,
        "attempts": 1,
        "retried": False,
        "fallback_used": False,
        "task_id": task["task_id"],
        "role": task["author_role"],
        "review_role": task["review_role"],
        "review_session": task.get("review_session"),
        "independent_review": {
            "separated_from_author": task["author_role"] != task["review_role"],
            "proves_distinct_model_or_account": False,
        },
        "tool": invocation["tool"],
        "resource_alias": alias,
        "requested_model_alias": approval.get("model_alias") if approval.get("authorize_model_alias") else None,
        "observed_model": parsed["observed_model"],
        "account_ref": resource.get("account_ref") or "UNKNOWN",
        "client_version_declared": invocation["declared_version"],
        "client_version_observed": "UNKNOWN",
        "prompt_sha256": sha256_text(prompt),
        "stdout_sha256": sha256_text(observed_process["stdout"]),
        "stdout": observed_process["stdout"],
        "stderr": observed_process["stderr"],
        "stdout_truncated": observed_process["stdout_truncated"],
        "stderr_truncated": observed_process["stderr_truncated"],
        "jsonl_lines": parsed["jsonl_lines"],
        "schema_interpreted": False,
        "exit_code": observed_process["exit_code"],
        "functional_state": observed_process["functional_state"],
        "child_termination": observed_process["child_termination"],
        "spawned_pid": observed_process["spawned_pid"],
        "cleanup": observed_process["cleanup"],
        "write_outcome": observed_process["write_outcome"],
        "duration_observed": "UNKNOWN",
        "allowed_data_scope": task["data_scope"],
        "private_data_allowed": False,
        "telemetry_completeness": "gap",
        "metering": {
            "completeness": "gap",
            "per_request_cost": "UNKNOWN",
            "input_tokens": "UNKNOWN",
            "output_tokens": "UNKNOWN",
            "cache_tokens": "UNKNOWN",
            "shared_pool_attribution": "not_observed",
            "duplicate_usage_records": "not_duplicated",
            "reason": "event_schema_not_evidenced",
        },
        "usage": "UNKNOWN",
        "timeout_proves_remote_billing_stopped": False,
        "codex_read_only_is_full_isolation": False,
        "ephemeral_proves_no_logs_or_network": False,
        "empty_workspace_is_sandbox": False,
        "skills_dir_is_sandbox": False,
        "separate_configuration_home_is_account_isolation": False,
        "separate_configuration_home_is_model_heterogeneity": False,
        "distinct_model_or_account": declared.get("distinct_model_or_account", False),
        "approval_bypass_used": False,
        "approval_detected": "UNKNOWN",
        "sandbox_claim": "none",
        "versions": {
            "task": versions["task"],
            "source": versions["source"],
            "plan": versions["plan"],
            "correction_of": versions.get("correction_of"),
        },
        "generation_binding": declared.get("generation_binding"),
        "argv": invocation["argv"],
        "unfinished": [] if observed_process["exit_code"] == 0 and observed_process["write_outcome"] == "known" else ["run_not_clean"],
    }


def refusal(error: BindingError) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "live_integration_claimed": False,
        "code": error.code,
        "detail": error.detail,
        "request_count": 0,
        "retried": False,
        "fallback_used": False,
    }
