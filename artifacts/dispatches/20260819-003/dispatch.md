# Dispatch Record：20260819-003 GitHub 分支保护补齐（工具化）

> 状态：`COMPLETED`
> Owner / Builder：Codex
> 风险：L3（外部平台权限、远程配置）
> 基线：`e1563cd`
> 当前候选：`11cd1ed`（分支保护补齐脚本与文档提交）

## Q1 目标、范围与完成标准

- 目标：补齐一套可复用、幂等的 GitHub 分支保护应用脚本，覆盖主分支 main 的基础治理参数（PR 审核、状态检查、会话约束）。
- 范围：仅补齐本地脚本与执行清单；不进行私钥/Token、仓库权限或配置文件的大改；
  不进行自动 push/release；不做额外仓库结构改动。
- 完成标准：脚本可执行，能在已授权环境下正确发起并生效 `branch protection` 更新；当前主分支保护已成功落地。

## Q2 风险、授权与验收

- 本任务与 20260819-002 分离，属于已确认的外部能力补齐。
- 当前 Human 授权范围允许继续补齐，允许使用 `gh api`，但不允许绕过平台权限。
- 验收采用 dry-run 命令与成功落地回执双重记录，含 `protection` GET 回读结果。

## Q3 执行者与路由

- Builder / Integrator：Codex
- Reviewer：仅本地事实确认；外部写入审阅在账号权限放开后复跑。

## Q4 执行上下文与隔离

- 分支：`main`
- Worktree：根 Worktree（无新增并行写任务）
- 关键文件：
  - `scripts/github-branch-protection.sh`
  - `artifacts/dispatches/20260819-003/verification.md`
  - `USER_OPERATING_GUIDE.md`
  - `handoff.md`

## Q5 执行

1. 新增 `scripts/github-branch-protection.sh`：
   - 使用 `gh api /repos/{owner}/{repo}/branches/{branch}/protection`
   - 支持 `--status-checks`、`--required-reviews`、`--dry-run`
   - 失败时明确报错并退出非零。
2. 验证命令已更新到使用手册第 8.3 节；并落地成功回执记录。
3. 任务手册/handoff 同步当前完成状态：仓库已 Public，`main` 分支保护已设置。

## Q6 结果与后续

- 当前任务状态：完成。`main` 分支保护已成功写入（PR 审核与严格状态检查/会话解析要求均已生效）；并新增 `main-branch-guard` ruleset，包含 pull_request、required_linear_history、required_signatures。
