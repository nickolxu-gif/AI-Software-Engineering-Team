# GitHub Remote 启动前置清单（20260819-001）

## 当前状态核对

- 仓库 remote：未配置（`git remote -v` 空）；
- 主要主线：`main`；
- 当前 HEAD：`4eea2784d241da2c5b4ce68a3506d9dd5ea710e2`；
- 当前状态：工作树仅 clean（无未提交变更）；
- 已完成：MVP0/1/2/2A/2B/2C/2D/3A 均已本地集成。

## 必须由 Human 提供的参数（BLOCK 条件）

- GitHub 账号 / 组织；
- 目标远端仓库名；
- Private/Public（建议 Private）；
- 认证方式（推荐最小权限 Token / App，附有效期与 scope）；
- 首次 push 授权（是 / 否）；
- 是否允许创建新仓库；
- 是否开启 2FA；
- 是否绑定 PR Review 与分支保护；
- 是否要求必经 CI 绿灯；
- 恢复策略：误推/误配置的回滚窗口与联系人。

任何缺失项直接阻断：`BLOCKED: Missing human decision inputs`。

## 验收前置（进入执行前）

- `git status --short`：必须仅看到 clean；
- `git remote -v`：无 remote；
- `./scripts/repo-health.sh`：PASS；
- 运行时控制库与主线都可核验（无 schema 阻塞）；
- 用户已确认执行该任务是下一步授权目标，而非“仅咨询/对比”。

## 远端实施约束（后续 20260819-002 必须执行）

- 不默认添加第三方 webhook，除非用户另行明确；
- 不配置敏感 token 到仓库文件；
- 先 `origin` 后 `upstream` 不反向覆盖；
- 首次 push 前完成分支保护草案；
- 分支保护至少包含：require PR（必要）+ review（至少1）+ status checks（必要）；
- 禁止在未验证前触发自动合并/自动发布；
- 失败恢复：保持本地工作可回退，保留执行命令与回执时间戳。

## 失败与重试

- 任一项前置失败：转为 `BLOCKED` 并保留阻断原因；
- 用户补齐参数后可重试；
- 只有用户明确 `yes` 且参数齐全后，Codex 才创建新的执行派活（`20260819-002`）；
- `20260819-001` 结论不含外部服务副作用，仅作为决策前置门禁。
