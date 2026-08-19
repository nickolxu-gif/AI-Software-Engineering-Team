# AI 软件工程团队操作与使用说明（MVP 0 + MVP 1 + MVP 2A + MVP 2B + MVP 2C）

## 1. 先给结论：你以后只需要在 Codex 里说话

你的日常主入口就是 **Codex**。你不需要 VS Code，不需要打开终端，也不需要记住或重复执行一大段命令。

你只要在这个项目的 Codex 任务中，用自然语言说明要做什么，例如：

> 进入软件工程团队，帮我实现登录功能。先做七问派活和风险判断，再开始执行。

要查看全局进展，只需说：

> 打开软件 AI 工程团队工作台。

Codex 会在前台请求中读取项目规则、检查 Git，并使用 MVP 0 已实现的稳定 CLI 或现有受控内部 API 组织任务。不能通过当前接口完成的全局查询或编排，Codex 必须明确说“当前不可用”，不能假装已有命令。

当前必须准确区分三件事：

- Codex 是唯一主入口和工程控制者；MVP 2A 工作台可以提交三类受控意图，MVP 2B 提供逐条门禁队列，MVP 2C 让 Codex 在正常工程请求开始时主动、安全地处理有限批次。
- `scripts/open-team-dashboard` 是 Codex 使用的一键启动器，你不需要自己运行。
- GitHub Remote 尚未配置；打开本地工作台不会配置 GitHub Remote，也不会 push。

MVP 0 稳定控制 CLI 包含 `init`、`start`、针对已知 `dispatch_id` 的 `status`、`transition`、`approvals` 列表和 `doctor inspect/repair`。MVP 2A 的本地工作台能查看全局首屏，并提交暂停、恢复或审批准备意图；MVP 2C 让 Codex 每次正常工程请求最多处理 10 条已提交意图。浏览器不能处理意图、修改 Git、调 Agent、merge、push、发布或消费审批 nonce。

现行依据：

- [项目交接 handoff.md](handoff.md)
- [七问派活协议](CODEX_AGENT_DISPATCH_PROTOCOL.md)
- [Agent 角色与模型矩阵](AGENT_ROLE_AND_MODEL_MATRIX.md)
- [软件工程生命周期](SOFTWARE_ENGINEERING_WORKFLOW.md)
- [Git 工作方式](GIT_WORKFLOW.md)
- [控制平面设计](docs/superpowers/specs/2026-08-08-ai-engineering-team-control-plane-design.md)

## 2. 首次使用或项目扫描后怎么说

普通模式下第一次进入项目，直接复制下面这句话：

> 进入软件工程团队。先读取 handoff 和现行协议，检查仓库健康与控制平面状态；如果本地状态库还不存在，请安全初始化。我要启动新任务，或查询已知任务 20260809-001；不要执行超出当前授权阶段的工作。

如果你要求完全不写入，则必须明确说：

> 严格只读，不要创建或修改任何文件、数据库、锁或 Git 状态。只盘点 Git 和现有文件；如果控制状态库不存在，请报告状态库不可用，不要初始化。

严格只读时不得初始化。普通状态请求可以在核对仓库后安全 `init`；“严格只读/不要任何写入”优先级更高，只能做 Git 与文件的只读盘点。

Codex 应自动完成：

1. 读取 `handoff.md`，以及列出的四份工程文档：七问派活协议、角色矩阵、软件工程工作流和 Git 工作流。
2. 从主仓库运行健康检查，核对 `main`、Worktree、分支和脏文件。
3. 检查 Git common directory 下的控制数据库；普通请求可安全初始化，严格只读请求不得初始化。
4. 任何新写入前检查 `PREPARED`。当前没有通用 reconcile CLI，只能由 Codex 使用已有测试覆盖的内部 `OperationCoordinator` API；无法完成时转为 `BLOCKED`，不得直接修改 SQLite。
5. 正常工程请求会自动调用一次 `process-pending-intents --limit 10`。单条 `REJECTED` 或 `BLOCKED` 会被说明后继续当前请求；若队列命令本身非零退出，Codex 停止后续写入，只报告控制面问题。
6. 已知 `dispatch_id` 时读取该任务的 Git 事实、Agent、Blocker、Review、Evidence 和待审批项。
7. 把已验证事实、尚未验证判断和建议下一步分开报告。

如果项目刚刚被 Codex 扫描，也可以说：

> 项目扫描完成后，进入软件工程团队模式。严格只读，不要初始化状态库；只盘点 Git 和文件并告诉我是否适合启动新任务。发现不一致先 BLOCKED，不要清理现场。

## 3. 可直接复制的自然语言指令

以下指令都由你在 Codex 中发送，不需要自己执行 CLI。

