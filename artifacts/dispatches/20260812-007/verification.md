# 20260812-007 控制库 Schema 兼容性预检验证

候选 HEAD：`703db23`
基线：`b1703b7`
状态：`ACCEPTED — pending local main integration`

## 验证结果

- `python3 -m unittest discover -s tests -q`：通过。
- `python3.14 -m unittest discover -s tests -q`：通过。
- `git diff --check b1703b7..HEAD`：通过。
- `./scripts/repo-health.sh`：主线根 Worktree 通过；候选 Worktree 不运行该脚本，因其强制要求 `main`。

## 回归覆盖

- 缺失 `intents` 表时，`status` 与队列 CLI 均返回单行 `SCHEMA_MIGRATION_REQUIRED`，stdout 为空；显式 `init` 后队列恢复为空批次。
- 真实表缺列，或同名对象是 view 时，CLI 返回 `SCHEMA_UNSUPPORTED`；不会把 SQLite 异常降级为 `INTERNAL_ERROR`。
- 工作台读模型对缺表返回 `SCHEMA_MIGRATION_REQUIRED`，对 view/缺列返回 `SCHEMA_UNSUPPORTED`；HTTP `/api/health` 将缺表状态映射为 503。

## 独立验收

Claude Code / Sonnet 使用项目全局 `codex-claude-verify` 命令审阅 `b1703b7..703db23`，结论 `PASS`，无 findings。安全 receipt：`claude-final-schema-preflight-receipt.json`。未使用 CodeBuddy 或其他 fallback。

## 知识回流

控制库演进必须在命令边界进行只读、确定性的 schema 预检：缺少已知表可提示显式 `init`；非表对象或缺列属于未知不兼容，必须 `SCHEMA_UNSUPPORTED` 并保持 `BLOCKED`。不应把任何只读请求变成自动迁移。
