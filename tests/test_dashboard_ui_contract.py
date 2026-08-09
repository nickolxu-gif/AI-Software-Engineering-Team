import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "apps" / "dashboard" / "index.html"
CSS = ROOT / "apps" / "dashboard" / "styles.css"
JS = ROOT / "apps" / "dashboard" / "app.js"


class DashboardUiContractTests(unittest.TestCase):
    def test_five_views_and_readonly_status_exist(self):
        html = HTML.read_text(encoding="utf-8")
        for label in ("总览", "任务", "Agents", "审批", "证据", "只读"):
            self.assertIn(label, html)
        for landmark in ("<nav", "<main", "aria-live", "aria-current"):
            self.assertIn(landmark, html)

    def test_javascript_uses_get_only(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertNotRegex(javascript, r"\b(POST|PUT|PATCH|DELETE)\b")
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

    def test_ui_has_no_action_forms_or_inline_handlers(self):
        html = HTML.read_text(encoding="utf-8")
        javascript = JS.read_text(encoding="utf-8")
        self.assertNotIn("<form", html.lower())
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertIn("请回到 Codex 处理", javascript)
        self.assertIn("escapeHtml", javascript)


if __name__ == "__main__":
    unittest.main()
