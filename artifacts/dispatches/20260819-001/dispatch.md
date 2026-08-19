# Dispatch Record：20260819-001 GitHub Remote 前置评估与风险对齐

> 状态：`PLANNING`
> Owner / Builder：Codex
> 风险：L3（外部平台身份、权限、远端发布与回滚）
> 基线：`4eea2784d241da2c5b4ce68a3506d9dd5ea710e2`（当前 `main`）
> 当前候选：无代码变更候选（本任务为可执行清单和授权前置）

## Q1 目标、范围与完成标准

本任务目标是把 GitHub Remote 从“设计口头口令”提升为“可执行的授权门控方案”：

1. 明确并冻结 GitHub Remote 的启动输入（账户/组织、仓库名、可见性、认证方式）；
2. 明确最小安全边界（凭据、权限、分支保护、PR 与检查链路）；
3. 产出一份可复用的执行清单与回滚脚本；
4. 在用户明确 `yes` 之后，才进入下一任务（远端初始化/首次 push）。

本任务不做任何 remote 添加、push、凭据落地、token 写库、仓库创建、分支保护变更或 CI 配置。

完成标准：

- 所有输入项齐全，且与当前仓库状态（当前仅 `main`、无 remote）对齐；
- 决策清单可复核且可用于下一任务派活；
- 用户确认后可直接进入“GitHub Remote 独立实施”任务执行脚本化动作。

## Q2 风险、授权与验收

- 风险级别 L3：涉及外部账号、权限与发布通道，按 `NEEDS_HUMAN_APPROVAL` 处理；
- Human 已明确“继续推进”后，下一任务将由用户提供以下项后开始：
  - GitHub 账户 / 组织；
  - 目标仓库名；
  - `private/public`；
  - 认证方式（token/SSH/app 登录）；
  - 是否允许创建远端仓库与首次 push；
  - 是否启用分支保护与 PR Review 强制。
- 验收：本任务产物为一份“可执行前置清单 + 风险关闭条目 + 验收条件清单”，不进行代码提交。

## Q3 执行者、Reviewer 与路由

- Builder：Codex（本地文档与任务结构化）  
- Reviewer：无外部代码审阅（无代码变更）；如后续变更涉及脚本/配置，会恢复 Claude Code 最终审阅。
- 人工门禁：未确认前不执行远端动作；明确授权后再建立正式执行派活（新 dispatch）。

## Q4 上下文、隔离与范围

- 目标路径：`artifacts/dispatches/20260819-001/` 下的两份说明文件；
- 不涉及 `team_control/`、`apps/`、`tests/`、`scripts/` 的变更；
- 不修改 Git 配置、不操作 `git remote`、不改 `.git/config`、不改全局配置；
- 仍要求主线 `main` 干净，工作树无未跟踪外部残留。

## Q5 执行步骤

1. 生成 `handoff` 对齐清单（当前状态快照）；
2. 生成 GitHub Remote 执行参数清单（含分支保护、主备权重、回滚动作）；
3. 生成“不能自动跳过”的阻断项（若有缺失则 BLOCK）；
4. 形成“用户确认模板”和“任务启动门禁”；
5. 待用户确认后，转入 `20260819-002`（GitHub Remote 独立实施）。

## Q6 交付物与边界说明

- 交付：`artifacts/dispatches/20260819-001/dispatch.md`、`artifacts/dispatches/20260819-001/remote-readiness-checklist.md`
- 明确声明：本任务不执行任何远程动作；
- 未做任何 fallback reviewer、CodeBuddy、Hermes 自动降级；
- 主线未变化，不涉及 `merge`、`push`、`release`。

## Q7 盘点与知识回流

- 将该前置任务记录为“高影响任务前的闭环门禁模板”；
- 后续若进入 `20260819-002`，此处的清单用于自动决策，减少反复确认时间；
- 持续保留“用户确认输入即是任务起始条件”原则的证据链。