1. “进入软件工程团队，先检查项目健康和当前授权阶段。”
2. “进入软件工程团队，帮我实现这个需求：……；先完成七问派活，再开始。”
3. “把这个想法整理成可验收的派活单，先不要写代码。”
4. “开始任务 20260809-001，按 L1/L2/L3 判断风险并说明验收门。”
5. “继续任务 20260809-001；先核对 handoff、Git、锁、Blocker 和恢复点。”
6. “查看当前任务状态：任务 20260809-001；按事实、证据、风险和下一步汇报。”
7. “查看当前任务状态：任务 20260809-001；告诉我分支、Worktree、HEAD 和最近一次测试结果。”
8. “哪些事项等我批准？逐项说明动作、风险、目标 SHA 和不批准的影响。”
9. “暂停任务 20260809-001；先让写入 Agent 到达安全检查点。”
10. “继续任务 20260809-001；恢复前重新检查 Git、锁和原阻塞条件。”
11. “列出任务 20260809-001 的阻塞，说明负责人、解除条件、证据和下一步。”
12. “请处理任务 20260809-001 的这个 Blocker；如果需要扩大权限，先请求 Human 审批。”
13. “让独立 Reviewer 审查任务 20260809-001 的当前结果；当前没有登记 Review 的稳定 CLI，只能由 Codex 受控编排。”
14. “Claude 是否已经完成最终验收？如果没有，请明确写成待验收，不要降级。”
15. “把任务 20260809-001 的测试、Review、提交 SHA 和残余风险登记为证据；没有稳定 CLI 时只使用受控内部 API。”
16. “检查当前结果是否满足 Definition of Done；缺任何证据都不要标记完成。”
17. “完成这个任务，但整合前先给我看 Review 结论和待审批项。”
18. “为任务 20260809-001 准备 Mimo 盘点输入；当前没有 Mimo 稳定入口，请先说明可用的受控方式或标记后续。”
19. “Worktree 创建失败了。按 Minor 流程先做 Doctor inspect，不要删除任何目录或分支。”
20. “查看 Minor 检查结果；只有明确可修分类才 repair，否则保持 BLOCKED。”
21. “检查任务 20260809-001 是否关联未完成的 PREPARED 操作；仅使用 OperationCoordinator 内部 API，失败则 BLOCKED，不得改 SQLite。”
22. “给我任务 20260809-001 的今日摘要；当前不提供全局任务列表，不要推测其他任务。”
23. “给我任务 20260809-001 的本周盘点；跨任务汇总当前不可用，除非我提供其他已知任务 ID。”
24. “暂停任务 20260809-001；不要 merge、push、删除 Worktree 或配置 GitHub，等我下一步指令。”

## 4. Codex 前台自动执行的受控闭环

### 4.1 稳定 CLI 与 Codex 内部受控编排

MVP 0 稳定 CLI 只有以下能力：

| 稳定入口 | 当前能力 |
|---|---|
| `init` | 初始化本地控制状态；严格只读时禁用 |
| `start` | 创建一个指定 ID 的写入任务和 Worktree |
| `status` | 查询一个已知 `dispatch_id` |
| `transition` | 对一个已知任务执行合法状态转换 |
| `approvals` | 列出审批；不负责 approval create/consume |
| `doctor inspect/repair` | 检查 Minor；仅明确可修分类允许 repair |

以下能力当前**没有稳定 CLI**：全局 list tasks、全局 Blocker 或 daily summary、批量暂停、Agent/Blocker/Review/Evidence 登记、approval create/consume、通用 `PREPARED` reconcile，以及 Mimo 入口。

这些动作只有在已有测试覆盖的 Python 内部 API 可用、身份和 SHA 已校验、且 Codex 能保持控制锁与事务边界时，才可作为“Codex 内部受控编排”执行。例如通用 operation 恢复必须调用 `OperationCoordinator.reconcile_one/reconcile_all` 并提供匹配 verifier。不得直接修改 SQLite，也不得把内部方法描述成用户可依赖的稳定 CLI。内部 API 不可用、参数不足或恢复事实不唯一时，任务转为 `BLOCKED`。

MVP 2B 工作台可以显示受限首屏任务列表与“待处理意图”计数。任务详情中的“提交给 Codex”只会创建 `PENDING` 意图；状态显示“已提交给 Codex，尚未执行”并不代表任务已经暂停、恢复或获批。MVP 2C 中，Codex 在每次正常工程请求的前台启动循环中按稳定顺序处理至多 10 条意图，并为每一条重新核验实际 HEAD、状态、待审批项和已准备操作；结果才会写入生命周期事件。这不是后台 daemon 或定时器。底层控制 CLI 本身仍不提供全局任务列表。

### 4.1.1 工作台按钮怎么用

1. 在 Codex 中说“打开软件 AI 工程团队工作台”；Codex 启动本机页面。
2. 在“任务”中选择目标任务，查看当前 HEAD、阻塞、审查和既有意图。
3. 点击“申请暂停”“申请恢复”或“请求审批准备”。审批准备会要求你说明事项。
4. 页面出现“已提交给 Codex，尚未执行”后，Codex 可在正常主动工作循环中处理有限批次；你也可以说“处理本项目至多 10 条待处理意图，并报告逐条重新核验结果”。

