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
            "本地只读工作台",
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

    def test_strict_read_only_never_initializes_missing_control_state(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        guide = GUIDE_PATH.read_text(encoding="utf-8")
        for required in (
            "If the user explicitly requires strictly read-only",
            "do not initialize",
            "read-only Git and file inventory",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)
        self.assertIn("严格只读时不得初始化", guide)
        self.assertIn("状态库不可用", guide)

    def test_docs_distinguish_stable_cli_from_internal_or_unavailable_flows(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        guide = GUIDE_PATH.read_text(encoding="utf-8")
        for required in (
            "known dispatch ID",
            "no stable CLI",
            "OperationCoordinator",
            "Never edit SQLite directly",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)
        for required in (
            "稳定 CLI 只有",
            "已知 `dispatch_id`",
            "当前不提供全局任务列表",
            "Agent/Blocker/Review/Evidence 登记",
            "approval create/consume",
            "通用 `PREPARED` reconcile",
            "Mimo 入口",
            "OperationCoordinator",
            "不得直接修改 SQLite",
        ):
            with self.subTest(required=required):
                self.assertIn(required, guide)
        self.assertNotIn("暂停所有未完成写入任务", guide)

    def test_docs_state_current_secret_handling_limit(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        guide = GUIDE_PATH.read_text(encoding="utf-8")
        self.assertIn("No automatic secret scanning or redaction exists", skill)
        self.assertIn("当前没有自动秘密检测或自动脱敏", guide)
        self.assertIn("不要把含 Token 的日志交给系统", guide)
        self.assertIn("持久化前", guide)
        self.assertIn("请求用户提供脱敏材料", guide)

    def test_guide_documents_real_status_field_names_and_overlay(self):
        text = GUIDE_PATH.read_text(encoding="utf-8")
        for required in (
            "task.current_head_sha",
            "actual_head_sha",
            "blocker.resolution_condition",
            "review.disposition",
            "review.report_sha256",
            "review.stale",
            "review.effective",
            "evidence.path",
            "evidence.sha256",
            "evidence.source_sha",
            "evidence.stale",
            "effective_state",
            "overlay",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("读取 `handoff.md` 和五份现行工程协议", text)

    def test_skill_maps_open_dashboard_to_readonly_launcher(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "open-team-dashboard",
            "127.0.0.1",
            "never initializes a missing database",
            "browser remains read-only",
            "return to Codex",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)
        self.assertIn("ordinary non-dashboard requests", skill)
        self.assertIn("Dashboard open/view intents", skill)

    def test_skill_uses_one_bounded_foreground_intent_loop_for_writable_requests(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "process-pending-intents --limit 10",
            "once per request",
            "Run only if the control database exists",
            "If init fails or it is unavailable, do not run the queue and stop subsequent writes",
            "a non-zero result means control-plane failure: stop subsequent writes",
            "strictly read-only",
            "Dashboard open/view intents",
            "non-zero",
            "stop subsequent writes",
            "REJECTED",
            "BLOCKED",
            "not a daemon",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_skill_preserves_production_and_future_mvp_human_gates(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("production", text)
        self.assertIn("MVP 2/3", text)

    def test_guide_documents_mvp1_actual_usage_and_limits(self):
        guide = GUIDE_PATH.read_text(encoding="utf-8")
        for required in (
            "打开软件 AI 工程团队工作台",
            "scripts/open-team-dashboard",
            "每 15 秒",
            "45 秒",
            "请回到 Codex 处理",
            "不会配置 GitHub Remote",
        ):
            with self.subTest(required=required):
                self.assertIn(required, guide)
        self.assertNotIn("MVP 1 的本地只读前端工作台尚未实现", guide)

    def test_guide_identifies_dashboard_post_as_existing_mvp2a_capability(self):
        guide = GUIDE_PATH.read_text(encoding="utf-8")
        self.assertIn("MVP 2A 的既有三类受限意图", guide)

    def test_skill_requires_codex_to_acknowledge_handled_task_intakes(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("task intake", text)
        self.assertIn("ACKNOWLEDGED", text)


if __name__ == "__main__":
    unittest.main()
