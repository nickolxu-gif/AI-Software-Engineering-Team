# Dispatch 20260812-004：MVP 2A 受限操作意图适配器

## Q1：目标、范围与完成标准

在本地工作台与 Codex 控制平面之间建立可审计的意图入口。浏览器只能提交 `PAUSE_REQUEST`、`RESUME_REQUEST` 或 `APPROVAL_REQUEST`；Codex 适配器重新核验任务、实际 Git HEAD、状态、审批和幂等性后，才可进行既有状态机允许的状态变更或记录审批预备请求。

完成标准：意图合同、SQLite inbox、Codex 处理器、受限 CLI、loopback HTTP 边界、最小工作台控件和用户指南均有自动化测试；默认 Python 与 Python 3.14 全量测试、`git diff --check` 和仓库健康检查通过。浏览器不得执行 Git、合并、推送、发布或审批 nonce 消费。

非目标：SSE、远程访问、多用户权限、GitHub Remote、任意命令、自动处理意图、Git 写入、真实发布。

## Q2：风险、授权与验收等级

L2。新增本机受限写入入口与状态变化适配器，但保持 loopback-only、最小动作白名单与 Codex 复核。Human 已于 2026-08-12 明确授权启动 MVP 2A。外部访问、权限扩大、nonce 消费、Git 写入、合并、推送或发布仍须新的明确确认。

## Q3：执行与审阅

- Owner / 工程协调：Codex；
- 单一写入 Worktree：`agent/codex/20260812-004-mvp2a-intent-adapter`；
- 独立过程审查：按每个计划任务执行规格符合性与代码质量双审；
- 最终 L2 验收：Claude Code V4.10 不可变 `main...HEAD` 审查包；不可用时默认等待，不自动降级；
- 交付盘点：Mimo 盘点，Codex 审阅后才回流。

## Q4：隔离与允许范围

- Base：`36940c3e5fbb1915142fc3b0a221c63a4f42a581`；
- Worktree：`.worktrees/20260812-004-codex-mvp2a-intent-adapter`；
- 允许范围：计划列出的 `team_control/`、`apps/dashboard/`、`tests/`、用户指南、handoff 与本 dispatch 工件；
- 禁止：`main`、其他 Worktree、Git 配置/remote、第三方依赖、云服务、凭据和无关文件。

## Q5：状态与纠偏

状态：`IN_PROGRESS`。每个计划任务必须先有失败测试（RED）、最小实现（GREEN）和独立双审；发现浏览器能绕过 Codex、可泄露 confirmation/nonce、未重新核验 SHA 或状态无法审计时，立即停止该任务并记录 `BLOCKED`。

## Q6：验收

每个任务完成后运行对应 focused tests 与 `git diff --check`。最终仅接受 Claude V4.10 的可解析 `PASS` 作为独立验收；`PASS_WITH_WARNINGS`、`BLOCKED`、空输出、连接探针通过或替代模型意见均不等价于验收通过。未通过前不整合、不推送。

## Q7：盘点与知识回流

最终候选验收后，由 Mimo 盘点目标对照、关键决策、返工、验证证据和可复用经验；Codex 核实后才更新 handoff/规范。不得把原始 confirmation、nonce 或模型原始输出作为知识沉淀。