三个按钮不是直接命令：暂停仍需要安全检查点，恢复会被待审批或 HEAD 漂移拦截，审批准备不会直接授权、创建 nonce 或执行审批。批处理遇到一条被拒绝或阻塞的意图会记录该条结果并继续下一条；未预期系统错误会停止批次、保留已完成审计事实。页面重启后 token 自动失效；如提交失败，刷新页面并回到 Codex 检查即可。

### 4.2 七问派活与风险判断

Codex 在开始前形成七问 Dispatch Record：

1. 目标、非目标、交付物、允许范围、依赖和完成标准是什么？
2. 风险和验收等级是 L1、L2 还是 L3？哪些动作需要 Human 授权？
3. 谁执行、谁独立 Review，为什么选择这些 Agent/模型，失败时如何等待或升级？
4. 最小上下文、Branch、Worktree、基础 SHA、Owner 和隔离边界是什么？
5. Agent 如何汇报进度、Codex 如何维护状态、冲突和方向错误如何纠偏？
6. 谁做最终验收；Claude 不可用时等待、升级还是申请降级？
7. 完成后如何由 Mimo 盘点、Codex 审阅并形成受控知识候选？

风险与验收一般按以下口径处理：

| 等级 | 常见范围 | 最低质量门 |
|---|---|---|
| L1 | 文档、小修、小范围低风险改动 | Codex 自检、适用测试、证据 |
| L2 | 模块、API、数据结构、中等重构 | 独立 Reviewer、适用测试、Codex 复核 |
| L3 | 架构、安全、权限、生产数据、大迁移或发布 | Claude Code 最终独立验收；缺席时不得伪装成已验收 |

### 4.3 Worktree、Agent 和 Review

一个写入任务对应：一个短分支、一个独立 Worktree、一个写入 Agent。Codex 独占 `main`、合并、冲突处理、Worktree 生命周期和交接更新；Agent 只能在自己的 Worktree 内改动和提交。

执行完成后，Codex检查改动范围、提交 SHA、测试结果和证据，再交给独立 Reviewer。Reviewer 输出 `ACCEPT / MODIFY / BLOCK / ESCALATE`：

- `ACCEPT`：仍需 Codex 核对门禁，不代表自动合并。
- `MODIFY`：回到同一 Worktree 修改，并重新 Review。
- `BLOCK`：保留现场和证据，停止整合。
- `ESCALATE`：提交 Human 或更高等级 Reviewer 决策。

### 4.4 验收、证据和知识闭环

“完成”必须有：实际文件路径、提交 SHA、测试命令与结果、Review 结论、必要审批、残余风险和整合后验证。进度百分比不能代替这些证据。

长期目标是只保留蒸馏后的报告与索引。Mimo 目前没有稳定入口；只有 Codex 能通过当前会话中实际可用的受控 Agent 路由完成盘点时才执行，否则标记为后续事项，不能声称已经自动完成。

## 5. 你会看到哪些状态和字段

### 5.1 状态报告的核心字段

| 字段 | 你应如何理解 |
|---|---|
| `task.dispatch_id` | 任务唯一编号，用于查询、暂停、继续和审批 |
| `task.title` / `task.objective` | 标题与可验收目标 |
| `task.risk_level` | L1/L2/L3 风险等级 |
| `task.state` | 底层生命周期状态 |
| `effective_state` | 状态展示 overlay；有有效待审批时显示 `NEEDS_HUMAN_APPROVAL` |
| `task.owner` | 工程控制 Owner，固定为 Codex |
| `task.task_base_sha` | 任务开始时的基线提交 |
| `task.current_head_sha` | 控制库记录的任务 HEAD |
| `actual_head_sha` | 状态查询时从 Git 观察到的实际 HEAD |
| `head_drift` | `task.current_head_sha` 与 `actual_head_sha` 是否漂移 |
| `task.branch` / `task.worktree_path` | 隔离分支和工作目录 |
| `agents[]` | 执行者、角色、状态、进度和最后汇报时间 |
| `blockers[].resolution_condition`（简称 `blocker.resolution_condition`） | Blocker 的正式解除条件 |
| `reviews[].disposition`（简称 `review.disposition`） | `ACCEPT / MODIFY / BLOCK / ESCALATE` |
| `reviews[].report_sha256`（简称 `review.report_sha256`） | Review 报告内容哈希 |
| `reviews[].stale` / `reviews[].effective`（简称 `review.stale` / `review.effective`） | Review 是否漂移，以及当前是否有效 |
| `evidence[].path` / `evidence[].sha256`（简称 `evidence.path` / `evidence.sha256`） | Evidence 受控路径及内容哈希 |
| `evidence[].source_sha` / `evidence[].stale`（简称 `evidence.source_sha` / `evidence.stale`） | Evidence 绑定提交及当前漂移状态 |
| `pending_approvals` | 当前仍有效且未消费的审批 |

