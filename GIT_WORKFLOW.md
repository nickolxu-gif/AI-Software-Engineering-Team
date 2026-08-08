# Git Agent Workflow

## 1. 目标模型

采用 Trunk-Based Development：

- `main` 是唯一稳定主线，不设长期 `develop`；远程仓库尚未配置。
- 一个写入任务 = 一个短任务分支 = 一个独立 Worktree = 一个写入 Agent。
- 分支：`agent/<agent>/<dispatch-id>-<slug>`。
- Worktree：`.worktrees/<dispatch-id>-<agent>-<slug>`。
- 只有大型、多分支联调确有必要时，才由 Codex 建立临时 `integration/<dispatch-id>`；完成后整合并清理。
- Codex 独占 Worktree add/remove/prune、合并、冲突解决、分支删除、Git config 和所有 `main` 操作。

以下变量仅作命令模板，执行前由 Codex替换为派活单中的真实值：

```bash
repo_root="/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team"
dispatch_id="20260808-001"
agent_name="codex"
slug="example-change"
branch_name="agent/${agent_name}/${dispatch_id}-${slug}"
worktree_path="${repo_root}/.worktrees/${dispatch_id}-${agent_name}-${slug}"
```

## 2. 从七问派活

Codex 为每项工程任务建立 Dispatch Record，并逐项明确：

1. 背景、目标、非目标、交付物、范围、依赖、完成标准和拆分方式；
2. L1/L2/L3 风险、授权和验收等级；
3. 执行 Agent、独立 Reviewer、选择依据、替代者及等待/降级/升级条件；
4. 最小上下文、基础提交、Branch、Worktree、允许/禁止路径、所有者和整合顺序；
5. 状态汇报、证据、阻塞和纠偏机制；
6. Claude Code 验收及不可用时的处置；
7. Mimo 盘点、Codex 审阅和受控知识回流。

派活单必须同时列出验证命令、人工确认条件和清理条件。范围或完成标准不清时，不得创建写入任务。

## 3. Codex 建立 Worktree

未来统一通过 `scripts/new-agent-worktree.sh` 创建：

```bash
cd "$repo_root"
git status --short --branch
git switch main
git status --porcelain
./scripts/new-agent-worktree.sh "$dispatch_id" "$agent_name" "$slug"
git worktree list
git -C "$worktree_path" status --short --branch
```

脚本可用前，仅 Codex 可按等价命令创建；必须先确认 `main` 干净、分支和目录不存在：

```bash
git -C "$repo_root" show-ref --verify --quiet "refs/heads/$branch_name"
test ! -e "$worktree_path"
git -C "$repo_root" worktree add -b "$branch_name" "$worktree_path" main
git -C "$repo_root" worktree list
```

`show-ref` 在分支不存在时返回非零是预期结果；若返回零，必须停止并确认归属。Codex 把 Worktree 绝对路径、基础提交、允许路径、禁止动作和验证命令交给执行 Agent。

## 4. 执行 Agent 原子提交

执行 Agent 只能在所属 Worktree 内执行任务所需的 status、diff、显式 add、commit 和 test：

```bash
git -C "$worktree_path" status --short --branch
git -C "$worktree_path" diff --check
git -C "$worktree_path" diff --stat
git -C "$worktree_path" diff --name-only
<dispatch-test-command>
git -C "$worktree_path" add -- <allowed-path-1> <allowed-path-2>
git -C "$worktree_path" diff --cached --name-only
git -C "$worktree_path" diff --cached --check
git -C "$worktree_path" commit -m "<type>(<scope>): <atomic change>"
git -C "$worktree_path" status --short --branch
git -C "$worktree_path" log -1 --oneline --decorate
```

禁止使用 `git add .` 或隐式全量暂存。一个提交只表达一个可审查、可回退的完整变化。完成汇报必须提供提交号、文件清单、验证命令及结果、未覆盖区域和残余风险。

## 5. Review

Codex 核对提交、授权范围和验证证据，再向独立 Reviewer 提供只读上下文：

```bash
git -C "$repo_root" log --oneline --decorate main.."$branch_name"
git -C "$repo_root" diff --stat main..."$branch_name"
git -C "$repo_root" diff --check main..."$branch_name"
git -C "$repo_root" diff --name-status main..."$branch_name"
git -C "$repo_root" diff main..."$branch_name" -- <allowed-path-1> <allowed-path-2>
```

Reviewer 独立检查目标、非目标、范围、实现、测试、安全、回归风险和证据，输出 `ACCEPT / MODIFY / BLOCK / ESCALATE`。`MODIFY` 退回同一 Worktree 修改并重新 Review；`BLOCK` 或 `ESCALATE` 停止整合并保留现场。

