# MVP 2B Codex Intent Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Codex 能以受限、可审计批次处理已提交的安全意图，并在工作台显示待处理数量。

**Architecture:** Store 提供稳定的 PENDING snapshot；IntentService 逐条复用现有处理器；CLI 只输出白名单汇总；Dashboard 只增加一个只读计数。浏览器无新增执行权。

**Tech Stack:** Python 标准库、SQLite、unittest、静态 HTML/JavaScript。

---

### Task 1: PENDING snapshot 与批处理服务

**Files:**

- Modify: `team_control/store.py`
- Modify: `team_control/intents.py`
- Test: `tests/test_intents.py`
- Test: `tests/test_store.py`

- [ ] 写失败测试：两个按时间提交的意图在 `process_pending(2)` 中按稳定顺序处理，首条 stale HEAD 为 `REJECTED` 仍继续处理第二条。
- [ ] 运行 `python3 -m unittest tests/test_intents.py tests/test_store.py -q`，确认因为缺少 `process_pending` 失败。
- [ ] 实现 `list_pending_intents(limit)`：拒绝 bool、非整数和不在 `1..25` 的 limit；只读查询 `status='PENDING' ORDER BY created_at, intent_id LIMIT ?`。
- [ ] 实现 `process_pending(limit)`：取得一次 snapshot，逐条调用现有 `process`，返回 `{attempted, results}`；空队列返回零和空列表。
- [ ] 重跑上述测试，提交 `feat: process bounded pending intents`。

### Task 2: Codex CLI 与公开输出

**Files:**

- Modify: `team_control/cli.py`
- Test: `tests/test_cli.py`

- [ ] 写失败测试：`process-pending-intents --limit 2` 返回处理数与结果，且每个结果精确等于 `SAFE_INTENT_FIELDS`。
- [ ] 运行 `python3 -m unittest tests/test_cli.py -q`，确认命令尚未注册。
- [ ] 注册严格的 `--limit` 参数；执行时调用 `IntentService.process_pending` 并对结果运行 `safe_intent_summary`。
- [ ] 重跑 CLI 测试，提交 `feat: add Codex pending intent queue command`。

### Task 3: 工作台待处理意图可见性

**Files:**

- Modify: `team_control/dashboard_read_model.py`
- Modify: `apps/dashboard/app.js`
- Test: `tests/test_dashboard_read_model.py`
- Test: `tests/test_dashboard_server.py`

- [ ] 写失败测试：项目 counts 含 `pending_intents`，创建一条 intent 后计数为一；UI 契约仅显示 metric，不包含 `process-pending-intents` 或新增写请求。
- [ ] 运行 dashboard focused tests，确认缺少字段/显示失败。
- [ ] 在 `_counts` 计数 PENDING intents，在 overview 增加“待处理意图”卡片；保持 API 与浏览器执行边界不变。
- [ ] 重跑 focused tests，提交 `feat: show pending intent queue in dashboard`。

### Task 4: 交付、验收与整合

**Files:**

- Modify: `USER_OPERATING_GUIDE.md`
- Modify: `handoff.md`
- Create: `artifacts/dispatches/20260812-005/verification.md`

- [ ] 更新使用手册：Codex 可主动执行受限批次；工作台仅显示积压，不可处理。
- [ ] 运行 `git diff --check <base>..HEAD`、两套 Python 全量测试和 `./scripts/repo-health.sh`。
- [ ] 使用 Claude V4.10.3 对核心和边界包进行一次最终独立验收；仅接受精确 scope acknowledgement 的 `PASS`。
- [ ] 保存安全 receipts、验证记录和 handoff；no-ff 整合到本地 main 后重新运行完整验证。
