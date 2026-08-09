import re
import unittest
from pathlib import Path


SKILL_PATH = Path(".agents/skills/ai-software-engineering-team/SKILL.md")
GUIDE_PATH = Path("USER_OPERATING_GUIDE.md")


class SkillContractTests(unittest.TestCase):
    def test_skill_frontmatter_is_discoverable_and_body_is_concise(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter, body = match.groups()
        self.assertIn("name: ai-software-engineering-team", frontmatter)
        description = next(
            line.removeprefix("description: ")
            for line in frontmatter.splitlines()
            if line.startswith("description: ")
        )
        self.assertTrue(description.startswith("Use when "))
        self.assertLess(len(re.findall(r"\b[\w'-]+\b", body)), 500)

    def test_skill_preserves_codex_control_and_minor_risk_boundary(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "Codex is the only engineering authority",
            "handoff.md",
            "scripts/repo-health.sh",
            "scripts/team-control",
            "scripts/worktree-doctor",
            "Minor",
            "NEEDS_HUMAN_APPROVAL",
            "Never run git reset --hard",
            "PREPARED",
            "seven-question Dispatch Record",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_skill_closes_known_no_cli_baseline_gaps(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "If the control database is absent, initialize it safely",
            "inspect before repair",
            "repairable classification",
            "MVP 1",
            "GitHub",
            "Claude",
            "explicit yes",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_user_guide_contains_required_no_cli_phrases(self):
        text = GUIDE_PATH.read_text(encoding="utf-8")
        for phrase in (
            "进入软件工程团队",
            "查看当前任务状态",
            "哪些事项等我批准",
            "暂停任务",
            "继续任务",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_user_guide_covers_operating_and_governance_contract(self):
        text = GUIDE_PATH.read_text(encoding="utf-8")
        for required in (
            "不需要 VS Code",
            "七问派活",
            "NEEDS_HUMAN_APPROVAL",
            "本次明确回复 `yes`",
            "Minor",
            "Doctor",
            "不会自动删除",
            "GitHub Remote 尚未配置",
            "MVP 1",
            "尚未实现",
            "threat model",
            "CLI 附录",
            "术语表",
            "每日推荐用法",
            "每周推荐用法",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_user_guide_offers_at_least_fifteen_copyable_prompts(self):
        text = GUIDE_PATH.read_text(encoding="utf-8")
        prompts = re.findall(r'^\d+\. “.+”$', text, re.MULTILINE)
        self.assertGreaterEqual(len(prompts), 15)


if __name__ == "__main__":
    unittest.main()
