# MVP 2C 验证记录

任务：`20260812-006`  
候选 HEAD：`ea77999`  
基线：`8e38632`  
状态：`ACCEPTED — pending local main integration`

## 自动化验证

- `python3 -m unittest discover -s tests -q`：通过。
- `python3.14 -m unittest discover -s tests -q`：通过。
- `git diff --check 8e38632..HEAD`：通过。
- `./scripts/repo-health.sh`：从 `main` 根 Worktree 通过；任务 Worktree 报“必须位于 main”是脚本的预期防护，不是候选失败。

## 独立验收

Claude Code / Sonnet 使用 V4.10.3、无工具、无 session persistence 审阅完整候选。首次审阅为 `PASS_WITH_WARNINGS`，发现三项可复现问题：文字测试未锁定因果 fail-closed 语义、`MVP 2/3` 的 Human 门禁被缩窄、工作台 POST 未说明是既有 MVP 2A 能力。Codex 修复后，仅针对这三项的 delta 审阅为 `PASS`。

安全 receipts：

- `claude-final-proactive-loop/receipt.json`：完整候选，`PASS_WITH_WARNINGS`，不作为验收。
- `claude-final-delta-tightening/receipt.json`：修复 delta，`PASS`，精确 scope acknowledgement 覆盖三个修改文件。

最终合同：普通可写 Codex 请求在健康成功、控制库存在（必要时 `init` 成功）后只运行一次 `process-pending-intents --limit 10`；严格只读和 Dashboard open/view 不初始化、不处理；初始化失败、控制库不可用或队列命令非零时停止后续写入。单条 `REJECTED`/`BLOCKED` 仅记录并继续当前请求。没有 daemon、浏览器处理、Git/远程/发布、审批消费或权限扩大。

## 整合后的运行库恢复验证

首次在主线实际运行队列时，旧的 MVP 1 运行数据库缺少后来新增的 `intents` 表，CLI 因未识别的 SQLite 错误 fail-closed 为 `INTERNAL_ERROR`。Codex 随即停止写入并只读核对：缺失表仅为 `intents`，有 3 个任务、0 条 review、0 条 approval，且既有 `init` 的幂等 schema 迁移只会创建该表。随后通过稳定入口 `scripts/team-control init` 恢复，未直接修改 SQLite；重新执行 `process-pending-intents --limit 10` 返回 `{"attempted":0,"results":[]}`，`intents` 读模型也为空。仓库健康检查仍为 PASS。

## MiMo 盘点

当前没有可用的 MiMo 独立执行入口，未伪造盘点。Codex 的可复用结论是：主动性应以“每次前台请求的一次有上限循环”实现，并用明确的前置条件和非零退出 fail-closed；不能把主动性实现成后台权限或把旧能力伪装成新扩权。
