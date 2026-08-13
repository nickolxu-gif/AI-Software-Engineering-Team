import os
import json
import hashlib
import shlex
import stat
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "codebuddy-verify.sh"
GLOBAL_SKILL_ROOT = Path("/Users/qinxu/.codex/skills/claude-emergency-verifier")


class CodeBuddyAdapterTests(unittest.TestCase):
    def clean_global_wrapper(self, temporary):
        global_root = Path(temporary) / "global-core"
        scripts = global_root / "scripts"
        scripts.mkdir(parents=True)
        for relative in (
            "scripts/review_packet.py", "scripts/codebuddy_stream_runner.py",
            "scripts/normalize_review_result.py", "VERSION",
        ):
            content = subprocess.check_output(
                ["git", "-C", str(GLOBAL_SKILL_ROOT), "show", "HEAD:" + relative],
            )
            target = global_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (scripts / "codebuddy_stream_runner.py").chmod(0o755)
        for command in (
            ["git", "init", "-q", str(global_root)],
            ["git", "-C", str(global_root), "config", "user.email", "test@example.invalid"],
            ["git", "-C", str(global_root), "config", "user.name", "Test"],
            ["git", "-C", str(global_root), "add", "scripts", "VERSION"],
            ["git", "-C", str(global_root), "commit", "-qm", "global core"],
        ):
            subprocess.run(command, check=True)
        packet_hash = hashlib.sha256((scripts / "review_packet.py").read_bytes()).hexdigest()
        runner_hash = hashlib.sha256((scripts / "codebuddy_stream_runner.py").read_bytes()).hexdigest()
        test_wrapper = Path(temporary) / "wrapper.sh"
        wrapper_text = WRAPPER.read_text(encoding="utf-8").replace(
            "/Users/qinxu/.codex/skills/claude-emergency-verifier", str(global_root),
        ).replace(
            "7a970e656df08ea67b87e0c6b501d2258ee592759e0415995f42dbf2a4dcdcab", packet_hash,
        ).replace(
            "3cfd6f2f9eee50a6e392ac56bd68bb3adc0bd9789816575d09a2aa252fe7934b", runner_hash,
        ).replace(
            "8663b02839e591260ba30cd1e00612eb718a14db3fd2c004fcaf0177711b509e",
            hashlib.sha256((scripts / "normalize_review_result.py").read_bytes()).hexdigest(),
        )
        test_wrapper.write_text(wrapper_text, encoding="utf-8")
        test_wrapper.chmod(0o755)
        return test_wrapper, global_root

    def test_project_adapter_is_executable_and_pins_global_v410_core(self):
        self.assertTrue(WRAPPER.is_file())
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        text = WRAPPER.read_text(encoding="utf-8")
        required = (
            "global_skill_root=/Users/qinxu/.codex/skills/claude-emergency-verifier",
            "show HEAD:scripts/review_packet.py",
            "show HEAD:scripts/codebuddy_stream_runner.py",
            "show HEAD:scripts/normalize_review_result.py",
            "review_packet=$isolated_core/review_packet.py",
            "7a970e656df08ea67b87e0c6b501d2258ee592759e0415995f42dbf2a4dcdcab",
            "3cfd6f2f9eee50a6e392ac56bd68bb3adc0bd9789816575d09a2aa252fe7934b",
            "8663b02839e591260ba30cd1e00612eb718a14db3fd2c004fcaf0177711b509e",
            "--tools ''",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--max-turns 1",
            "--output-format stream-json",
            "V4.10.6",
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
        with tempfile.TemporaryDirectory() as temporary:
            wrapper, _ = self.clean_global_wrapper(temporary)
            result = subprocess.run(
                [str(wrapper), "--prompt", "bounded adapter contract probe",
                 "--report", "reports/codebuddy-adapter-test.md",
                 "--file", "tests/does-not-exist.py"],
                cwd=ROOT, text=True, capture_output=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source must be an existing regular file", result.stderr)

    def test_adapter_rejects_report_outside_reports_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            wrapper, _ = self.clean_global_wrapper(temporary)
            result = subprocess.run(
                [str(wrapper), "--base-ref", "55c195e", "--head-ref", "HEAD",
                 "--prompt", "bounded adapter report path probe", "--report", ".git/config",
                 "--file", "scripts/codebuddy-verify.sh"],
                cwd=ROOT, text=True, capture_output=True,
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
            test_wrapper, _ = self.clean_global_wrapper(temporary)
            wrapper_text = test_wrapper.read_text(encoding="utf-8").replace(
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
        self.assertIn("isolated_core=$work_dir/global-core", text)
        self.assertIn("ln \"$report_tmp\" \"$report_path\"", text)
        self.assertIn("--provider codebuddy", text)
        self.assertIn("--model glm-5.2", text)

    def test_adapter_creates_a_new_reports_directory_in_a_clean_project(self):
        verdict = {
            "verdict": "PASS_WITH_WARNINGS",
            "findings": [],
            "scope_ack": ["scope.py"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            project = temporary / "project"
            project.mkdir()
            (project / "scope.py").write_text("value = 1\n", encoding="utf-8")
            for command in (
                ["git", "init", "-q", str(project)],
                ["git", "-C", str(project), "config", "user.email", "test@example.invalid"],
                ["git", "-C", str(project), "config", "user.name", "Test"],
                ["git", "-C", str(project), "add", "scope.py"],
                ["git", "-C", str(project), "commit", "-qm", "baseline"],
            ):
                subprocess.run(command, check=True)
            (project / "scope.py").write_text("value = 2\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(project), "commit", "-am", "candidate", "-q"],
                check=True,
            )
            fake = temporary / "codebuddy"
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
            wrapper, _ = self.clean_global_wrapper(temporary)
            wrapper.write_text(
                wrapper.read_text(encoding="utf-8").replace(
                    "codebuddy_executable=/Users/qinxu/.local/bin/codebuddy",
                    "codebuddy_executable=" + str(fake),
                ),
                encoding="utf-8",
            )
            report = project / "reports" / "nonpass.md"
            result = subprocess.run(
                [str(wrapper), "--base-ref", "HEAD~1", "--head-ref", "HEAD",
                 "--prompt", "bounded clean project probe", "--report", "reports/nonpass.md",
                 "--file", "scope.py"],
                cwd=project, text=True, capture_output=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Verdict: PASS_WITH_WARNINGS", report.read_text(encoding="utf-8"))

    def test_adapter_uses_committed_global_core_when_worktree_is_dirty(self):
        with tempfile.TemporaryDirectory() as temporary:
            test_wrapper, global_root = self.clean_global_wrapper(temporary)
            runner = global_root / "scripts" / "codebuddy_stream_runner.py"
            runner.write_text("#!/bin/sh\n# dirty\nexit 0\n", encoding="utf-8")
            fake = Path(temporary) / "codebuddy"
            verdict = {
                "verdict": "PASS_WITH_WARNINGS",
                "findings": [],
                "scope_ack": ["scripts/codebuddy-verify.sh"],
            }
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
            test_wrapper.write_text(
                test_wrapper.read_text(encoding="utf-8").replace(
                    "codebuddy_executable=/Users/qinxu/.local/bin/codebuddy",
                    "codebuddy_executable=" + str(fake),
                ),
                encoding="utf-8",
            )
            report = ROOT / "reports" / ".codebuddy-adapter-dirty-core-test.md"
            try:
                result = subprocess.run(
                    [
                        str(test_wrapper), "--base-ref", "55c195e", "--head-ref", "HEAD",
                        "--prompt", "bounded dirty core probe " + uuid.uuid4().hex, "--report",
                        "reports/.codebuddy-adapter-dirty-core-test.md", "--file",
                        "scripts/codebuddy-verify.sh",
                    ],
                    cwd=ROOT, text=True, capture_output=True,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Verdict: PASS_WITH_WARNINGS", report.read_text(encoding="utf-8"))
            finally:
                report.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
