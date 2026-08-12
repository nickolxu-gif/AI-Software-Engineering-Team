import os
import json
import shlex
import stat
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "codebuddy-verify.sh"


class CodeBuddyAdapterTests(unittest.TestCase):
    def test_project_adapter_is_executable_and_pins_global_v410_core(self):
        self.assertTrue(WRAPPER.is_file())
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        text = WRAPPER.read_text(encoding="utf-8")
        required = (
            "claude-emergency-verifier/scripts/review_packet.py",
            "claude-emergency-verifier/scripts/codebuddy_stream_runner.py",
            "7a970e656df08ea67b87e0c6b501d2258ee592759e0415995f42dbf2a4dcdcab",
            "8bbb51770bebb50ee7da550be5fd5e2cff6d4c5d28912559b8353ef44707f11e",
            "--tools ''",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--max-turns 1",
            "--output-format stream-json",
            "V4.10.3",
            "validate-verdict",
            "receipt",
            "if [ \"$verdict\" != \"PASS\" ]",
            "report path must be under reports",
            "ls-files --error-unmatch",
            "rev-parse --verify",
            "setting-sources ''",
            "codebuddy_executable=/Users/qinxu/.local/bin/codebuddy",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("--dangerously-skip-permissions", text)
        self.assertNotIn("Projects/agent-collaboration-hub/scripts", text)

    def test_adapter_rejects_missing_source_before_provider_start(self):
        result = subprocess.run(
            [
                str(WRAPPER),
                "--prompt", "bounded adapter contract probe",
                "--report", "reports/codebuddy-adapter-test.md",
                "--file", "tests/does-not-exist.py",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source must be an existing regular file", result.stderr)

    def test_adapter_rejects_report_outside_reports_directory(self):
        result = subprocess.run(
            [
                str(WRAPPER),
                "--base-ref", "55c195e",
                "--head-ref", "HEAD",
                "--prompt", "bounded adapter report path probe",
                "--report", ".git/config",
                "--file", "scripts/codebuddy-verify.sh",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("report path must be under reports", result.stderr)

    def test_non_pass_verdict_is_a_blocking_process_result(self):
        verdict = {
            "verdict": "PASS_WITH_WARNINGS",
            "findings": [],
            "scope_ack": ["scripts/codebuddy-verify.sh"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "codebuddy"
            init_line = json.dumps({
                "type": "system", "subtype": "init", "model": "glm-5.2", "tools": [],
            }, separators=(",", ":"))
            result_line = json.dumps({
                "type": "result", "subtype": "success", "result": json.dumps(verdict),
            }, separators=(",", ":"))
            fake.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' " + shlex.quote(init_line) + "\n"
                "printf '%s\\n' " + shlex.quote(result_line) + "\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            test_wrapper = Path(temporary) / "wrapper.sh"
            wrapper_text = WRAPPER.read_text(encoding="utf-8").replace(
                "codebuddy_executable=/Users/qinxu/.local/bin/codebuddy",
                "codebuddy_executable=" + str(fake),
            )
            test_wrapper.write_text(wrapper_text, encoding="utf-8")
            test_wrapper.chmod(0o755)
            report = ROOT / "reports" / ".codebuddy-adapter-nonpass-test.md"
            if report.exists():
                report.unlink()
            try:
                result = subprocess.run(
                    [
                        str(test_wrapper),
                        "--base-ref", "55c195e",
                        "--head-ref", "HEAD",
                        "--prompt", "bounded adapter non pass probe " + uuid.uuid4().hex,
                        "--report", "reports/.codebuddy-adapter-nonpass-test.md",
                        "--file", "scripts/codebuddy-verify.sh",
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Verdict: PASS_WITH_WARNINGS", report.read_text(encoding="utf-8"))
            finally:
                report.unlink(missing_ok=True)

    def test_adapter_binds_local_report_and_packet_evidence(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("report_path=$project_root/$report", text)
        self.assertIn("evidence_dir=$project_root/.review-evidence", text)
        self.assertIn("--provider codebuddy", text)
        self.assertIn("--model glm-5.2", text)


if __name__ == "__main__":
    unittest.main()
