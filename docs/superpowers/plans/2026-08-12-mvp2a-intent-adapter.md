# MVP 2A 实施计划

1. 定义严格意图合同、规范化与哈希；完成边界测试。
2. 增加 SQLite inbox、幂等性和不可泄露的事件记录。
3. 实现 Codex 意图处理器，在控制锁中重验 SHA、状态、审批和已准备操作。
4. 提供 Codex 专用 `intents` 与 `process-intent` CLI。
5. 增加 loopback-only HTTP 会话和受限提交端点。
6. 在工作台显示安全意图摘要，并提供暂停、恢复、审批准备三个提交控件。
7. 更新操作手册和 handoff；完成全量验证、一次最终 Claude V4.10 审阅、盘点与本地整合。

非目标：远程访问、多用户权限、自动处理、Git 写入、审批消费、GitHub Remote、merge、push、发布。
