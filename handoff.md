# 项目交接状态

> 日期：2026-08-09
> 维护者：仅 Codex 主控。其他 Agent 和 Reviewer 只读，可提交修改建议。
> 生效条件：本文件随本次主线 handoff 提交生效。

## 当前状态

**Git 治理、MVP 0 控制平面、MVP 1 本地工作台、MVP 2A 受控意图入口、MVP 2B Codex 意图队列与 MVP 2C 前台主动循环均已整合到 `main`。**

- `main` 是唯一稳定主线；Git bootstrap 历史基线 SHA 为 `cd459565b8bb24156f92e400a11769d254eccda9`。
- MVP 0 任务分支已 fast-forward 整合到 `main`；MVP 0 集成 SHA 为 `f4b60ab4a4f3112912641fd8b56667b27d6fb819`。
- MVP 1 任务分支已通过 no-ff merge 整合到 `main`；MVP 1 集成 SHA 为 `a0d11656b7dd0fbdc2cd6114eed70adc06ebd1b2`。
- MVP 0 任务 Worktree 已清理；MVP 1 任务 Worktree 只在本 handoff 提交、状态闭环和最终复验完成前保留。
- 当前没有配置 remote。
- `scripts/new-agent-worktree.sh` 与 `scripts/repo-health.sh` 已完成；可复核命令事实见 `GIT_BOOTSTRAP_VERIFICATION.md`。
- Git bootstrap 验证工件：`GIT_BOOTSTRAP_VERIFICATION.md`、`GIT_BOOTSTRAP_REVIEW_LOG.md`。
- `REVIEW_ITERATION_2026-08-08.md` 是状态为 `MODIFY` 的审阅证据，不是现行规范。

## AI 软件工程团队控制平面

- Human 已确认 MVP 0 → MVP 1 → MVP 2 → MVP 3 的渐进实施顺序。
- 控制平面设计：`docs/superpowers/specs/2026-08-08-ai-engineering-team-control-plane-design.md`。
- MVP 0 实施计划：`docs/superpowers/plans/2026-08-08-mvp0-control-plane.md`。
- 这里的 `Minor` 指 `git worktree add` 失败后可能残留目录、分支或 Worktree metadata；不是对象存储。Codex 必须先检查实际 Git 状态，再决定安全重建或转为 `BLOCKED`，不得自动强删未知数据。
- 当前授权阶段：MVP 0、MVP 1、MVP 2A、MVP 2B、MVP 2C 已整合；MVP 3 和 GitHub Remote 尚未进入实施。

### MVP 0 Control Plane

**状态：ACCEPTED and integrated**

- 集成 SHA：`f4b60ab4a4f3112912641fd8b56667b27d6fb819`。
- 最终独立验收：Claude Code 2.1.224，结论 `ACCEPT`；CodeBuddy / GLM 5.2 降级未使用。
- 主线验证：`python3 -m unittest discover -s tests -v`，`Ran 207 tests ... OK`。
- 运行时数据库：`/Users/qinxu/Documents/vibe coding/AI-Software-Engineering-Team/.git/team/runtime/team.db`。该文件位于 Git common directory，不提交到仓库。
- 项目 Skill：`.agents/skills/ai-software-engineering-team/SKILL.md`。
- 用户操作手册：`USER_OPERATING_GUIDE.md`。
- 内部健康命令：`./scripts/repo-health.sh`。
- 已知任务状态示例：`./scripts/team-control status --dispatch-id 20260808-009`。仅在该任务已经登记时有效；MVP 0 没有稳定的全局任务列表接口。
- Minor 规则：先运行 Doctor `inspect`；只有明确的 `REPAIRABLE_BRANCH_ONLY` 才允许 `repair`。未知目录、脏文件、额外提交、注册冲突、符号链接或无法证明安全的 metadata 一律转为 `BLOCKED`，不得自动删除。
- 验收工件：`artifacts/dispatches/20260808-mvp0-acceptance/verification.md`。
- GitHub Remote、云服务或生产发布仍未配置。

### MVP 1 Local Read-only Dashboard

**状态：ACCEPTED and integrated**

