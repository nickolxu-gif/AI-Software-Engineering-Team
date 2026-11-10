"""Offline smoke checks. They execute fake binaries only."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from execution_adapter.bindings import recommend, sha256_text
from execution_adapter.package_hash import source_hash
from execution_adapter.runner import dispatch


def _fake(path: Path, side: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "from pathlib import Path",
                f"side = Path({str(side)!r})",
                "prev = json.loads(side.read_text()) if side.exists() else []",
                "prev.append({'argv': sys.argv, 'stdin': sys.stdin.read(), 'home': os.environ.get('CODEX_HOME')})",
                "side.write_text(json.dumps(prev))",
                "sys.stdout.write('model: configured-alias\\n')",
                "sys.stdout.write('{\"type\":\"event\"}\\n')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _config(binary: str, home: str) -> dict:
    return {
        "execution_label": "fixture",
        "binary_allowlist": [binary],
        "resources": {
            "codex": {"enabled": True, "kind": "codex_exec", "binary": binary, "codex_home": None, "model_alias": None, "account_ref": None},
            "codex2": {"enabled": True, "kind": "codex_exec", "binary": binary, "codex_home": home, "model_alias": None, "account_ref": None},
            "kimi": {"enabled": True, "kind": "kimi_cli", "binary": binary, "skills_dir": None, "agent_file": None, "account_ref": None},
            "hermes": {"enabled": True, "kind": "hermes_chat", "binary": binary, "account_ref": None},
            "openclaw": {"enabled": False, "kind": "paused", "binary": None},
            "dsh": {"enabled": False, "kind": "unsupported_config_needed", "binary": None},
            "cursor": {"enabled": False, "kind": "external_orchestrator", "binary": None},
            "claude": {"enabled": False, "kind": "disabled", "binary": None},
        },
    }


def _task(alias: str) -> dict:
    prompt = "Synthetic smoke prompt."
    return {
        "task_id": "SMOKE-1",
        "prompt": prompt,
        "data_scope": "public_or_synthetic",
        "private_data": False,
        "preferred_resource": alias,
        "author_role": "reading-arrangement",
        "review_role": "knowledge-content-acceptance",
        "review_session": "separate-smoke-session",
        "versions": {"task": "v1", "source": "v1", "plan": "v1"},
    }


def _approval(task: dict, alias: str, workspace: str) -> dict:
    body = {
        "task_id": task["task_id"],
        "resource_alias": alias,
        "approved": True,
        "authority_ref": "smoke-authority",
        "covers_private_data": False,
        "prompt_sha256": sha256_text(task["prompt"]),
        "workspace": workspace,
    }
    if alias == "hermes":
        body["max_turns"] = 1
    if alias == "kimi":
        body["authorize_model_alias"] = True
        body["model_alias"] = "fixture-alias"
    return body


def run_offline_smoke() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        binary = root / "fake-client"
        side = root / "calls.json"
        home = root / "config-home"
        home.mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        _fake(binary, side)
        config = _config(str(binary), str(home))
        aliases = []
        for alias in ("codex", "codex2", "kimi", "hermes"):
            task = _task(alias)
            recommendation = recommend(task, config)
            if recommendation.get("dispatch_approved") is not False or not recommendation.get("recommended"):
                raise RuntimeError(f"smoke recommendation failed for {alias}")
            receipt = dispatch(task, recommendation, _approval(task, alias, str(workspace)), config)
            if receipt["live_integration_claimed"] or receipt["observed_model"] != "UNKNOWN":
                raise RuntimeError(f"smoke receipt overclaimed for {alias}")
            if receipt["request_count"] != 1 or receipt["retried"] or receipt["fallback_used"]:
                raise RuntimeError(f"smoke retry state failed for {alias}")
            aliases.append(alias)
        calls = json.loads(side.read_text(encoding="utf-8"))
        if len(calls) != 4:
            raise RuntimeError("smoke invoked an unexpected number of processes")
        paused = recommend(_task("openclaw"), config)
        if paused.get("reason") != "paused" or paused.get("globally_unavailable") is not False:
            raise RuntimeError("paused entry was not preserved")
    return {"aliases": aliases, "source_hash": source_hash(), "live_integration_claimed": False}


def main() -> int:
    result = run_offline_smoke()
    print("offline_smoke=PASS")
    print("live_integration_claimed=false")
    print("source_hash=" + result["source_hash"])
    print("adapters=" + ",".join(result["aliases"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
