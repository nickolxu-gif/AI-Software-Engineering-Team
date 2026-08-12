# MVP 2A 受控操作意图适配器设计

日期：2026-08-12
任务：`20260812-004`
风险：L2

## 决策

工作台从“严格只读”升级为“受控意图”。浏览器只能提交三种声明式请求：`PAUSE_REQUEST`、`RESUME_REQUEST`、`APPROVAL_REQUEST`。它不执行 Git、状态处理、审批消费、合并、推送或发布。

Codex 通过本地适配器处理 inbox 中的意图，并在持有控制锁时重新核验：任务存在、没有已准备操作、实际 Git HEAD 与意图和控制库记录的 SHA 一致、状态转换合法、恢复时没有待审批项。任何不确定事实都以 `BLOCKED` 或 `REJECTED` 结束并记录事件。

## 边界

- 仅监听 `127.0.0.1`；写入端点要求可信 Host、同源 Origin、进程内 token、精确 JSON 内容类型和最多 8 KiB 正文。
- token 仅在 `GET /api/session` 的当前进程响应中返回，不落库、不写浏览器存储。
- inbox 使用 UUID 意图 ID 与唯一幂等键；确认文字只存域分隔 SHA-256，读模型与 HTTP 响应不返回确认内容或哈希。
- `APPROVAL_REQUEST` 仅登记 `APPROVAL_PREPARATION_REQUESTED`，不会创建审批 nonce 或执行审批。
- 处理意图是 Codex 的显式受控操作，不由浏览器自动触发。

## 数据与可见性

`intents` 表记录动作、目标 SHA、请求哈希、确认哈希（如有）、状态、结果码与时间。工作台任务详情只显示：意图 ID、动作、目标 SHA、状态、结果码和时间。

## 验收

自动化测试必须覆盖合同、持久化、幂等、HEAD 漂移、状态冲突、待审批恢复拦截、HTTP 边界、无敏感字段读回和三按钮 UI 契约。最终候选须通过默认 Python、Python 3.14、差异检查、仓库健康检查，以及一次 Claude V4.10 `PASS` 独立验收后才可整合。
