# Dispatch Record：20260812-008 MVP 2D 受控任务需求入口

> 状态：`ACCEPTED and integrated`
> Owner / Builder：Codex
> 风险：L2（本地受控写入与浏览器边界）
> 基线：`6858bb715b4b8bb738a3d4e62fc2542ea616169c`

## Q1 目标、范围与完成标准

**目标**：让本地工作台提供一个简短的“新工程需求”表单，用户可提交标题、目标和可选背景；提交结果仅进入本地待处理收件箱，供 Codex 在后续正常工程会话中转换为七问派活单。

**不做**：浏览器不得创建任务、Worktree、分支、Git 提交、合并、push、发布、审批 nonce 或后台任务；不得引入远程服务、账户、通知、守护进程或 GitHub。

**完成标准**：输入合同、持久化、HTTP 边界、工作台表单和只读状态均有自动化覆盖；默认 Python 与 Python 3.14 全量测试、差异检查、仓库健康检查和 Claude 独立验收通过。

## Q2 风险与授权

L2。新增本地 SQLite 记录和 loopback POST，但没有外部 I/O、权限扩大或不可逆动作。Human 已授权本地实施、测试、Claude 验收和本地整合；GitHub、push、发布、外部发送或审阅降级仍需另行明确授权。

## Q3 执行者、Reviewer 与路由

- Builder / Integrator：Codex。
- Reviewer：Claude Code，使用无工具、无 session persistence 的只读 V4 包进行最终验收。
- 无可解析 Claude `PASS` 时保持 `BLOCKED`；不得自动启用 CodeBuddy 或其他 fallback。

## Q4 上下文与隔离

- Branch：`agent/Codex/20260812-008-task-intake`
- Worktree：`.worktrees/20260812-008-Codex-task-intake`
- 相关模块：`team_control/`、`apps/dashboard/`、`tests/`、`USER_OPERATING_GUIDE.md`。
- 只有 Codex 写入此 Worktree；现有旧 Worktree 和运行数据库保持原状。

## Q5 执行、状态与纠偏

先以测试定义边界，再实现最小独立 `task_intake_requests` 收件箱。浏览器只可提交和读取安全摘要；Codex 在自然语言会话中读取待处理需求、补齐七问并另起正式执行任务。任何 schema、origin、token、大小或未知 action 问题 fail closed，不自动修复 SQLite。

## Q6 验收

验证合同边界、幂等、无任务创建副作用、loopback/origin/token/content-type/8 KiB 限制、只读摘要和 UI 行为。Claude 审查核心服务、HTTP 与 UI delta，并给出明确 `PASS`。

## Q7 盘点与知识回流

保存派活单、设计、计划、测试与 Claude 回执到本任务 artifacts。Codex 整合后更新 `handoff.md` 与使用手册；仅沉淀可复用的“浏览器需求收件箱不等于工程执行权”原则。