### 5.2 生命周期解释

| 状态 | 含义 |
|---|---|
| `PLANNED` | 已登记，七问和计划尚在补齐 |
| `NEEDS_CLARIFICATION` | 目标、范围或输入不清，需要澄清 |
| `DISPATCHED` | Agent、上下文和隔离方式已经明确 |
| `IN_PROGRESS` | 正在执行 |
| `PAUSE_REQUESTED` | 已停止派发新动作，等待写入者到达安全检查点 |
| `PAUSED` | 已安全暂停，只允许只读检查、停止确认、审批记录和证据归档 |
| `BLOCKED` | 依赖、权限、测试、资源或安全状态不满足 |
| `NEEDS_DIRECTION` | 发现需求或技术方向风险，需要 Codex 纠偏 |
| `REVIEWING` | 执行完成，正在等待独立审查 |
| `ACCEPTED` | 质量门通过，等待 Codex 安全整合 |
| `INTEGRATED` | 已整合，仍需整合后验证 |
| `RELEASED` | 已按授权交付或发布 |
| `CLOSED` | 已完成盘点、证据归档和知识回流 |
| `UNKNOWN` | 事实不足，禁止猜测性推进 |

暂停是两阶段动作：先进入 `PAUSE_REQUESTED`，所有写入者确认安全后才是 `PAUSED`。继续任务也不是简单“开机”，Codex 必须重查 Git、锁、Blocker 和保存的恢复状态。

`NEEDS_HUMAN_APPROVAL` 不是普通 lifecycle transition。当前实现把它作为 `effective_state` overlay：只要存在有效待审批项，`effective_state` 显示 `NEEDS_HUMAN_APPROVAL`，而底层 `task.state` 保留原生命周期状态。审批消费、过期或失效后，展示恢复为 `task.state`。

## 6. 哪些事情需要你审批，应该怎么回复

以下情况通常必须暂停并请求 Human：

- 删除、覆盖关键原件、批量迁移或不可逆操作；
- 生产系统、真实业务操作、外部发送或首次发布；
- 凭据、敏感数据、新数据源或权限扩大；
- 目标或范围发生战略级变化；
- 配置 GitHub Remote、创建远端仓库或首次 push；
- 从当前阶段进入 MVP 2/3；
- Claude Code 无法完成要求的验收，拟启用降级 Reviewer。

正规的审批请求应告诉你：任务 ID、具体动作、原因、目标 SHA、参数摘要/请求哈希、风险、有效期和替代方案。不要只回复“可以”，应指向明确动作。

安全回复示例：

> yes，仅批准任务 20260809-001 在当前目标 SHA 上使用 CodeBuddy GLM 5.2 做本次应急审阅；不得视为 Claude 已验收，不批准 merge 或 push。

Claude 降级必须基于本次会话中可见的 Claude 限额/配额失败证据，并由你在本次明确回复 `yes`。旧会话的同意、默认授权或“以前用过”都不能复用。没有这次明确授权，Codex只能等待 Claude，或保持 `REVIEWING/BLOCKED`。

拒绝时可回复：

> no，不批准降级。保持 REVIEWING，等待 Claude 恢复；不要整合或发布。

## 7. Minor 的正确含义与 Doctor 规则

这里的 `Minor` 不是 MinIO，也不是对象存储风险。它专指 `git worktree add` 失败后，可能留下目录、分支或 Worktree metadata 的残留状态。

正确顺序永远是：**Doctor inspect → 根据分类判断 → 仅可修分类 repair**。

常见分类：

| 分类 | 含义与动作 |
|---|---|
| `HEALTHY` | Worktree、分支、HEAD、登记和清洁状态一致；无需修复 |
| `NO_RESIDUE` | 没有目录、分支或 metadata 残留；可按正常创建流程继续 |
| `REPAIRABLE_BRANCH_ONLY` | 仅有仍停留在任务基线的预期分支；Doctor 可安全重建 Worktree |
| `BLOCKED_PATH_RESIDUE` | 目标路径有未知内容；保留现场，禁止删除 |
| `BLOCKED_BRANCH_ADVANCED` | 分支已有超出基线的提交；可能是有效成果，禁止重建覆盖 |
| `BLOCKED_DIRTY_WORKTREE` | 有未提交修改；禁止清理 |
| `BLOCKED_STALE_METADATA` | Git metadata 与路径不一致，不能自动证明安全 |
| `BLOCKED_REGISTRATION_MISMATCH` | 分支被其他 Worktree 占用或登记不一致 |
| 其他 `BLOCKED_*` | 身份、仓库、HEAD、符号链接或未知事实不一致；交给 Codex 判断 |

