import os
import stat
import subprocess
import unittest
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
            "validate-verdict",
            "receipt",
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

    def test_adapter_binds_local_report_and_packet_evidence(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("report_path=$project_root/$report", text)
        self.assertIn("evidence_dir=$project_root/.review-evidence", text)
        self.assertIn("--provider codebuddy", text)
        self.assertIn("--model glm-5.2", text)


if __name__ == "__main__":
    unittest.main()
