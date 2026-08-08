# Git Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 AI 软件工程团队规范落地为可直接使用的本地 Git 主线、任务分支与 Agent Worktree 机制。

**Architecture:** 使用单仓库 Trunk-Based Development：`main` 是唯一稳定主线，每个写入型任务使用短生命周期分支和项目内 `.worktrees/` 目录隔离。Codex 负责建分支、状态维护、Review 编排、验收与合并；子 Agent 只在授权 Worktree 内工作。

**Tech Stack:** Git 2.x、POSIX shell、Markdown。

---

### Task 1: 建立仓库运行契约

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `AGENTS.md`
- Create: `handoff.md`
- Create: `GIT_WORKFLOW.md`

- [x] **Step 1: 写入 Git 忽略和文本规范**

`.gitignore` 必须忽略 `.worktrees/`、系统缓存、编辑器缓存、常见构建输出、本地环境文件和日志；`.gitattributes` 必须统一文本文件为 LF，并把常见二进制格式标记为 `binary`。

- [x] **Step 2: 写入 Agent 运行契约**

`AGENTS.md` 必须要求 Agent 先读 `handoff.md`，把最新三份协议作为当前执行依据，禁止 Agent 自行合并 `main`、扩大权限或修改任务范围。

- [x] **Step 3: 写入当前交接状态**

`handoff.md` 必须记录当前主线、规范优先级、已完成的 Git 初始化任务、下一步使用方式和已知的 v0.1 七问口径差异。

- [x] **Step 4: 写入 Git 操作手册**

`GIT_WORKFLOW.md` 必须给出分支命名、Worktree 创建、Agent 执行、Codex Review、验收、合并和清理的完整命令。

### Task 2: 添加安全辅助脚本

**Files:**
- Create: `scripts/new-agent-worktree.sh`
- Create: `scripts/repo-health.sh`

- [x] **Step 1: 实现 Worktree 创建脚本**

脚本接收 `<dispatch-id> <agent> <slug>`，校验参数只能包含字母、数字、点、下划线和连字符，确认 `.worktrees/` 已被 Git 忽略，然后从 `main` 创建 `agent/<agent>/<dispatch-id>-<slug>` 分支及对应 Worktree。

- [x] **Step 2: 实现仓库健康检查脚本**

脚本检查必需文档、`main` 分支、`.worktrees/` 忽略规则和工作区状态，并打印 Worktree 清单。

- [x] **Step 3: 验证脚本语法**

Run:

```bash
sh -n scripts/new-agent-worktree.sh
sh -n scripts/repo-health.sh
```

Expected: 两条命令均返回 0，且无语法错误输出。

### Task 3: 初始化 Git 基线

**Files:**
- Create: `.git/` repository metadata

- [x] **Step 1: 初始化唯一稳定主线**

Run:

```bash
git init -b main
```

Expected: 当前仓库分支为 `main`。

- [x] **Step 2: 验证 Worktree 目录被忽略**

Run:

```bash
git check-ignore -q .worktrees/
```

Expected: 返回 0。

- [x] **Step 3: 建立基线提交**

Run:

```bash
git add -- .gitignore .gitattributes AGENTS.md handoff.md GIT_WORKFLOW.md scripts/new-agent-worktree.sh scripts/repo-health.sh CODEX_AGENT_DISPATCH_PROTOCOL.md AGENT_ROLE_AND_MODEL_MATRIX.md SOFTWARE_ENGINEERING_WORKFLOW.md PROJECT_SPEC.md REVIEW_ITERATION_2026-08-08.md docs/superpowers/plans/2026-08-08-git-agent-workflow.md
git diff --cached --check
git status --short
git commit -m "chore: bootstrap AI engineering team repository"
```

Expected: 创建根提交，包含现有规范、运行契约、辅助脚本和实施计划。

### Task 4: 验证可用性

**Files:**
- Test: `scripts/repo-health.sh`

- [x] **Step 1: 运行仓库健康检查**

Run:

```bash
./scripts/repo-health.sh
```

Expected: 输出 `Repository health: PASS`、当前分支和 Worktree 清单。

- [x] **Step 2: 检查 Git 状态**

Run:

```bash
git status --short --branch
git log --oneline --decorate -1
```

Expected: `main` 工作区干净，并显示基线提交。

- [x] **Step 3: 检查 Worktree 脚本的拒绝路径**

Run:

```bash
./scripts/new-agent-worktree.sh bad/id codex sample
```

Expected: 非零退出，并说明参数包含不允许的字符；不得创建分支或目录。

实际正向 Worktree smoke test 已由 `20260808-001` 完成；创建结果的 branch、path、base SHA 均与预期一致，证据见 `GIT_BOOTSTRAP_VERIFICATION.md`。