Doctor 不会自动删除未知目录，不会丢弃额外提交，不会执行 `git reset --hard`、`git clean -xdf`、force 删除或无范围清理。只要安全条件不能被事实证明，结果就是 `BLOCKED`。

你可以说：

> 按 Minor 流程检查实际 Git 状态。只允许 Doctor inspect；如果不是 REPAIRABLE_BRANCH_ONLY 或明确的安全状态，不要 repair，更不要删除。

## 8. Git 怎么工作，GitHub 以后怎么启用

### 8.1 当前本地 Git 工作方式

- `main` 是唯一稳定主线，不设长期 `develop`。
- 每个写入任务使用短分支和独立 Worktree。
- Codex 创建、核对和整合；执行 Agent 只在所属 Worktree 写入。
- Reviewer 独立只读审查，不能直接改 `main`。
- `MODIFY` 回到原 Worktree；`BLOCK` 保留现场。
- 只有测试、Review、验收、审批和主线健康门都满足后，Codex 才能整合。
- 没有实际合并 SHA 和主线验证，就不能说“已经合并”。

### 8.2 GitHub 当前状态

GitHub Remote 尚未配置，本地控制平面和 MVP 1 工作台都不依赖 GitHub。启动工作台不会配置 GitHub Remote；当前不能声称已经创建远端、设置 `origin`、首次 push、分支保护或 CI。

未来启用时，应作为独立高影响任务，由你明确确认：

1. GitHub 个人账户或组织；
2. 仓库名称；
3. Private 或 Public（默认建议 Private）；
4. 认证方式；
5. 是否允许创建远端仓库和首次 push；
6. 主分支保护、PR Review、必要检查和恢复方案。

你届时可以说：

> 我确认启动 GitHub Remote 独立任务。先给我配置方案和风险，不要创建仓库或 push，等我确认账户、仓库名、可见性和认证方式。

## 9. MVP 1 本地只读工作台

### 9.1 怎么打开和关闭

你不需要 VS Code 或 CLI。在 Codex 中说：

> 打开软件 AI 工程团队工作台。

Codex 先从主仓库检查健康，再调用 `scripts/open-team-dashboard`。成功后会打开形如 `http://127.0.0.1:端口` 的本机页面并把地址告诉你。固定端口上若已经是同一仓库的健康工作台，会复用它；若端口被其他程序占用，会停止并报告 `PORT_IN_USE`，不会换身份冒用。

关闭时说“关闭软件 AI 工程团队工作台”。Codex只终止本次确认的本地服务，不删除数据库、任务、分支或 Worktree。

### 9.2 五个页面怎么看

| 页面 | 显示内容 | 你应该怎么做 |
|---|---|---|
| 总览 | 项目健康、活跃/阻塞/审批/过期计数、异常优先队列 | 先看红色或需要关注项 |
| 任务 | 首屏最多 100 个任务，可搜索和按风险过滤；点击读取详情和生命周期时间线 | 需要动作时记住任务 ID |
| Agents | 活跃任务的 Agent、角色、状态和最近进度 | 长时间无汇报时回到 Codex 检查 |
| 审批 | 审批索引和到期时间 | 页面不批准；请回到 Codex 处理 |
| 证据 | 所选任务的证据路径、来源 SHA 和是否过期 | 页面不打开或修改证据原文 |

任务详情还显示 owner、executor、生命周期、Task Base SHA、记录 HEAD、实际 HEAD、分支、Worktree、Agent、Blocker、Review、审批数、证据数和事件时间线。数据不存在时明确写“当前控制库未记录”，不会臆造。

### 9.3 提交新工程需求

总览页的“提交新工程需求”只需要填写：标题、要达成的目标，以及可选背景。提交后它显示为“待处理需求”，并只进入本机 SQLite 收件箱；页面不会据此创建任务、分支、Worktree、Git 提交、合并、push、发布或审批。收件箱最多同时保留 100 条待处理需求；Codex 确认后的记录仍作为不可变审计历史保留，但不再占用待处理容量，因此不会因历史记录累积而堵塞新需求。

下一次在 Codex 中说“处理工作台里的待处理工程需求”或继续正常工程对话时，Codex 才会读取该收件箱，补齐七问、确定风险和执行方案；处理完成后由 Codex 显式确认，需求不再计入待处理数。背景内容仅供 Codex 本地处理，不会在工作台读回；不要把密码、Token、私钥或敏感原文填入该字段。

### 9.4 刷新和受控写入边界

- 页面每 15 秒自动刷新；刷新期间不会叠加新的全量刷新。
- 连续 45 秒没有成功刷新时显示过期提醒，并保留最后一次成功数据。
- 所有并行响应必须来自同一个 Git HEAD，否则显示 `SOURCE_HEAD_MISMATCH`，不混合新旧快照。
- 浏览器读取使用 GET/HEAD/OPTIONS；仅可 POST MVP 2A 的既有三类受限意图请求与 MVP 2D 的任务需求收件箱，不能处理意图、启动 Agent、创建任务、改状态、审批、修复、merge、push 或发布。
- 页面仅绑定 `127.0.0.1`，不监听局域网；不配置远端，不依赖云服务。
- 所有工程动作都显示“请回到 Codex 处理”。

