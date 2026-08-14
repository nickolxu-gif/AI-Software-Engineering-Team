import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "apps" / "dashboard" / "index.html"
CSS = ROOT / "apps" / "dashboard" / "styles.css"
JS = ROOT / "apps" / "dashboard" / "app.js"


class DashboardUiContractTests(unittest.TestCase):
    def test_five_views_and_controlled_request_status_exist(self):
        html = HTML.read_text(encoding="utf-8")
        for label in ("总览", "任务", "Agents", "审批", "证据", "受控意图"):
            self.assertIn(label, html)
        for landmark in ("<nav", "<main", "aria-live", "aria-current"):
            self.assertIn(landmark, html)

    def test_javascript_only_uses_bounded_post_routes(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("method: 'POST'", javascript)
        self.assertNotRegex(javascript, r"\b(PUT|PATCH|DELETE)\b")
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)
        self.assertIn("fetch(url, { method: 'GET'", javascript)

    def test_refresh_and_stale_thresholds_are_explicit(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("const REFRESH_INTERVAL_MS = 15000", javascript)
        self.assertIn("const STALE_AFTER_MS = 45000", javascript)

    def test_css_has_focus_and_narrow_layout(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertRegex(css, r"@media\s*\(max-width:\s*1024px\)")

    def test_ui_has_no_static_action_forms_or_inline_handlers(self):
        html = HTML.read_text(encoding="utf-8")
        javascript = JS.read_text(encoding="utf-8")
        self.assertNotIn("<form", html.lower())
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertIn("请回到 Codex 处理", javascript)
        self.assertIn("escapeHtml", javascript)

    def test_ui_exposes_task_intake_without_execution_controls(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("/api/task-intakes", javascript)
        self.assertIn("提交新工程需求", javascript)
        self.assertIn("等待下一次工程会话处理", javascript)
        for forbidden in (
            "process-pending-intents", "process-intent", "git merge", "git push", "nonce",
        ):
            self.assertNotIn(forbidden, javascript)

    def test_task_detail_uses_events_and_rebinds_fresh_snapshot(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("/events?limit=100&offset=0", javascript)
        self.assertIn("state.selectedDetail = selectedDetail", javascript)
        for field in (
            "task_base_sha",
            "current_head_sha",
            "actual_head_sha",
            "worktree_path",
            "pending_approval_count",
            "evidence_count",
        ):
            self.assertIn(field, javascript)

    def test_all_secondary_responses_keep_the_source_head_envelope(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("assertSourceHead", javascript)
        self.assertRegex(
            javascript,
            r"assertSourceHead\(payload,\s*expectedHead\)",
        )
        self.assertIn("detailPayloads", javascript)
        self.assertIn("eventPayload", javascript)

    def test_secondary_caches_are_bound_to_task_and_head(self):
        javascript = JS.read_text(encoding="utf-8")
        for field in (
            "selectedDetailSourceHead",
            "selectedEventsTaskId",
            "selectedEventsSourceHead",
            "evidenceTaskId",
            "evidenceSourceHead",
        ):
            self.assertIn(field, javascript)
        self.assertIn("reloadSelectedTask", javascript)
        self.assertGreaterEqual(
            javascript.count("expectedHead !== state.sourceHeadSha) return"),
            4,
        )

    def test_timeline_discloses_bounded_results_and_unknown_state(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("has_more", javascript)
        self.assertIn("仅显示最新 100 条", javascript)
        self.assertIn("UNKNOWN: '状态无法确认'", javascript)


if __name__ == "__main__":
    unittest.main()
