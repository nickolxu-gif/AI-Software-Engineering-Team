# 项目交接状态

> 日期：2026-08-08
> 维护者：仅 Codex 主控。其他 Agent 和 Reviewer 只读，可提交修改建议。
> 生效条件：本文件随验证分支合并后生效。

## 当前状态

**Git 治理已完成，`main` 基线已建立。**

- `main` 是唯一稳定主线；基线 SHA 为 `cd459565b8bb24156f92e400a11769d254eccda9`。
- Codex 已使用 `--no-ff` 合并验证分支，并已清理该分支及其 Worktree；最终仅保留 `main` 根 Worktree。
- 当前没有配置 remote。
- `scripts/new-agent-worktree.sh` 与 `scripts/repo-health.sh` 已完成；可复核命令事实见 `GIT_BOOTSTRAP_VERIFICATION.md`。
- Git bootstrap 验证工件：`GIT_BOOTSTRAP_VERIFICATION.md`、`GIT_BOOTSTRAP_REVIEW_LOG.md`。
- `REVIEW_ITERATION_2026-08-08.md` 是状态为 `MODIFY` 的审阅证据，不是现行规范。

## AI 软件工程团队控制平面

- Human 已确认 MVP 0 → MVP 1 → MVP 2 → MVP 3 的渐进实施顺序。
- 控制平面设计：`docs/superpowers/specs/2026-08-08-ai-engineering-team-control-plane-design.md`。
- MVP 0 实施计划：`docs/superpowers/plans/2026-08-08-mvp0-control-plane.md`。
- 这里的 `Minor` 指 `git worktree add` 失败后可能残留目录、分支或 Worktree metadata；不是对象存储。Codex 必须先检查实际 Git 状态，再决定安全重建或转为 `BLOCKED`，不得自动强删未知数据。
- 当前授权阶段：执行 MVP 0；MVP 1、2、3 和 GitHub Remote 尚未进入实施。

可使用以下命令复核最终 Worktree 和分支状态：

```bash
git worktree list
git branch --list
```

前者应仅显示 `main` 根 Worktree；后者不应包含已清理的验证分支。

## 现有四份规范

1. `CODEX_AGENT_DISPATCH_PROTOCOL.md`：现行七问派活、风险、验收和状态协议。
2. `AGENT_ROLE_AND_MODEL_MATRIX.md`：现行角色、模型路由与 Reviewer 降级矩阵。
3. `SOFTWARE_ENGINEERING_WORKFLOW.md`：现行工程生命周期、门禁、异常处理与知识回流流程。
4. `PROJECT_SPEC.md` v0.1：早期设计背景，不是当前冲突口径的最终依据。

规范优先级：Human 最新明确指令与当前派活单 > 前三份现行协议 > `AGENTS.md` 与 `GIT_WORKFLOW.md` > `PROJECT_SPEC.md` v0.1 > 更早计划和讨论。新旧信息冲突时，以最新明确指令为准，并保留冲突、依据和决策记录。

## 审阅证据与现行对齐

- `REVIEW_ITERATION_2026-08-08.md`：状态 `MODIFY`，仅作为审阅过程与问题证据，不属于上述现行规范。
- 该记录提出的三个口径已经体现在现行协议中：CodeBuddy GLM 5.2 降级需 Human 明确 `yes`；K3 仅作实验用途；Claude 是独立质量门，最终工程决定由 Codex/Nick 基于证据作出。
- 审阅记录本身不会自动覆盖现行协议；未来如出现新增差异，由 Codex/Human 依据证据处理并更新相应规范。

## `PROJECT_SPEC.md` v0.1 七问与当前协议的已知差异

| v0.1 七问 | 当前协议口径 |
|---|---|
| Q1 任务目标和类型 | Q1 扩展为背景、目标、非目标、交付物、允许范围、依赖、完成标准及是否拆分。 |
| Q2 任务复杂度 | Q2 改为风险、授权和验收等级，L1/L2/L3 直接绑定 Reviewer、Claude 和 Human 门禁。 |
| Q3 是否拆分 | 拆分并入 Q1 与工作流 Phase 2；Q3 改为执行者、Reviewer、选择理由、替代者及等待/降级/升级条件。 |
| Q4 需要哪些角色 | 角色选择并入 Q3；Q4 改为最小上下文、Branch、Worktree、基础提交、授权范围、所有者和合并隔离。 |
| Q5 调用哪个模型 | 模型路由并入 Q3，按风险、上下文、独立性、可用性和额度选择，不能只按模型偏好。 |
| Q6 完成标准 | 完成标准前移至 Q1；Q6 改为 Claude Code 最终验收及不可用时的等待、降级或升级规则。 |
| Q7 失败如何处理 | 失败和纠偏由 Q5 的状态与证据机制覆盖；Q7 改为 Mimo 盘点、Codex 审阅和受控知识回流。 |

后续 Dispatch Record 必须回答当前协议七问，不能以 v0.1 的简化七问代替验收记录。

## Git 目标模型

- 推荐 Trunk-Based Development，`main` 是唯一稳定主线，不设长期 `develop`。
- 一个任务分支对应一个独立 Worktree 和一个写入 Agent；命名及命令以 `GIT_WORKFLOW.md` 为准。
- Codex 独占 Worktree 生命周期、主线、合并、冲突解决、分支删除和 Git 配置操作。
- 执行 Agent 只在所属 Worktree 内修改、测试和原子提交；Reviewer 独立审查；L3 由 Claude Code 最终验收。
- 大型联调只使用临时 `integration/<dispatch-id>`，不得形成第二条长期主线。

## 下一次任务执行

```bash
./scripts/repo-health.sh
./scripts/new-agent-worktree.sh <dispatch-id> <agent> <slug>
```