- 任务：`20260809-001`；实现候选 SHA 为 `f785ea1f40820481c19d1f8d5f511485576fc63d`，主线集成 SHA 为 `a0d11656b7dd0fbdc2cd6114eed70adc06ebd1b2`。
- 最终独立验收：Claude Code / Sonnet，结论 `ACCEPT`；未使用 CodeBuddy 或其他降级 Reviewer。
- 主线验证：默认 Python 与 Python 3.14 均运行 `python -m unittest discover -s tests -q`，各 273 项，全部 `OK`。
- 启动入口：`./scripts/open-team-dashboard`；默认只绑定 `127.0.0.1`，浏览器工作台严格只读。
- 五类视图：总览、任务、Agents、审批、证据；七个 API 均为只读 GET/HEAD/OPTIONS 契约。
- 使用手册：`USER_OPERATING_GUIDE.md`；项目 Skill：`.agents/skills/ai-software-engineering-team/SKILL.md`。
- 验收工件：`artifacts/dispatches/20260809-001/`，包含派活单、设计审查、实现审查、验证记录、MiMo 盘点和 Codex 盘点审阅。
- 已知边界：任务 `current_head_sha` 尚无受控推进入口，因此真实任务可能显示 `HEAD_DRIFT`；该状态不会被伪装成有效验收。并发 writer 回归、真实浏览器自动化和 HTTP 线程限制属于后续改进。
- 收尾修复任务 `20260810-001` 已整合于 `8e91a6e98e8b706980904776153d8e6ee63bda3c`：`CLOSED` 任务完成 Worktree 生命周期后不再因 Worktree 缺失或漂移把项目升级为 `ATTENTION`；任务详情仍可保留历史 `head_drift` 事实。活跃任务继续失败关闭。
- MVP 2 前不得从浏览器直接执行状态变更、调 Agent、审批、merge、push 或发布；所有工程动作继续回到 Codex 控制平面。

### MVP 2A Bounded Task Intent Adapter

**状态：ACCEPTED and integrated**

- 任务：`20260812-004`；本地 `main` 整合 SHA：`9a5eeed403ca2549ee767e35abed7d9b362283b2`。
- 工作台可提交且仅可提交 `PAUSE_REQUEST`、`RESUME_REQUEST`、`APPROVAL_REQUEST` 三类受控意图；提交只形成 `PENDING` inbox 记录，处理仍由 Codex 显式执行。
- 每次处理在控制锁中重新核验任务、实际 Git HEAD、控制库 HEAD、状态、待审批项和已准备操作。浏览器不能执行 Git、merge、push、发布、审批 nonce 消费或自动处理。
- 写入端点只允许 loopback Host、同源 Origin、进程内 token、精确 `application/json` 和最多 8 KiB 正文；HTTP、CLI 和读模型都只返回显式白名单字段。
- 独立验收：Claude Code / Sonnet V4.10.3 对核心终态拦截 delta 与 HTTP/CLI 白名单 delta 均为 `PASS`；安全记录见 `artifacts/dispatches/20260812-004/verification.md`。未使用 fallback Reviewer。
- 整合后验证：默认 Python、Python 3.14 全量测试、`git diff --check` 和 `./scripts/repo-health.sh` 均通过。
- MiMo 当前没有可用独立执行入口；未伪造盘点结论。可复用验收器改进已在 `claude-emergency-verifier` V4.10.3 保存：完整传入 immutable packet，且强制精确 scope acknowledgement。

### MVP 2B Codex Intent Queue

**状态：ACCEPTED and integrated**

- 任务：`20260812-005`；本地 `main` 整合 SHA：`9882f3538de640001185a8fcbce4bd5ed807af0c`。
- 新增 Codex 专用 `process-pending-intents --limit 1..25`：按 `created_at, intent_id` 选择有限 PENDING snapshot，逐条复用 MVP 2A 的完整核验与审计链路。
- `REJECTED` 或 `BLOCKED` 的单条意图不会中断后续处理；未预期系统错误会停止批次，保留已完成条目的事实。浏览器仍无队列处理、Git、merge、push、发布或审批消费能力。
- 工作台总览新增“待处理意图”只读计数；所有公开意图结果继续使用显式白名单字段。
- 独立验收：Claude Code / Sonnet V4.10.3 对修复后的浏览器边界 delta 给出 `PASS`；安全记录见 `artifacts/dispatches/20260812-005/verification.md`。未使用 fallback Reviewer。
- 整合后验证：默认 Python、Python 3.14 全量测试、`git diff --check` 与 `./scripts/repo-health.sh` 均通过。
- 下一阶段建议为 MVP 2C：由 Codex 会话/调度层把“检查并处理至多 N 条队列”接入固定工作循环；仍不引入后台 daemon、远程访问或浏览器执行权。

