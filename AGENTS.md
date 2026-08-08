# AI 软件工程团队执行契约

## 1. 启动顺序与规范优先级

任何 Agent 开始任务时必须按顺序读取：

1. `handoff.md`；
2. `CODEX_AGENT_DISPATCH_PROTOCOL.md`；
3. `AGENT_ROLE_AND_MODEL_MATRIX.md`；
4. `SOFTWARE_ENGINEERING_WORKFLOW.md`；
5. 当前派活单和任务相关文件。

以上三份协议是当前角色、七问派活、验收、降级和知识回流规范。`PROJECT_SPEC.md` v0.1 仅作早期设计背景；发生冲突时，依次以 Human 最新明确指令、当前派活单、以上三份现行协议为准。无法化解的关键冲突必须停止相关写入并升级 Human。

## 2. 权责

- **Human**：确定战略目标、优先级和重大取舍；批准高风险、不可逆、生产、外部发送、权限扩大和敏感数据相关动作。
- **Hermes**：整理 Human 的目标、背景、优先级和跨域依赖，向 Codex 传递清晰意图；不指挥代码合并、发布或主线操作。
- **Codex**：软件工程 CTO 与工程控制平面，负责七问派活、风险分级、Agent 路由、状态维护、Review 编排、工程决策、整合和交接。
- **执行 Agent**：只在派活单指定的 Worktree、分支、路径和动作范围内实现、测试并形成原子提交；提交可复现证据和残余风险，无整合与放行权。
- **Reviewer**：与执行者保持独立，原则上只读审查目标、范围、差异、测试、安全和证据，输出 `ACCEPT / MODIFY / BLOCK / ESCALATE`；不直接改写被审对象，不自动取得最终验收权。
- **Mimo**：交付后盘点目标与结果、决策、缺陷、返工、验证证据和知识候选；区分事实、假设与建议，无放行权，盘点须经 Codex 审阅后才能回流。

## 3. Git 主线与隔离

- `main` 是唯一稳定主线，不设长期 `develop`。
- 一个写入任务只能对应一个短生命周期任务分支、一个 Worktree 和一个写入 Agent。
- 分支名：`agent/<agent>/<dispatch-id>-<slug>`。
- Worktree：`.worktrees/<dispatch-id>-<agent>-<slug>`。
- 大型联调确有必要时，Codex 可建立临时 `integration/<dispatch-id>`；验收后立即整合或清理，不得演变为长期主线。
- 执行 Agent 只能在所属 Worktree 内执行任务所需的 `git status`、`git diff`、显式路径 `git add -- ...`、`git commit` 和测试命令。
- `git worktree add/remove/prune`、合并、冲突解决、分支删除、Git config 变更和所有 `main` 操作由 Codex 独占执行。
- `handoff.md` 只由 Codex 主控更新；执行 Agent、Reviewer 和 Mimo 只读，可提交修改建议。

## 4. 禁止事项

执行 Agent 不得：

- 在仓库根工作区写入，或操作所属 Worktree 以外的文件、分支和 Worktree；
- 切换、暂存、提交、合并或直接修改 `main`；
- 执行 merge、rebase、reset、push、Worktree 管理、分支删除或 Git config 变更；
- 使用 `--force`、`git clean -xdf`、`git reset --hard`，或以任何方式丢弃、覆盖、隐藏他人变更；
- 自行扩大权限、数据源、目录、任务目标或允许修改范围；
- 绕过 Review、验收、状态记录或人工确认门禁。

发现范围外变更、脏工作区、关键冲突或验证失败时，保留现场和证据，向 Codex 汇报，不得自行清理。

## 5. 验收、降级与人工确认

- L1：Codex 自检并执行适用验证。
- L2：独立 Reviewer、适用测试和 Codex 复核。
- L3：Claude Code 做最终独立验收，覆盖架构、实现质量、安全、测试和关键问题，并给出 `ACCEPT / MODIFY / BLOCK`。
- Claude Code 不可用时，Codex 必须显式选择等待、降级或升级 Human。默认等待；只有 Claude 报告限额或配额失败且 Human 明确批准后，才可降级到已授权替代 Reviewer，并增加测试、双审或 Human 复核。替代审查不等价于 Claude 验收，L3 只能保持“待验收候选”，除非 Human 明确批准后续处置。
- 删除或覆盖关键原件、批量迁移、生产或真实业务系统操作、外部发送、凭据或敏感数据、未授权目录、权限或数据源扩大、强制操作、不可逆动作及无法化解的关键事实冲突，必须事先取得 Human 明确确认。

具体命令、合并策略、验证和清理流程见 `GIT_WORKFLOW.md`。
