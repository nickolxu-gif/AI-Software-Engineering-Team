# Dispatch 20260812-007：控制库 Schema 兼容性预检

## Q1：目标、范围与完成标准

将历史运行库缺少后续控制面表时的通用 `INTERNAL_ERROR` 改为显式、可恢复的 `SCHEMA_MIGRATION_REQUIRED`。范围为控制 CLI、工作台只读健康检查、测试与操作手册；不自动运行迁移、不修改现有运行库、不进入 MVP 3。

完成标准：所有非 `init` CLI 在缺表时 fail closed 且机器可读；工作台缺少其意图表时同样可诊断；`init` 后恢复正常；测试、Claude 独立验收与本地整合通过。

## Q2：风险、授权与验收

L2。Human 已授权 Codex 自主修复已发现的本地工作流缺口。预检严格只读；恢复仍必须经已有幂等 `init`，不得直接编辑 SQLite。

## Q3：执行与审阅

Codex 单写入、测试、整合；Claude Code 做最终独立验收。不可用时不自动降级。

## Q4：隔离与上下文

- 基线：`b1703b7`。
- 分支：`agent/Codex/20260812-007-schema-preflight`。
- Worktree：`.worktrees/20260812-007-Codex-schema-preflight`。
- 允许范围：`team_control/`、相关测试、使用手册、handoff 与本任务工件。

## Q5：状态与纠偏

缺表必须停止原命令并返回明确状态，不得猜测执行。只在已有 `init` 迁移通过本地测试、且数据库路径与仓库绑定可信时恢复。

## Q6：验收

仅接受 Claude V4 可解析 `PASS`；`PASS_WITH_WARNINGS`、`BLOCKED` 或空输出均不得整合。

## Q7：知识回流

沉淀规则：版本演进后的运行库兼容性应在命令边界显式诊断，不能让 SQLite 异常退化为笼统内部错误。
