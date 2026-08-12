# MVP 2D 受控任务需求入口设计

> 任务：`20260812-008`  
> 风险：L2

## 决策

在现有任务详情内的三类受控意图之外，增加独立的“任务需求收件箱”。总览页展示表单，用户只能提交 `title`、`objective` 和可选 `context`。服务把请求写入独立 `task_intake_requests` 表，状态固定从 `PENDING` 变为由 Codex 会话处理的终态；本次只实现接收、读取和审计，不把浏览器输入自动转换为工程执行。

Codex 在下一次普通自然语言请求中读取待处理需求，补齐七问、风险、Agent、隔离与验收策略，并按既有 `start` 流程创建正式任务。这避免仅凭浏览器文本自动分配风险、创建 Worktree 或执行未审查的工程动作。

## 备选方案与取舍

1. **直接在浏览器创建正式任务**：交互快，但会把风险分级、Worktree 生命周期和 Git 写入扩展到浏览器，拒绝。
2. **复用现有 `intents` 表**：该表强制绑定既有 `dispatch_id` 与目标 SHA；新需求在正式任务创建前没有这些事实，拒绝。
3. **独立收件箱（采用）**：新需求与既有任务隔离，最小化 schema 与 API，保留 Codex 的七问和执行控制权。

## 合同与边界

- POST ` /api/task-intakes` 仅在 loopback、同源 Origin、进程内 session token、精确 `application/json` 和最大 8 KiB body 下接受。
- 请求字段仅为 UUID `idempotency_key`、1–120 字符 `title`、1–2000 字符 `objective`，以及可选 1–2000 字符 `context`；拒绝未知字段、控制字符、孤立 surrogate 和过深/非对象 JSON。
- 存储记录生成 UUID `intake_id`，请求哈希和明确状态；公开 API 只返回 `intake_id`、`title`、`objective`、`status`、`result_code`、时间。`context` 与请求哈希不读回，避免工作台放大敏感或冗长内容。
- 初始状态为 `PENDING`。本次不增加浏览器处理端点、后台循环、自动任务创建、Git 操作、审批或远程访问。
- 总览只展示有上限的待处理摘要和表单。提交成功后提示“已提交给 Codex，等待下一次工程会话处理”。

## 数据与兼容性

`task_intake_requests` 是新的已知 schema 表；`ControlStore.initialize()` 只做 `CREATE TABLE IF NOT EXISTS`。缺少该表应使用已有 `SCHEMA_MIGRATION_REQUIRED` fail-closed 路径，普通可写 Codex 请求再显式 `init`。表或列不兼容继续返回 `SCHEMA_UNSUPPORTED`，不得直接编辑 SQLite。

## 验收

测试必须证明：合同拒绝无效输入；提交幂等；数据库只增加 intake 记录而非任务、Worktree 或 Git 改动；HTTP 安全边界不回归；读取摘要不泄露 `context` 或哈希；UI 不出现 Git/merge/push/处理动作。最终通过两套 Python 全量测试、差异/健康检查和 Claude `PASS` 后，才整合本地 main。