### 9.5 故障排查

| 现象 | 含义 | 处理方式 |
|---|---|---|
| 页面打不开 | 服务未启动、已停止或地址属于旧进程 | 回到 Codex 说“重新检查并打开工作台” |
| `DATABASE_UNAVAILABLE` | 控制数据库不存在/不可读 | 只读启动器绝不会初始化缺失数据库；由 Codex单独判断是否允许初始化 |
| `SCHEMA_MIGRATION_REQUIRED` | 历史控制库缺少已知控制面表 | Codex先只读核对，再在普通可写请求中运行稳定 `init`；严格只读时不得恢复 |
| `SCHEMA_UNSUPPORTED` | 表不是预期 SQLite table，或必需字段不匹配 | 保持 `BLOCKED` 并保留现场；`init` 不是对此类未知不兼容的自动修复方式，禁止直接编辑 SQLite |
| `PORT_IN_USE` | 固定端口被其他程序或其他仓库占用 | Codex核对进程身份后处理，不自动杀进程 |
| 45 秒过期 | 最近刷新失败或服务停止 | 页面保留旧数据；回到 Codex 检查服务和 Git |
| HEAD 漂移 | 控制库记录与已登记 Worktree 的实际提交不一致 | 停止验收判断，由 Codex核对分支、Worktree 和 Review |
| `SOURCE_HEAD_MISMATCH` | 一次页面刷新跨越了不同 Git 提交 | 等下一次完整刷新；持续出现则回到 Codex |

MVP 1 只解决“看见状态”。MVP 2 才考虑从自然语言意图到受控动作建议或请求；浏览器不会因为未来规划而获得写权限。

## 10. 数据位置、隐私与 threat model

### 10.1 数据在哪里

- 代码与协议：当前本地 Git 仓库。
- 运行状态：`<git-common-dir>/team/runtime/team.db`。
- 单仓库写锁：`<git-common-dir>/team/runtime/control-plane.lock`。
- 可提交的蒸馏证据：`artifacts/dispatches/<dispatch-id>/`。
- 所有主 Worktree 和任务 Worktree 共享同一 Git common directory 状态库，但运行数据库不会进入 Git 提交。

SQLite 是本地运行状态索引，不是唯一不可替代事实源。Git 是代码事实源；长期状态还要靠派活记录、提交、测试和蒸馏证据重建。

### 10.2 隐私边界

当前没有自动秘密检测或自动脱敏。MVP 0 的 schema、路径和长度校验能限制部分结构化字段，但不是覆盖所有日志、文件和文本的秘密扫描器，不能把“政策禁止”误写成“技术已保证”。

- 不要把含 Token 的日志交给系统，也不要提交密码、Cookie、私钥、恢复码、完整凭据文件或敏感原文。
- Codex 在持久化前必须先人工检查或使用明确规则式最小化，只保留完成任务所需的字段、摘要、路径和哈希。
- 发现疑似秘密、凭据或无法判断的敏感内容时，Codex必须停止登记，请求用户提供脱敏材料；不得先保存再清理。
- Evidence 的目标格式是受控路径、摘要、哈希、时间和关联 SHA，但这不代表源文件内容已经自动脱敏。
- Agent 只应得到完成任务所需的最小上下文；本地优先，MVP 0 不要求云服务或 GitHub。

### 10.3 threat model（威胁模型）

控制锁、单写者、Worktree 隔离、路径校验和操作日志，主要防止**遵守项目协议的受控协作者**发生并发冲突、重复执行、状态漂移或误操作。

它不是同一 macOS 账户下对抗恶意进程的安全沙箱。拥有同等文件权限的恶意或不受控进程，仍可能绕过协作锁直接修改 Git、数据库或文件。发现外部漂移时，系统应 fail closed、转为 `BLOCKED` 并要求重新核验，而不是声称绝对安全。

## 11. 常见问题与故障排查

### Q1：以后每次都要输入一大段命令吗？

不用。你只需说“进入软件工程团队……”或其他自然语言指令。项目 Skill 告诉 Codex 自动读取规则和调用底层工具。

### Q2：Codex 说数据库不存在，我该怎么办？

普通请求可以说：“安全初始化控制平面，然后查看已知任务 20260809-001。”Codex 应先核对仓库，再安全初始化。若你已经说“严格只读/不要任何写入”，则不得 init，只能盘点 Git 和文件，并明确报告状态库不可用。

### Q2.1：出现 `SCHEMA_MIGRATION_REQUIRED` 或 `SCHEMA_UNSUPPORTED` 怎么办？

