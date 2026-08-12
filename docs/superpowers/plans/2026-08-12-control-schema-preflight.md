# 控制库 Schema 兼容性预检 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让历史控制库缺表在 CLI 与工作台中显式、可恢复地 fail closed。

**Architecture:** `ControlStore` 提供只读表集预检；CLI 在非 `init` 服务调用前使用它，工作台将 `intents` 纳入现有 snapshot schema 校验并映射缺表到迁移状态。

**Tech Stack:** Python 3、sqlite3、unittest、Markdown。

---

### Task 1: CLI 失败测试

**Files:**
- Modify: `tests/test_cli.py`
- Test: `tests/test_cli.py::CliTests::test_missing_intents_table_requires_schema_migration_before_queue_processing`

- [ ] 写入测试：初始化 fixture，删除 `intents`，运行 `process-pending-intents --limit 1`，断言非零、stdout 为空、stderr JSON 的 code 为 `SCHEMA_MIGRATION_REQUIRED`；随后运行 `init`，断言队列返回 `attempted == 0`。
- [ ] 运行 `python3 -m unittest tests/test_cli.py -q`，预期测试失败，因为当前 code 是 `INTERNAL_ERROR`。

### Task 2: 最小只读预检

**Files:**
- Modify: `team_control/errors.py`
- Modify: `team_control/store.py`
- Modify: `team_control/cli.py`

- [ ] 在 `errors.py` 增加 `SchemaMigrationRequiredError`，code 固定为 `SCHEMA_MIGRATION_REQUIRED`。
- [ ] 在 `store.py` 定义完整必需表集合，增加只读 `require_schema_tables()`；缺表抛出新错误并只列出排序后的表名。
- [ ] 在 CLI 的 `init` 分支之后、任何 `ControlPlane`/`IntentService` 创建之前调用预检。
- [ ] 重跑 CLI 测试，预期通过。

### Task 3: 工作台一致性

**Files:**
- Modify: `tests/test_dashboard_read_model.py`
- Modify: `team_control/dashboard_read_model.py`

- [ ] 写失败测试：删除 `intents` 后 `model.health()` 抛出 `DashboardUnavailableError`，code 为 `SCHEMA_MIGRATION_REQUIRED`。
- [ ] 将 `intents` 加入工作台必需 schema；表缺失映射为 `SCHEMA_MIGRATION_REQUIRED`，字段缺失仍为 `SCHEMA_UNSUPPORTED`。
- [ ] 重跑工作台读模型测试，预期通过。

### Task 4: 文档、验收与整合

**Files:**
- Modify: `USER_OPERATING_GUIDE.md`
- Modify: `handoff.md`
- Create: `artifacts/dispatches/20260812-007/verification.md`

- [ ] 说明错误含义和唯一恢复路径；禁止直接编辑 SQLite。
- [ ] 运行 `python3`、`python3.14` 全量测试、`git diff --check` 和主线健康检查。
- [ ] 使用 Claude V4 focused immutable packet 审阅；仅 `PASS` 后生成安全 receipt、更新 handoff、no-ff 整合 main 并复验。
