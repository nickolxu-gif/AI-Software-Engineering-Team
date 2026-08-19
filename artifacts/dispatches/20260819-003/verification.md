# 20260819-003 GitHub 分支保护补齐验收记录

- 任务：GitHub 分支保护补齐（工具化）
- 基线：`e1563cd`
- 候选：`11cd1ed`
- 状态：`PASS`

## 1. 脚本与环境核对

- 文件存在：`scripts/github-branch-protection.sh`
- 执行权限：`chmod +x` 已设置
- GitHub 登录状态：`gh auth status` 通过（repo 认证可用）

## 2. 脚本功能验证

- dry-run：`./scripts/github-branch-protection.sh --owner nickolxu-gif --repo AI-Software-Engineering-Team --branch main --status-checks --dry-run`
- 结论：参数解析与 payload 生成正确（含 `required_status_checks.strict=true`）。

## 3. 真实 API 验证

执行：

```bash
./scripts/github-branch-protection.sh --owner nickolxu-gif --repo AI-Software-Engineering-Team --branch main --status-checks
```

返回：

```json
{"url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection","required_status_checks":{"url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection/required_status_checks","strict":true,"contexts":[],"contexts_url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection/required_status_checks/contexts","checks":[]},"required_pull_request_reviews":{"url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection/required_pull_request_reviews","dismiss_stale_reviews":false,"require_code_owner_reviews":false,"require_last_push_approval":false,"required_approving_review_count":1},"required_signatures":{"url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection/required_signatures","enabled":false},"enforce_admins":{"url":"https://api.github.com/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection/enforce_admins","enabled":false},"required_linear_history":{"enabled":false},"allow_force_pushes":{"enabled":false},"allow_deletions":{"enabled":false},"block_creations":{"enabled":false},"required_conversation_resolution":{"enabled":true},"lock_branch":{"enabled":false},"allow_fork_syncing":{"enabled":false}}
```

同时：

- `gh api "/repos/nickolxu-gif/AI-Software-Engineering-Team/rulesets"` 返回 `[]`（可访问）；
- `gh api "/repos/nickolxu-gif/AI-Software-Engineering-Team/branches/main/protection"` 同步返回当前保护配置。

## 4. 风险与后续

- 当前公共仓库下，分支保护成功落地；
- 仍可后续补齐 rulesets 细化策略（未纳入本任务硬性范围）。
