# MVP 2B Codex 受控意图队列设计

任务：`20260812-005`

## 决策

MVP 2A 已能安全接收浏览器意图，但 Codex 必须逐个知道 ID 才能处理。MVP 2B 增加一个 Codex 触发、有限批次、非后台的队列入口，使 Codex 在每次主动工作循环中能处理有限数量的待处理意图，并让工作台显示当前积压量。

采用 `process-pending-intents --limit 1..25`，而不是后台 daemon 或浏览器自动调用。后台 daemon 会扩大运行时权限、故障恢复和长期资源管理面；浏览器必须继续只是意图提交者，不能成为工程执行者。

## 不变量

1. 批处理不直接写任务状态；每条意图必须复用单条 `process` 的控制锁、实际 HEAD、记录 HEAD、状态、审批和 prepared-operation 核验。
2. 选择顺序固定为 `created_at, intent_id`；运行中新增意图留待下次调用。
3. 单条得到 `APPLIED`、`REJECTED` 或 `BLOCKED` 后继续下一条；未预期系统异常立即停止，不伪造其余结果。
4. 公开汇总只使用 `safe_intent_summary` 白名单字段；不得输出确认、哈希、nonce、请求哈希或幂等键。
5. 工作台只增加待处理数；不新增浏览器处理入口、不新增 POST 路由、不新增自动触发处理。

## 数据流与测试

`ControlStore.list_pending_intents(limit)` 在只读连接中按稳定顺序取得 snapshot。`IntentService.process_pending(limit)` 对 snapshot 的每个 ID 调用既有 `process`，返回 `{attempted, results}`。CLI 是 Codex 使用的显式调用点。

`DashboardReadModel._counts` 增加 `pending_intents`，工作台显示“待处理意图”。测试覆盖稳定批次、上限、空队列、单条失败继续、CLI 白名单输出、项目计数和 UI 显示。最终验收必须对核心代码与边界代码分别得到可解析 `PASS`。