## 6. Claude L3 验收与不可用处置

- L1：Codex 自检和适用验证。
- L2：独立 Reviewer、适用测试和 Codex 复核。
- L3：Claude Code 最终独立验收，至少覆盖架构、实现质量、安全、测试、关键问题和 `ACCEPT / MODIFY / BLOCK`。

Claude Code 不可用时，Codex 只能显式选择：

1. **等待**：保持 `REVIEWING` 或 `BLOCKED`，不整合、不发布；
2. **降级**：仅在 Claude 报告限额或配额失败且 Human 明确批准后，使用已授权替代 Reviewer，并增加测试、双审或 Human 复核；替代审查不等价于 Claude 验收；
3. **升级**：风险、权限或残余不确定性超出已有授权时交 Human 决定。

降级记录必须包含不可用原因、替代者、覆盖范围、额外验证、残余风险和发布限制。L3 在质量门不足时只能保留为“待验收候选”。

## 7. Codex 整合

若任务期间 `main` 已前进，只能由 Codex 在任务 Worktree 合入最新 `main`、解决冲突并重跑验证：

```bash
git -C "$repo_root" switch main
git -C "$repo_root" status --porcelain
git -C "$worktree_path" merge main
git -C "$worktree_path" status --short --branch
<dispatch-test-command>
```

默认使用 `--no-ff`，保留任务边界、原子提交和审计轨迹：

```bash
git -C "$repo_root" switch main
git -C "$repo_root" status --porcelain
git -C "$repo_root" merge --no-ff "$branch_name" -m "merge: $dispatch_id $slug"
```

只有分支含多个修正型提交、这些中间提交没有独立审计价值，且主线需要一个清晰原子变化时，Codex 才使用 squash：

```bash
git -C "$repo_root" switch main
git -C "$repo_root" status --porcelain
git -C "$repo_root" merge --squash "$branch_name"
git -C "$repo_root" diff --cached --check
git -C "$repo_root" commit -m "<type>(<scope>): <change> [$dispatch_id]"
```

执行 Agent 不得选择整合方式、合并或解决冲突。任何整合前必须满足主线干净、Review、验收和授权门禁。

## 8. 整合后验证与回退

Codex 在 `main` 上重新执行全部受影响验证并保存证据：

```bash
git -C "$repo_root" status --short --branch
git -C "$repo_root" log -1 --oneline --decorate
git -C "$repo_root" diff --check HEAD^ HEAD
<dispatch-test-command>
```

验证失败时停止发布和清理，记录命令、错误与影响并回到 `MODIFY / BLOCK / ESCALATE`。已提交变更需要回退时优先使用可审计的 `git revert <commit>`；不得以历史改写掩盖问题。

## 9. Codex 清理

仅当整合后验证通过、证据已记录且 Worktree 无未提交变更时清理。先检查：

```bash
git -C "$worktree_path" status --porcelain
git -C "$repo_root" worktree list
git -C "$repo_root" branch --contains "$branch_name"
git -C "$repo_root" merge-base --is-ancestor "$branch_name" main
```

`--no-ff` 合并后，确认分支已被 `main` 包含，再由 Codex 清理：

```bash
git -C "$repo_root" worktree remove "$worktree_path"
git -C "$repo_root" branch -d "$branch_name"
git -C "$repo_root" worktree prune
git -C "$repo_root" worktree list
```

squash 后任务分支不是 `main` 的祖先，安全删除检查会拒绝。Codex 必须保留源分支，直到 Human 根据已记录的 squash 提交、验证证据和残余风险明确批准后续删除；不得绕过保护性拒绝。

## 10. 禁令、确认与远程边界

- 禁止使用 `--force`、`git clean -xdf`、`git reset --hard`；禁止强制删除脏 Worktree、未整合分支、未知目录、stash、标签或远端引用。
- 禁止执行 Agent 在根工作区写入、操作 `main`、merge、rebase、push、管理 Worktree/分支或更改 Git config。
- 回退优先 `git revert`，保留审计轨迹；不得丢弃或覆盖未知变更。
- 删除/覆盖关键原件、批量迁移、生产或真实业务系统操作、外部发送、敏感数据、权限扩大、不可逆操作、强制操作、验收降级及无法化解的关键冲突，必须先取得 Human 明确确认。
- 远程仓库尚未配置。配置远端、fetch、pull、push、删除远端分支或标签均不属于当前初始化范围；未来必须由 Codex 在明确授权后执行。
