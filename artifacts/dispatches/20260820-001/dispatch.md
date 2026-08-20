# 20260820-001：同步 GitHub 合并后的交接事实

## 任务来源

- Owner：Codex
- 执行者：Codex
- 风险：L1（文档与交接事实同步；不改变运行逻辑）
- 基线：`77244f2e8b0570bf6f4d4bb120a39784402ce5c9`
- 目标：让 `handoff.md` 与当前 Public GitHub、分支保护、Verified 签名和 PR #2 合并事实一致。

## 七问派活单

### Q1：目的与完成态

把本次 GitHub Remote 配置、分支保护、独立 Review、SSH 签名和 PR #2 合并结果写入现行交接文档。完成时，接手 Codex 只读 `handoff.md` 即能得到当前主线 SHA、远端可见性、保护规则、合并证据和后续授权边界。

### Q2：风险与验收等级

风险等级为 L1。验收要求：文档中的 Public/保护/签名/合并事实与 GitHub 和本地 Git 事实一致；不改变 Python 运行逻辑；默认 Python、Python 3.14、`git diff --check` 和 `repo-health` 全部通过。

### Q3：执行者与 Reviewer

Codex 在隔离 Worktree 内执行和自检。由于这是事实同步和低风险文档变更，不新增模型 Reviewer；验收由 Codex 按命令输出、GitHub PR #2 记录和主线 SHA 复核。

### Q4：上下文、隔离与授权

- Worktree：`.worktrees/20260820-001-Codex-handoff-github-sync`
- Branch：`agent/Codex/20260820-001-handoff-github-sync`
- 允许修改：`handoff.md`、本任务 `artifacts/dispatches/20260820-001/`。
- 不允许：源代码、测试逻辑、全局 Git 配置、GitHub 规则、远端仓库可见性、生产环境和其他任务现场。

### Q5：状态、进度与纠偏

Codex 维护本任务的 `DISPATCHED → IN_PROGRESS → REVIEWING → ACCEPTED` 状态；每次提交前记录差异、测试和 GitHub 事实。发现文档与 GitHub 或主线 SHA 不一致时停止声称完成，回到只读核对；不得用旧 handoff 文字覆盖新事实。

### Q6：验收与失败处理

最终验收检查：`handoff.md` 的 Public、PR #2、Verified 签名、`77244f2e` 合并提交和 schema 初始化事实；双 Python 全量测试各 456 项通过，健康检查 PASS。若测试或事实核对失败，保持 `BLOCKED`，不提交或整合。

### Q7：盘点与知识回流

将稳定结论沉淀在 `handoff.md` 和本任务验证记录中：GitHub 保护门禁不可绕过；签名配置仅为本地仓库级；旧控制库遇到缺表必须使用稳定 `init`。不写入私钥、Token、设备码或原始登录材料。

## 防跑偏

- 不把 GitHub 页面显示的 Review 状态替代本地测试和主线 SHA 证据。
- 不把 Public 仓库继续描述成 Private，也不把历史“待补齐”文字当成当前状态。
- 不删除历史 Worktree、分支或运行库；不使用 `git reset --hard`、`git clean -xdf` 或直接编辑 SQLite。
- 不因本次文档同步自动启动 MVP 3B、发布、远程 Agent 或新的权限申请。

## 未知情况

未能从本地或 GitHub 证明的事实记为 `BLOCKED` 并保留证据；不猜测、不静默改写。下一阶段功能另立任务并重新走授权和七问门禁。
