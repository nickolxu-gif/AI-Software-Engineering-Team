"""Offline fixture tests. No installed client and no model call is used."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from execution_adapter.bindings import BindingError, build_invocation, recommend, sha256_text
from execution_adapter.catalog import (
    ADAPTER_VERSION,
    CODEX_FORBIDDEN_FLAGS,
    HERMES_FORBIDDEN_FLAGS,
    RESOURCES,
)
from execution_adapter.package_hash import source_hash
from execution_adapter.runner import dispatch
from execution_adapter.smoke import run_offline_smoke


def write_fake(path: Path, side: Path, mode_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, os, sys, time",
                "from pathlib import Path",
                f"side = Path({str(side)!r})",
                f"mode = json.loads(Path({str(mode_path)!r}).read_text())",
                "prev = json.loads(side.read_text()) if side.exists() else []",
                "record = {'argv': sys.argv, 'stdin': None, 'home': os.environ.get('CODEX_HOME'), 'pid': os.getpid(), 'seen': {name: os.environ.get(name) for name in ('DISABLE_AUTO_UPDATE', 'FIXTURE_PARENT_ONLY', 'MARKED_SECRET')}}",
                "prev.append(record)",
                "side.write_text(json.dumps(prev))",
                "record['stdin'] = sys.stdin.read()",
                "side.write_text(json.dumps(prev))",
                "action = mode.get('action')",
                "if action == 'sleep':",
                "    time.sleep(mode['seconds'])",
                "sys.stdout.write(mode.get('stdout', ''))",
                "sys.stderr.write(mode.get('stderr', ''))",
                "raise SystemExit(mode.get('code', 0))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def canon(path) -> str:
    return os.path.realpath(path)


def config_for(binary: str, home: str | None = None) -> dict:
    return {
        "execution_label": "fixture",
        "binary_allowlist": [binary],
        "resources": {
            "codex": {"enabled": True, "kind": "codex_exec", "binary": binary, "codex_home": None, "model_alias": None, "account_ref": None},
            "codex2": {"enabled": True, "kind": "codex_exec", "binary": binary, "codex_home": home, "model_alias": None, "account_ref": "opaque-fixture"},
            "kimi": {"enabled": True, "kind": "kimi_cli", "binary": binary, "skills_dir": None, "agent_file": None, "account_ref": None},
            "hermes": {"enabled": True, "kind": "hermes_chat", "binary": binary, "account_ref": None},
            "cursor": {"enabled": True, "kind": "external_orchestrator", "binary": binary},
            "dsh": {"enabled": True, "kind": "unsupported_config_needed", "binary": binary},
            "openclaw": {"enabled": True, "kind": "paused", "binary": binary},
            "claude": {"enabled": True, "kind": "disabled", "binary": binary},
        },
    }


def make_task(alias: str, prompt: str = "Summarize the synthetic note.") -> dict:
    return {
        "task_id": "SYN-READ-01",
        "prompt": prompt,
        "data_scope": "public_or_synthetic",
        "private_data": False,
        "preferred_resource": alias,
        "author_role": "reading-arrangement",
        "review_role": "knowledge-content-acceptance",
        "review_session": "separate-fixture-session",
        "versions": {"task": "v2", "source": "v2", "plan": "v1", "correction_of": None, "prior_write_outcome": None},
    }


def make_approval(task: dict, alias: str, workspace: str, **extra) -> dict:
    body = {
        "schema_version": "2026-11-08",
        "task_id": task["task_id"],
        "resource_alias": alias,
        "approved": True,
        "authority_ref": "fixture-authority",
        "covers_private_data": False,
        "prompt_sha256": sha256_text(task["prompt"]),
        "workspace": workspace,
    }
    body.update(extra)
    return body


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.binary = self.root / "fake-client"
        self.side = self.root / "calls.json"
        self.mode = self.root / "mode.json"
        self.mode.write_text(json.dumps({"action": "print", "stdout": "model: configured-alias\n{\"model\":\"from-json\",\"usage\":{\"input_tokens\":9,\"cost\":1}}\n", "code": 0}), encoding="utf-8")
        write_fake(self.binary, self.side, self.mode)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.home_a = self.root / "home-a"
        self.home_b = self.root / "home-b"
        self.home_a.mkdir()
        self.home_b.mkdir()
        (self.home_a / "auth.json").write_text("do-not-copy", encoding="utf-8")
        self.config = config_for(str(self.binary), str(self.home_b))

    def tearDown(self):
        self.tmp.cleanup()

    def assert_not_running(self, pid: int):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        try:
            state = subprocess.check_output(["ps", "-p", str(pid), "-o", "state="], text=True).strip()
        except subprocess.CalledProcessError:
            return
        self.assertTrue(state.startswith("Z"), f"pid {pid} still running: {state}")

    def calls(self):
        if not self.side.exists():
            return []
        return json.loads(self.side.read_text(encoding="utf-8"))

    def dispatch_alias(self, alias: str, task=None, approval_extra=None, config=None):
        task = task or make_task(alias)
        recommendation = recommend(task, config or self.config)
        extra = {} if approval_extra is None else approval_extra
        if alias == "hermes":
            extra.setdefault("max_turns", 2)
        if alias == "kimi":
            extra.setdefault("authorize_model_alias", True)
            extra.setdefault("model_alias", "fixture-alias")
        approval = make_approval(task, alias, str(self.workspace), **extra)
        return recommendation, dispatch(task, recommendation, approval, config or self.config)

    def test_catalog_leaves_unevidenced_bindings_null(self):
        self.assertIsNone(RESOURCES["dsh"]["argv_template"])
        self.assertIsNone(RESOURCES["dsh"]["generation_binding"])
        self.assertEqual(RESOURCES["dsh"]["launch_contract"], "not_evidenced")
        self.assertIsNone(RESOURCES["cursor"]["argv_template"])
        self.assertFalse(RESOURCES["cursor"]["local_runner"])
        self.assertIsNone(RESOURCES["openclaw"]["argv_template"])
        self.assertFalse(RESOURCES["openclaw"]["globally_unavailable"])
        self.assertIsNone(RESOURCES["claude"]["argv_template"])
        self.assertFalse(RESOURCES["claude"]["probe"])
        self.assertEqual(RESOURCES["codex2"]["argv_template"], RESOURCES["codex"]["argv_template"])
        self.assertFalse(RESOURCES["codex2"]["distinct_model_or_account"])

    def test_recommendation_does_not_dispatch_or_approve(self):
        task = make_task("codex")
        recommendation = recommend(task, self.config)
        self.assertTrue(recommendation["recommended"])
        self.assertFalse(recommendation["dispatch_approved"])
        self.assertFalse(recommendation["autonomous_dispatch"])
        self.assertEqual(recommendation["quota"], "UNKNOWN")
        self.assertEqual(self.calls(), [])
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommendation, None, self.config)
        self.assertEqual(caught.exception.code, "recommendation_not_approved")
        self.assertEqual(self.calls(), [])

    def test_approval_mismatch_and_private_data_do_not_spawn(self):
        task = make_task("codex")
        recommendation = recommend(task, self.config)
        bad = make_approval(task, "kimi", str(self.workspace))
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommendation, bad, self.config)
        self.assertEqual(caught.exception.code, "approval_mismatch")
        private = make_task("codex")
        private["data_scope"] = "private"
        with self.assertRaises(BindingError) as caught:
            dispatch(private, recommend(private, self.config), make_approval(private, "codex", str(self.workspace)), self.config)
        self.assertEqual(caught.exception.code, "private_data_prohibited")
        self.assertEqual(self.calls(), [])

    def test_prompt_cannot_change_binary_or_flags(self):
        prompt = "$(touch " + str(self.root / "pwned") + "); --binary /tmp/evil"
        task = make_task("kimi", prompt)
        task["binary"] = "/tmp/evil"
        recommendation = recommend(task, self.config)
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommendation, make_approval(task, "kimi", str(self.workspace), authorize_model_alias=True, model_alias="fixture-alias"), self.config)
        self.assertEqual(caught.exception.code, "task_control_override")
        self.assertFalse((self.root / "pwned").exists())
        task.pop("binary")
        _, receipt = self.dispatch_alias("kimi", task)
        self.assertFalse((self.root / "pwned").exists())
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("-p") + 1], prompt)
        self.assertNotIn("/tmp/evil", argv)
        self.assertEqual(receipt["argv"][0], canon(self.binary))

    def test_codex_and_codex2_argv_and_home_separation(self):
        _, receipt = self.dispatch_alias("codex")
        call = self.calls()[0]
        self.assertEqual(
            call["argv"],
            [
                canon(self.binary),
                "-c",
                "check_for_update_on_startup=false",
                "exec",
                "--json",
                "--color",
                "never",
                "--ephemeral",
                "-s",
                "read-only",
                "-C",
                canon(self.workspace),
                "-",
            ],
        )
        self.assertEqual(call["stdin"], "Summarize the synthetic note.")
        self.assertIsNone(call["home"])
        self.assertEqual(receipt["observed_model"], "UNKNOWN")
        self.assertNotEqual(receipt["observed_model"], "from-json")
        self.assertNotIn("configured-alias", receipt["observed_model"])
        self.assertEqual(receipt["metering"]["per_request_cost"], "UNKNOWN")
        self.assertEqual(receipt["metering"]["input_tokens"], "UNKNOWN")
        self.assertFalse(receipt["codex_read_only_is_full_isolation"])
        self.assertFalse(receipt["ephemeral_proves_no_logs_or_network"])
        self.assertFalse(receipt["timeout_proves_remote_billing_stopped"])
        for flag in CODEX_FORBIDDEN_FLAGS:
            self.assertNotIn(flag, call["argv"])
        _, second = self.dispatch_alias("codex2")
        other = self.calls()[1]
        self.assertEqual(other["argv"], call["argv"])
        self.assertEqual(other["home"], canon(self.home_b))
        self.assertFalse((self.home_b / "auth.json").exists())
        self.assertFalse(second["distinct_model_or_account"])
        self.assertFalse(second["separate_configuration_home_is_account_isolation"])
        self.assertEqual(second["account_ref"], "opaque-fixture")

    def test_codex2_without_home_does_not_spawn(self):
        config = config_for(str(self.binary), None)
        task = make_task("codex2")
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommend(task, config), make_approval(task, "codex2", str(self.workspace)), config)
        self.assertEqual(caught.exception.code, "configuration_home_required")
        self.assertEqual(self.calls(), [])

    def test_hermes_contract_and_missing_limits(self):
        task = make_task("hermes")
        recommendation = recommend(task, self.config)
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommendation, make_approval(task, "hermes", str(self.workspace)), self.config)
        self.assertEqual(caught.exception.code, "approved_limits_missing")
        self.assertEqual(self.calls(), [])
        _, receipt = self.dispatch_alias("hermes", approval_extra={"process_timeout_seconds": 5})
        argv = self.calls()[0]["argv"]
        self.assertEqual(
            argv,
            [
                canon(self.binary),
                "chat",
                "--query-file",
                "-",
                "--oneshot",
                "--format",
                "stream-json",
                "--in",
                canon(self.workspace),
                "--max-turns",
                "2",
            ],
        )
        for flag in ("-m", "--provider", "-p", "-z", "--usage-file", "--run-budget"):
            self.assertNotIn(flag, argv)
        for flag in HERMES_FORBIDDEN_FLAGS:
            self.assertNotIn(flag, argv)
        self.assertNotIn("--run-budget", RESOURCES["hermes"]["argv_template"])
        self.assertIn("--max-turns", RESOURCES["hermes"]["argv_template"])
        self.assertEqual(self.calls()[0]["stdin"], task["prompt"])
        self.assertFalse(receipt["approval_bypass_used"])
        self.assertEqual(receipt["approval_detected"], "UNKNOWN")
        self.assertEqual(receipt["client_version_declared"], "0.23.0")
        self.assertEqual(receipt["client_version_observed"], "UNKNOWN")
        self.assertEqual(receipt["generation_binding"], "gateway_allow_deny_checked")
        self.assertEqual(receipt["binding_basis"], "gateway_allow_deny")
        self.assertFalse(RESOURCES["hermes"]["help_text_is_executable_contract"])
        self.assertFalse(RESOURCES["hermes"]["writes_user_config"])
        self.assertEqual(receipt["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(ADAPTER_VERSION, "0.1.4")
        self.assertEqual(receipt["process_timeout_seconds"], 5)
        self.assertEqual(receipt["process_timeout_scope"], "client_process")
        self.assertFalse(receipt["client_close_proves_gateway_turn_stopped"])
        self.assertEqual(receipt["gateway_termination_semantics"], "incomplete")
        self.assertFalse(receipt["hermes_run_budget_accepted"])
        self.assertFalse(receipt["timeout_proves_remote_billing_stopped"])
        _, authorized = self.dispatch_alias(
            "hermes",
            approval_extra={"authorize_profile": True, "profile": "fixture-profile", "authorize_provider": True, "provider": "fixture-provider", "authorize_model_alias": True, "model_alias": "fixture-alias"},
        )
        selected = self.calls()[1]["argv"]
        self.assertEqual(
            selected,
            [
                canon(self.binary),
                "-p",
                "fixture-profile",
                "chat",
                "-m",
                "fixture-alias",
                "--provider",
                "fixture-provider",
                "--query-file",
                "-",
                "--oneshot",
                "--format",
                "stream-json",
                "--in",
                canon(self.workspace),
                "--max-turns",
                "2",
            ],
        )
        self.assertNotIn("--run-budget", selected)
        self.assertFalse(authorized["hermes_run_budget_accepted"])

    def test_retired_hermes_run_budget_approval_is_refused(self):
        task = make_task("hermes")
        recommendation = recommend(task, self.config)
        for extra in (
            {"run_budget_seconds": 3},
            {"max_turns": 2, "run_budget_seconds": 3},
            {"max_turns": 2, "run_budget_seconds": None},
        ):
            with self.assertRaises(BindingError) as caught:
                dispatch(task, recommendation, make_approval(task, "hermes", str(self.workspace), **extra), self.config)
            self.assertEqual(caught.exception.code, "hermes_run_budget_contract_retired")
            self.assertEqual(caught.exception.detail, "omit_run_budget_seconds")
        self.assertEqual(self.calls(), [])
        _, receipt = self.dispatch_alias("codex", approval_extra={"run_budget_seconds": 3})
        self.assertNotIn("--run-budget", receipt["argv"])
        self.assertNotIn("hermes_run_budget_accepted", receipt)

    def test_kimi_stream_json_contract(self):
        _, receipt = self.dispatch_alias("kimi")
        argv = self.calls()[0]["argv"]
        self.assertEqual(
            argv,
            [canon(self.binary), "-m", "fixture-alias", "-p", "Summarize the synthetic note.", "--output-format", "stream-json"],
        )
        self.assertEqual(self.calls()[0]["stdin"], "")
        self.assertEqual(receipt["observed_model"], "UNKNOWN")
        self.assertEqual(receipt["usage"], "UNKNOWN")
        self.assertEqual(receipt["schema_interpreted"], False)
        self.assertEqual(receipt["metering"]["completeness"], "gap")
        self.assertEqual(receipt["metering"]["shared_pool_attribution"], "not_observed")
        self.assertEqual(receipt["metering"]["duplicate_usage_records"], "not_duplicated")
        self.assertFalse(receipt["skills_dir_is_sandbox"])

    def test_failure_does_not_retry_or_fall_back(self):
        self.mode.write_text(json.dumps({"action": "print", "stdout": "", "stderr": "failed", "code": 3}), encoding="utf-8")
        _, receipt = self.dispatch_alias("codex")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(receipt["exit_code"], 3)
        self.assertFalse(receipt["retried"])
        self.assertFalse(receipt["fallback_used"])
        self.assertEqual(receipt["request_count"], 1)
        self.assertIn("run_not_clean", receipt["unfinished"])

    def test_timeout_kills_child_and_blocks_replay(self):
        group = self.root / "group-client"
        record = self.root / "group-record.json"
        group.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    "sleep 30 &",
                    "child=$!",
                    f"printf '%s\\n' \"$$ $child\" > {shlex.quote(str(record))}",
                    "wait",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        group.chmod(group.stat().st_mode | stat.S_IXUSR)
        config = config_for(str(group), str(self.home_b))
        holder = {}

        def invoke():
            try:
                holder["value"] = self.dispatch_alias(
                    "codex",
                    config=config,
                    approval_extra={"process_timeout_seconds": 1},
                )
            except Exception as exc:
                holder["error"] = exc

        thread = threading.Thread(target=invoke)
        thread.start()
        deadline = time.monotonic() + 5
        text = ""
        while time.monotonic() < deadline:
            if record.exists() and record.stat().st_size:
                text = record.read_text(encoding="utf-8").strip()
                if text:
                    break
            time.sleep(0.02)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", holder)
        self.assertTrue(text, "pid record did not appear within the finite wait")
        parent_text, child_text = text.split()
        parent_pid = int(parent_text)
        child_pid = int(child_text)
        _recommendation, receipt = holder["value"]
        self.assertEqual(receipt["child_termination"], "timed_out_killed")
        self.assertEqual(receipt["write_outcome"], "unknown")
        self.assertFalse(receipt["timeout_proves_remote_billing_stopped"])
        self.assertEqual(receipt["spawned_pid"], parent_pid)
        self.assertEqual(receipt["cleanup"]["alive_after_cleanup"], [])
        self.assertIn(child_pid, receipt["cleanup"]["descendants_before_kill"])
        for pid in (parent_pid, child_pid):
            self.assert_not_running(pid)
        follow = make_task("codex")
        follow["versions"]["correction_of"] = "v1"
        follow["versions"]["prior_write_outcome"] = "unknown"
        with self.assertRaises(BindingError) as caught:
            dispatch(follow, recommend(follow, config), make_approval(follow, "codex", str(self.workspace)), config)
        self.assertEqual(caught.exception.code, "unknown_write_outcome_not_replayed")
        self.assertFalse(record.with_name("group-record-replay.json").exists())
        self.assertEqual(text, record.read_text(encoding="utf-8").strip())

    def test_symlink_workspace_is_compared_by_realpath(self):
        link = self.root / "linked-work"
        os.symlink(self.workspace, link, target_is_directory=True)
        _recommendation, receipt = self.dispatch_alias("codex")
        linked = make_task("codex")
        linked["task_id"] = "SYN-READ-LINK"
        approval = make_approval(linked, "codex", str(link))
        second = dispatch(linked, recommend(linked, self.config), approval, self.config)
        self.assertEqual(receipt["argv"][receipt["argv"].index("-C") + 1], canon(self.workspace))
        self.assertEqual(second["argv"][second["argv"].index("-C") + 1], canon(link))
        self.assertEqual(canon(link), canon(self.workspace))

    def test_redaction_and_author_review_separation(self):
        secret_mail = "person" + "@" + "example.com"
        secret_token = "sk-" + "abcdEFGH1234"
        secret_path = "/" + "Users/someone/notes"
        self.mode.write_text(
            json.dumps({"action": "print", "stdout": secret_mail + " " + secret_token + " " + secret_path, "code": 0}),
            encoding="utf-8",
        )
        _, receipt = self.dispatch_alias("codex")
        self.assertNotIn(secret_mail, receipt["stdout"])
        self.assertNotIn(secret_token, receipt["stdout"])
        self.assertNotIn(secret_path, receipt["stdout"])
        self.assertIn("[REDACTED]", receipt["stdout"])
        self.assertTrue(receipt["independent_review"]["separated_from_author"])
        self.assertFalse(receipt["independent_review"]["proves_distinct_model_or_account"])
        self.assertFalse(receipt["empty_workspace_is_sandbox"])
        self.assertEqual(receipt["versions"]["source"], "v2")
        self.assertFalse(receipt["live_integration_claimed"])
        self.assertTrue(receipt["simulated_fixture"])

    def test_paused_disabled_and_unbound_entries_do_not_spawn(self):
        for alias, reason in (
            ("openclaw", "paused"),
            ("claude", "disabled_not_probed"),
            ("dsh", "unsupported_config_needed"),
            ("cursor", "external_to_local_runner"),
        ):
            task = make_task(alias)
            recommendation = recommend(task, self.config)
            self.assertFalse(recommendation["recommended"])
            self.assertEqual(recommendation["reason"], reason)
            self.assertIsNone(recommendation["generation_binding"])
            if alias == "openclaw":
                self.assertFalse(recommendation["globally_unavailable"])
            forged = dict(recommendation)
            forged["recommended"] = True
            forged["dispatch_approved"] = False
            with self.assertRaises(BindingError):
                dispatch(task, forged, make_approval(task, alias, str(self.workspace)), self.config)
        self.assertEqual(self.calls(), [])

    def test_subprocess_shell_is_disabled(self):
        calls = []
        real = subprocess.Popen

        def wrapped(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        original = subprocess.Popen
        subprocess.Popen = wrapped
        try:
            self.dispatch_alias("codex")
        finally:
            subprocess.Popen = original
        self.assertFalse(calls[0][1].get("shell", False))
        self.assertIsInstance(calls[0][0][0], list)

    def test_artifact_write_must_stay_inside_scope(self):
        outside = self.root / "outside.txt"
        with self.assertRaises(BindingError) as caught:
            self.dispatch_alias("codex", approval_extra={"artifact_root": str(self.workspace), "final_message_path": str(outside)})
        self.assertEqual(caught.exception.code, "artifact_outside_scope")
        inside = self.workspace / "final.txt"
        self.dispatch_alias("codex", approval_extra={"artifact_root": str(self.workspace), "final_message_path": str(inside)})
        self.assertIn("-o", self.calls()[0]["argv"])
        self.assertEqual(self.calls()[0]["argv"][-1], "-")

    def test_env_allowlist_merges_declared_keys_only(self):
        previous_parent = os.environ.get("FIXTURE_PARENT_ONLY")
        previous_update = os.environ.get("DISABLE_AUTO_UPDATE")
        os.environ["FIXTURE_PARENT_ONLY"] = "parent-only-value"
        os.environ["DISABLE_AUTO_UPDATE"] = "parent-value"
        try:
            config = config_for(str(self.binary), str(self.home_b))
            config["resources"]["kimi"]["env"] = {
                "DISABLE_AUTO_UPDATE": "from-entry",
                "MARKED_SECRET": {"value": "fixture-marked-value", "secret": True},
            }
            _recommendation, receipt = self.dispatch_alias("kimi", config=config)
        finally:
            if previous_parent is None:
                os.environ.pop("FIXTURE_PARENT_ONLY", None)
            else:
                os.environ["FIXTURE_PARENT_ONLY"] = previous_parent
            if previous_update is None:
                os.environ.pop("DISABLE_AUTO_UPDATE", None)
            else:
                os.environ["DISABLE_AUTO_UPDATE"] = previous_update
        seen = self.calls()[0]["seen"]
        self.assertEqual(seen["DISABLE_AUTO_UPDATE"], "from-entry")
        self.assertEqual(seen["MARKED_SECRET"], "fixture-marked-value")
        self.assertIsNone(seen["FIXTURE_PARENT_ONLY"])
        self.assertEqual(receipt["injected_env_keys"], ["DISABLE_AUTO_UPDATE", "MARKED_SECRET"])
        encoded = json.dumps(receipt)
        self.assertNotIn("from-entry", encoded)
        self.assertNotIn("fixture-marked-value", encoded)
        self.assertNotIn("parent-only-value", encoded)

    def test_unmarked_secret_env_key_is_rejected(self):
        config = config_for(str(self.binary), str(self.home_b))
        config["resources"]["codex"]["env"] = {"API_TOKEN": "not-injected"}
        task = make_task("codex")
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommend(task, config), make_approval(task, "codex", str(self.workspace)), config)
        self.assertEqual(caught.exception.code, "env_secret_unmarked")
        self.assertEqual(self.calls(), [])

    def test_empty_skills_dir_is_accepted_and_file_is_rejected(self):
        instance = self.root / "instance"
        skills = instance / "skills"
        skills.mkdir(parents=True)
        config = config_for(str(self.binary), str(self.home_b))
        config["instance_root"] = str(instance)
        config["resources"]["kimi"]["skills_dir"] = str(skills)
        _recommendation, receipt = self.dispatch_alias("kimi", config=config)
        self.assertEqual(receipt["skills_dir_empty"], True)
        self.assertFalse(receipt["skills_dir_is_sandbox"])
        self.assertIn(canon(skills), self.calls()[0]["argv"])
        accepted_calls = len(self.calls())
        outside = self.root / "outside-skills"
        outside.mkdir()
        config["resources"]["kimi"]["skills_dir"] = str(outside)
        task = make_task("kimi")
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommend(task, config), make_approval(task, "kimi", str(self.workspace), authorize_model_alias=True, model_alias="fixture-alias"), config)
        self.assertEqual(caught.exception.code, "skills_dir_outside_root")
        skill_file = instance / "not-a-directory"
        skill_file.write_text("x", encoding="utf-8")
        config["resources"]["kimi"]["skills_dir"] = str(skill_file)
        with self.assertRaises(BindingError) as caught:
            dispatch(task, recommend(task, config), make_approval(task, "kimi", str(self.workspace), authorize_model_alias=True, model_alias="fixture-alias"), config)
        self.assertEqual(caught.exception.code, "skills_dir_invalid")
        self.assertEqual(len(self.calls()), accepted_calls)

    def test_skip_git_repo_check_is_opt_in_for_codex_only(self):
        _recommendation, receipt = self.dispatch_alias("codex")
        self.assertNotIn("--skip-git-repo-check", receipt["argv"])
        self.assertFalse(receipt["skip_git_repo_check"])
        config = config_for(str(self.binary), str(self.home_b))
        config["resources"]["codex"]["skip_git_repo_check"] = True
        config["resources"]["codex2"]["skip_git_repo_check"] = True
        task = make_task("codex")
        task["task_id"] = "SYN-SKIP-GIT"
        opted = dispatch(task, recommend(task, config), make_approval(task, "codex", str(self.workspace)), config)
        self.assertEqual(opted["argv"][-2], "--skip-git-repo-check")
        self.assertEqual(opted["argv"][-1], "-")
        self.assertTrue(opted["skip_git_repo_check"])
        config["resources"]["kimi"]["skip_git_repo_check"] = True
        kimi = make_task("kimi")
        before = len(self.calls())
        with self.assertRaises(BindingError) as caught:
            dispatch(
                kimi,
                recommend(kimi, config),
                make_approval(kimi, "kimi", str(self.workspace), authorize_model_alias=True, model_alias="fixture-alias"),
                config,
            )
        self.assertEqual(caught.exception.code, "skip_git_repo_check_unsupported")
        self.assertEqual(len(self.calls()), before)

    def test_smoke_and_source_hash(self):
        result = run_offline_smoke()
        self.assertEqual(result["aliases"], ["codex", "codex2", "kimi", "hermes"])
        self.assertFalse(result["live_integration_claimed"])
        recorded = (ROOT / "SOURCE_HASH.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(result["source_hash"], source_hash())
        self.assertEqual(recorded, source_hash())
        self.assertEqual(__import__("execution_adapter").__version__, ADAPTER_VERSION)
        self.assertIn('version = "0.1.4"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