前者表示历史数据库缺少系统已知的表。Codex 会先只读核对，再在普通可写请求中使用稳定 `init` 补齐；不会直接编辑 SQLite。后者表示表结构本身不符合当前协议，不能假设 `init` 可以无损修复，必须保留现场并转为 `BLOCKED`。

### Q3：出现 `PREPARED` 是失败了吗？

不一定。它表示 Git 与 SQLite 跨系统操作已登记但还没完成确认。当前没有通用 `PREPARED` reconcile 稳定 CLI；Codex只能调用已有测试覆盖的内部 `OperationCoordinator.reconcile_one/reconcile_all` 和匹配 verifier。事实无法唯一确认、内部 API 不可用或执行失败时必须 `BLOCKED`；严禁直接修改 SQLite 或盲目重放 Git 动作。

### Q4：状态显示完成，但没有测试或 Review，算完成吗？

不算。请说：“按 Definition of Done 核验，给出路径、SHA、测试、Review、审批和残余风险。”没有证据的完成声明无效。

### Q5：Agent 很久没汇报怎么办？

让 Codex读取 Agent 最后汇报、Git HEAD 和 Worktree 事实。若无法确定执行状态，停止新派活并标记 `BLOCKED/UNKNOWN`，不要盲目重复任务。

### Q6：Review 意见冲突怎么办？

Codex 要求各 Reviewer 提交假设、证据、验证方法和风险，区分事实冲突与价值取舍。最终由 Codex 作工程判断；战略取舍升级给你，不以投票决定。

### Q7：为什么暂停没有立刻变成 `PAUSED`？

因为需要先让写入者到安全检查点。`PAUSE_REQUESTED` 是正常中间状态；只有全部确认后才能 `PAUSED`。

### Q8：Minor 能不能直接删掉目录重来？

不能。未知目录、脏文件或额外提交可能是有效成果。必须 Doctor inspect；不能证明安全就保持 `BLOCKED`，不会自动删除。

### Q9：Claude 没额度时能自动换模型吗？

不能。Codex先提供本次限额证据和降级方案；你本次明确回复 `yes` 后，才可启用授权的应急 Reviewer，而且不能冒充 Claude 已验收。

### Q10：为什么还看不到前端仪表盘？

先回到 Codex说“打开软件 AI 工程团队工作台”。如果仍打不开，让 Codex检查启动器返回的结构化错误、数据库状态和端口身份；不要自己反复尝试不同地址。

### Q11：为什么不能直接列出全部任务或生成全局日报？

底层 MVP 0 CLI 仍不提供全局任务列表或批量动作；MVP 1 工作台只显示受限的只读首屏。要生成完整日报或对任务采取动作，请回到 Codex，由它按当前接口和权限边界处理。

## 12. 如何验证 Codex 的汇报是否可信

你不需要自己运行命令，但可以要求 Codex提供下列证据：

1. 当前主线、任务分支、Worktree 路径和实际 HEAD。
2. 工作区是否干净，是否存在范围外改动。
3. 已知任务 ID 的状态、最近事件、Agent、Blocker 和待审批项。
4. 测试命令、测试数量、通过/失败结果和未覆盖区域。
5. Review 报告路径、Reviewer 身份、结论和绑定 SHA。
6. Evidence 路径、SHA-256、来源 SHA 和是否发生漂移。
7. 如果声称已整合：合并 SHA、主线全量验证和交接更新。

推荐直接说：

> 不要只给结论。请给我当前状态的 Git SHA、文件路径、测试数量、Review disposition、待审批项和残余风险，并区分已验证事实与建议。

Task 11 已做人工前向验证：两份独立审阅用实际自然语言场景检查手册并返回 `MODIFY`，本版按其可复现意见修正。`tests/test_skill_contract.py` 是文字契约自动化测试，不等于 Agent 行为已经被自动化端到端验证。

## 13. CLI 附录（仅供 Codex 和故障复现；本人不需执行）

以下是 MVP 0 稳定 CLI 的完整范围。你本人不需要执行，也不需要记忆；它们用于 Codex 自动操作、测试或故障复现。

```bash
# 仓库健康
scripts/repo-health.sh

# MVP 1 本地只读工作台；Human 只需在 Codex 中说“打开工作台”
scripts/open-team-dashboard

# 安全初始化本地控制平面
scripts/team-control init

# 启动任务
scripts/team-control start --dispatch-id ID --title TITLE --objective OBJECTIVE --risk L1 --agent AGENT --slug SLUG

# 查询一个已知任务；approvals 只提供列表
scripts/team-control status --dispatch-id ID
scripts/team-control approvals --dispatch-id ID

# 受控状态转换
scripts/team-control transition --dispatch-id ID --to STATE --reason REASON

# Minor 只读检查；repair 只能在 inspect 明确可修后使用
scripts/worktree-doctor inspect --dispatch-id ID --agent AGENT --slug SLUG --base-sha SHA
scripts/worktree-doctor repair --dispatch-id ID --agent AGENT --slug SLUG --base-sha SHA
```

