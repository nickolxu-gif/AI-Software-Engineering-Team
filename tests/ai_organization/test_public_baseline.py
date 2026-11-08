"""Executable checks for the 2026-11-08 public organization baseline."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_syn_read import (
    REQUIRED_FAULT_CODES,
    Report,
    check_acceptance,
    check_claims,
    check_fault,
    evaluate,
    parse_source,
)
from json_subset import validate

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "ai-organization"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "syn-read-01"
SCHEMA_VERSION = "2026-11-08"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load(DOCS / "role-registry.json")
        cls.schema = load(DOCS / "schemas" / "role-registry.schema.json")

    def test_schema_version_and_shape(self):
        errors = validate(self.registry, self.schema)
        self.assertEqual(errors, [])
        self.assertEqual(self.registry["schema_version"], SCHEMA_VERSION)
        self.assertTrue(self.registry["target_organization"])
        self.assertFalse(self.registry["deployment_claimed"])
        self.assertTrue(self.registry["state_axes_are_independent"])

    def test_axes_are_not_collapsed_or_overclaimed(self):
        self.assertEqual(len(self.registry["roles"]), 41)
        first = []
        for role in self.registry["roles"]:
            self.assertNotIn("status", role)
            self.assertEqual(role["configuration_status"], "not_configured")
            self.assertIsNone(role["configuration_evidence"])
            self.assertEqual(role["runtime_status"], "not_run")
            self.assertIsNone(role["runtime_evidence"])
            self.assertEqual(role["acceptance_status"], "not_accepted")
            self.assertIsNone(role["acceptance_evidence"])
            self.assertFalse(role["created_in_this_repository"])
            if role["first_batch_template"]:
                first.append(role["role_id"])
                self.assertEqual(role["selection_status"], "selected")
        self.assertEqual(
            first,
            [
                "task-planning",
                "plan-review",
                "reading-arrangement",
                "knowledge-analysis",
                "knowledge-content-acceptance",
            ],
        )

    def test_selection_axis_covers_baseline_classes(self):
        counts = {}
        for role in self.registry["roles"]:
            counts[role["selection_status"]] = counts.get(role["selection_status"], 0) + 1
        self.assertGreater(counts["selected"], 0)
        self.assertEqual(counts["tendency"], 2)
        self.assertGreaterEqual(counts["candidate"], 4)
        maintenance = next(role for role in self.registry["roles"] if role["role_id"] == "knowledge-base-maintenance")
        self.assertEqual(maintenance["selection_status"], "candidate")
        self.assertEqual(maintenance["responsibility_rules"], "selected")
        self.assertEqual(maintenance["intended_form"], "undecided")
        self.assertTrue(any(item["selection_status"] == "undecided" for item in self.registry["undecided_items"]))

    def test_configured_without_evidence_is_rejected(self):
        mutated = copy.deepcopy(self.registry)
        mutated["roles"][0]["configuration_status"] = "configured"
        errors = validate(mutated, self.schema)
        self.assertEqual(errors, [])
        self.assertIsNone(mutated["roles"][0]["configuration_evidence"])
        self.assert_axis_rule_fails(mutated["roles"][0])

    def test_extra_status_field_is_rejected_by_schema(self):
        mutated = copy.deepcopy(self.registry)
        mutated["roles"][0]["status"] = "selected"
        errors = validate(mutated, self.schema)
        self.assertTrue(any("status" in item for item in errors))

    def test_historical_readback_does_not_configure_current_roles(self):
        history = self.registry["historical_v14"]
        self.assertEqual(history["static_readback_code"], "role_contracts_applied_and_readback_verified")
        self.assertEqual(history["existing_bot_description_contracts_updated"], 5)
        self.assertEqual(history["knowledge_life_posts_created"], 1)
        self.assertEqual(history["profile_readbacks_succeeded"], 6)
        self.assertEqual(history["static_configuration_checks_passed"], 13)
        self.assertEqual(history["functional_status"], "unverified")
        self.assertFalse(history["applies_to_current_registry"])
        self.assertFalse(history["target_organization"])
        self.assertFalse(history["bot_instruction_is_security_sandbox"])

    def assert_axis_rule_fails(self, role):
        self.assertEqual(role["configuration_status"], "configured")
        self.assertIsNone(role["configuration_evidence"])
        failed = role["configuration_status"] == "configured" and not role["configuration_evidence"]
        self.assertTrue(failed)


class TemplateAndRecordTests(unittest.TestCase):
    def test_first_batch_templates_are_not_deployment(self):
        document = load(DOCS / "first-batch-role-templates.json")
        self.assertEqual(document["schema_version"], SCHEMA_VERSION)
        self.assertFalse(document["deployment_claimed"])
        self.assertFalse(document["created_in_this_repository"])
        ids = [item["role_id"] for item in document["templates"]]
        self.assertEqual(
            ids,
            [
                "task-planning",
                "plan-review",
                "reading-arrangement",
                "knowledge-analysis",
                "knowledge-content-acceptance",
            ],
        )
        for item in document["templates"]:
            self.assertFalse(item["deployed"])
            self.assertFalse(item["configured"])
            self.assertNotIn("已部署", item["description_text"])
            self.assertNotIn("已配置", item["description_text"])

    def test_record_schemas_accept_templates_and_fixture(self):
        schemas = load(DOCS / "schemas" / "record-schemas.json")["schemas"]
        pairs = [
            (DOCS / "templates" / "task-record.template.json", "task_record"),
            (DOCS / "templates" / "handoff.template.json", "handoff"),
            (DOCS / "templates" / "acceptance-record.template.json", "acceptance_record"),
            (DOCS / "templates" / "version-change.template.json", "version_change"),
            (FIXTURES / "task.json", "task_record"),
            (FIXTURES / "v1" / "handoff.json", "handoff"),
            (FIXTURES / "v2" / "handoff.json", "handoff"),
            (FIXTURES / "v1" / "acceptance.json", "acceptance_record"),
            (FIXTURES / "v2" / "acceptance.json", "acceptance_record"),
            (FIXTURES / "v2" / "version-change.json", "version_change"),
        ]
        for path, name in pairs:
            errors = validate(load(path), schemas[name])
            self.assertEqual(errors, [], f"{path}: {errors}")
        self.assertEqual(schemas["task_record"]["properties"]["schema_version"]["const"], SCHEMA_VERSION)


class SyntheticReadTests(unittest.TestCase):
    def test_valid_bundle_passes_and_is_not_acceptance(self):
        result = evaluate(FIXTURES)
        self.assertEqual(result["v1"], [])
        self.assertEqual(result["v2"], [])
        self.assertEqual(result["task"], [])
        self.assertTrue(result["file_check_pass"])
        self.assertEqual(result["professional_acceptance"], "not_performed")
        self.assertTrue(result["script_pass_is_not_professional_acceptance"])
        self.assertTrue(REQUIRED_FAULT_CODES.issubset(set(result["fault_codes"])))

    def test_script_stdout_separates_file_check_from_acceptance(self):
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "check_syn_read.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("file_check=PASS", completed.stdout)
        self.assertIn("professional_acceptance=not_performed", completed.stdout)
        self.assertIn("script_pass_is_not_professional_acceptance=true", completed.stdout)

    def test_missing_value_marked_zero_is_a_defect(self):
        source = (FIXTURES / "v1" / "source.md").read_text(encoding="utf-8")
        parsed = parse_source(source, "v1")
        record = load(FIXTURES / "v1" / "arrangement.json")
        record["claims"][0]["energy"] = 0
        report = Report()
        check_claims(record, parsed, source, report)
        self.assertIn("MISSING_AS_ZERO", report.codes)

    def test_direct_comparison_is_a_defect(self):
        source = (FIXTURES / "v1" / "source.md").read_text(encoding="utf-8")
        parsed = parse_source(source, "v1")
        record = load(FIXTURES / "v1" / "arrangement.json")
        record["direct_comparison"] = "记录 A 优于记录 B"
        report = Report()
        check_claims(record, parsed, source, report)
        self.assertIn("DIRECT_COMPARISON", report.codes)

    def test_author_acceptance_is_a_defect(self):
        record = load(FIXTURES / "v1" / "acceptance.json")
        record["professional_acceptance"]["status"] = "accepted"
        record["professional_acceptance"]["performed_by_author"] = True
        report = Report()
        check_acceptance(record, "v1", 0, report)
        self.assertIn("AUTHOR_ACCEPTANCE_COLLAPSED", report.codes)

    def test_unlabeled_fault_copy_is_detected(self):
        record = load(FIXTURES / "fault" / "error-copy.json")
        record["fault_label"] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "error-copy.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            codes = {item["code"] for item in check_fault(path, (FIXTURES / "v1" / "source.md").read_text(encoding="utf-8"))}
        self.assertIn("FAULT_LABEL_MISSING", codes)

    def test_v2_keeps_old_number_traceable(self):
        source = (FIXTURES / "v2" / "source.md").read_text(encoding="utf-8")
        self.assertIn("由 v1 的 12 秒更正为 14 秒", source)
        self.assertIn("v1 保留", source)
        self.assertNotIn("平均用时 12 秒。", source)
        change = load(FIXTURES / "v2" / "version-change.json")
        self.assertEqual(change["change"], {"field": "A.mean_seconds", "from": 12, "to": 14})
        self.assertTrue(change["retains_v1"])
        self.assertEqual(change["recheck_count"], 1)
        statuses = {item["status"] for item in change["impact"]}
        self.assertTrue(statuses.isdisjoint({"verified", "accepted"}))


class HistoryAndSensitiveTests(unittest.TestCase):
    def test_v14_example_still_matches_its_schema(self):
        history = DOCS / "history" / "v1.4"
        example = load(history / "task-evidence.example.json")
        schema = load(history / "task-evidence.schema.json")
        self.assertEqual(validate(example, schema), [])
        self.assertEqual(example["document_status"], "superseded_historical")
        self.assertFalse(example["target_organization"])
        roles = load(history / "roles.template.json")
        self.assertEqual(len(roles["duties"]), 14)
        self.assertFalse(roles["target_organization"])
        readme = (history / "README.md").read_text(encoding="utf-8")
        self.assertIn("不再是目标组织", readme)

    def test_sensitive_markers_are_absent(self):
        patterns = [
            ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
            ("url", re.compile(r"https?://", re.I)),
            ("home_path", re.compile(r"(?:/Users/|/home/|/private/|/workspace|uploads/)")),
            ("key_material", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY|ssh-ed25519|ssh-rsa")),
            ("internal_name", re.compile(r"Hermes|Codex2|CodeBuddy|nickolxu|qinxu")),
        ]
        files = []
        for path in DOCS.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".json"}:
                files.append(path)
        fixture_root = Path(__file__).resolve().parent / "fixtures"
        for path in fixture_root.rglob("*"):
            if path.is_file():
                files.append(path)
        parent = Path(__file__).resolve().parent
        files.extend([parent / "check_syn_read.py", parent / "json_subset.py"])
        hits = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for label, pattern in patterns:
                for match in pattern.finditer(text):
                    hits.append(f"{path.relative_to(ROOT)}:{label}:{match.group(0)}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