### MVP 2C Codex Foreground Intent Loop

**状态：ACCEPTED and integrated**

- 任务：`20260812-006`；本地 `main` 整合 SHA：`b081f43d66d18aa090ccefbe1a491d7de2ecee61`。
- 项目 Skill 规定：每次普通可写 Codex 工程请求在健康通过且控制数据库存在（必要时安全 `init` 成功）后，最多一次调用 `scripts/team-control process-pending-intents --limit 10`。
- 严格只读与 Dashboard open/view 不初始化、不处理队列。初始化失败、控制库不可用或队列命令非零时停止后续写入，只提供只读说明；单条 `REJECTED`/`BLOCKED` 仍继续当前请求。
- 这是前台固定循环，不是后台 daemon、timer、webhook 或网络监听；没有新增浏览器处理、Git/merge/push/发布、审批 nonce 消费、远程或权限扩大。
- 独立验收：Claude Code / Sonnet V4.10.3 首次完整候选为 `PASS_WITH_WARNINGS`，三项警告修复后的 delta 为 `PASS`。安全记录见 `artifacts/dispatches/20260812-006/verification.md`；未使用 fallback Reviewer。
- 整合前验证：默认 Python、Python 3.14 全量测试与 `git diff --check` 均通过；`repo-health` 必须从 `main` 根 Worktree 运行。
- 历史运行库兼容性事件：在 `20260812-007` 前，若历史控制数据库缺少 MVP 2 的 `intents` 表，意图 CLI 会 fail-closed 为 `INTERNAL_ERROR`。该任务已改为显式 schema 预检；恢复仍只能使用稳定 `scripts/team-control init`，禁止直接修改 SQLite。任务 `20260812-006` 的实际恢复后队列为 `attempted: 0`。
- 下一阶段为 MVP 3 或 GitHub Remote，均仍需 Human 明确的新任务授权；不得由 MVP 2C 自动进入。

### 控制库 Schema 兼容性预检

**状态：ACCEPTED — pending local main integration**

- 任务：`20260812-007`；候选 HEAD：`703db23`；基线：`b1703b7`。
- 任何非 `init` 控制 CLI 先用只读连接校验完整控制面表、SQLite object type 和必需列。缺少已知表时返回 `SCHEMA_MIGRATION_REQUIRED`；同名 view 或缺列时返回 `SCHEMA_UNSUPPORTED`，不再退化为 `INTERNAL_ERROR`。
- 工作台 `intents` 表缺失时 `/api/health` 返回 503 + `SCHEMA_MIGRATION_REQUIRED`；view 或字段不兼容返回 `SCHEMA_UNSUPPORTED`。
- 恢复仍必须由 Codex 显式调用已有幂等 `scripts/team-control init`；严格只读请求不能恢复，且不得直接编辑 SQLite。`SCHEMA_UNSUPPORTED` 保持 `BLOCKED`，不假设 init 能无损修复。
- Claude Code / Sonnet 独立验收为 `PASS`，安全记录：`artifacts/dispatches/20260812-007/verification.md`。未使用 fallback Reviewer。

可使用以下命令复核最终 Worktree 和分支状态：

```bash
git worktree list
git branch --list
```

MVP 1 状态闭环和本 handoff 提交完成前，前者可同时显示已整合且干净的 MVP 1 任务 Worktree；清理后应仅显示 `main` 根 Worktree。分支清理必须由 Codex 在验证整合事实后执行。

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
- 大型联调只使用类似 `integration/20260809-001` 的临时分支，不得形成第二条长期主线。

## 下一次使用与后续实施

```bash
./scripts/repo-health.sh
./scripts/open-team-dashboard
```

用户无需日常手工执行整段 CLI；可在 Codex 中要求“打开 AI 软件工程团队工作台”，由 Codex 调用启动入口。MVP 2、GitHub Remote 或任何写操作仍需独立任务与相应门禁。`scripts/new-agent-worktree.sh` 仅保留为初始化前兼容入口并会 fail closed；不得用它或手工 `git worktree add` 绕过控制锁。