### 13.1 MVP 3A 中央项目登记册（仅 Codex 操作）

项目登记册是**手工、显式的本地 allowlist**，不是扫描器，也不在浏览器工作台提供登记、退休、删除或修复入口。你只需在 Codex 中明确要求登记或退休；Codex 先完成七问、风险与路径核验，再在中央控制仓库的仓库根目录执行下列命令（使 `team_control` 可被 `python3 -m` 导入）。`--repo PATH` 始终是中央控制仓库，绝不是被登记项目的路径。

```bash
python3 -m team_control.cli --repo PATH projects register --display-name NAME --path ABSOLUTE_PATH
python3 -m team_control.cli --repo PATH projects retire --project-id UUID
python3 -m team_control.cli --repo PATH projects list
```

登记最多保留 20 个 `ACTIVE` 项目。登记会只读核验目标是本地 Git 仓库并记录其身份；不会扫描目录、读取 remote、递归枚举项目，也**不会初始化、迁移、修复或写入**目标项目的 `team/runtime` 控制平面。`list` 只返回活跃项目的安全摘要，绝不返回目标路径或身份元数据。

`retire` 不删除中央记录：它把项目改为 `RETIRED`，并保留不可变的中央审计事件。已退休项目不会出现在 `list` 中；若需要再次纳入，必须作为新的明确登记动作重新经 Codex 核验。

`projects` 命令依赖显式的中央 `--repo PATH`，不是已绑定仓库的 wrapper。上面的既有 `scripts/` wrapper 才绑定所属仓库，输出机器可读 JSON。它们不提供全局 list tasks/blockers、批量暂停、Agent/Blocker/Review/Evidence 登记、approval create/consume、通用 `PREPARED` reconcile 或 Mimo 入口。

Projects 卡片的登记状态只有 `ACTIVE` / `RETIRED`；控制状态才可能是 `HEALTHY`、`UNINITIALIZED`、`UNAVAILABLE`、`UNSUPPORTED` 或 `IDENTITY_MISMATCH`。后四种均是交给 Codex 调查的状态信号，浏览器不得执行修复、初始化或 SQLite 修改。

Codex仍必须先读 `handoff.md` 并检查健康。需要处理 `PREPARED` 时只能使用已有测试覆盖的内部 `OperationCoordinator` API；不得直接修改 SQLite。内部 API 不是用户稳定接口，命令或方法存在也不等于动作已获授权。

## 14. 术语表

| 术语 | 简明解释 |
|---|---|
| Human | 你；决定战略、授权边界和高风险动作 |
| Hermes | 个人 AI 幕僚长；帮助澄清意图和长期协调，不替代 Codex 工程权威 |
| Codex | 唯一工程控制者/临时 CTO，负责任务、状态、Git、冲突和整合 |
| Agent | 在明确范围内执行任务的专业单元 |
| Reviewer | 与执行者独立的只读审查者 |
| Claude Code | L3 默认最终独立质量门 |
| Mimo | 完成后盘点与总结者，输出由 Codex 审阅 |
| Dispatch Record | 七问派活形成的结构化任务单 |
| Worktree | 同一 Git 仓库的隔离工作目录 |
| Minor | Worktree 创建失败后的目录、分支或 metadata 残留风险 |
| Doctor | 先 inspect、后按严格分类决定是否 repair 的工具 |
| Evidence | 绑定路径、哈希和 SHA 的可核验证据 |
| PREPARED | 跨 Git/SQLite 操作已登记、等待事实核对的阶段 |
| fail closed | 无法证明安全时停止写入并 BLOCKED，而不是猜测继续 |

## 15. 每日与每周推荐用法

### 每日推荐用法

早上：

> 进入软件工程团队。严格只读，查看任务 20260809-001 的状态、Review、阻塞和待批准事项；状态库不存在时不要初始化。

准备开始新工作：

> 为这个需求建立七问派活单和风险等级，告诉我哪些部分可以自动推进、哪些需要我确认。

结束工作：

> 暂停任务 20260809-001 到安全检查点，汇总该任务今天的提交、测试、Review、Blocker 和明天恢复条件。

### 每周推荐用法

每周盘点：

> 盘点任务 20260809-001 本周的交付、返工、阻塞、Review 发现、测试缺口和残余风险。Mimo 当前没有稳定入口；可用时由 Codex 受控调度，否则明确标记后续。

路线图检查：

> 检查 MVP 1 当前验收证据和未完成门禁。不要启动 MVP 2、GitHub 或任何外部动作，只给我进入下一阶段所需的确认清单。

这套用法的核心是：你负责方向和明确授权，Codex负责工程控制与证据闭环，Agent负责专业执行，Reviewer负责独立挑战；没有证据就不宣称完成，无法证明安全就停止推进。
