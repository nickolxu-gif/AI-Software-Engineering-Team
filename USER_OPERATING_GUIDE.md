# AI 软件工程团队操作与使用说明（MVP 0）

## 1. 先给结论：你以后只需要在 Codex 里说话

你的日常主入口就是 **Codex**。你不需要 VS Code，不需要打开终端，也不需要记住或重复执行一大段命令。

你只要在这个项目的 Codex 任务中，用自然语言说明要做什么，例如：

> 进入软件工程团队，帮我实现登录功能。先做七问派活和风险判断，再开始执行。

Codex 会在后台读取项目规则、检查 Git、初始化或读取本地状态、创建隔离 Worktree、调度 Agent、组织测试和 Review，并把真正需要你决定的事项单独列出来。

当前必须准确区分三件事：

- MVP 0 的交互界面是 Codex；底层命令是 Codex 使用的可复现接口，不是你的日常入口。
- 本手册和项目 Skill 属于 MVP 0 实现分支；整个 MVP 0 仍需完成 Task 12 的端到端验收和整合，不能仅凭本文声称已经合并到 `main`。
- GitHub Remote 尚未配置；MVP 1 的本地只读前端工作台尚未实现。两者都需要后续 Human 明确确认。

现行依据：

- [项目交接 handoff.md](handoff.md)
- [七问派活协议](CODEX_AGENT_DISPATCH_PROTOCOL.md)
- [Agent 角色与模型矩阵](AGENT_ROLE_AND_MODEL_MATRIX.md)
- [软件工程生命周期](SOFTWARE_ENGINEERING_WORKFLOW.md)
- [Git 工作方式](GIT_WORKFLOW.md)
- [控制平面设计](docs/superpowers/specs/2026-08-08-ai-engineering-team-control-plane-design.md)

## 2. 首次使用或项目扫描后怎么说

第一次进入项目，直接复制下面这句话：

> 进入软件工程团队。先读取 handoff 和现行协议，检查仓库健康与控制平面状态；如果本地状态库还不存在，请安全初始化。然后告诉我当前阶段、已有任务、阻塞、待审批事项和建议的下一步，不要执行超出当前授权阶段的工作。

Codex 应自动完成：

1. 读取 `handoff.md` 和五份现行工程协议。
2. 从主仓库运行健康检查，核对 `main`、Worktree、分支和脏文件。
3. 检查 Git common directory 下的控制数据库；数据库不存在时安全初始化，而不是只回复“尚未初始化”。
4. 在任何新写入前，核对并 reconcile 未完成的 `PREPARED` 操作。
5. 读取 Git 事实、任务状态、Agent、Blocker、Review、Evidence 和 Approval。
6. 把已验证事实、尚未验证判断和建议下一步分开报告。

如果项目刚刚被 Codex 扫描，也可以说：

> 项目扫描完成后，进入软件工程团队模式。只做只读盘点并告诉我是否适合启动新任务；发现不一致先 BLOCKED，不要清理现场。

## 3. 可直接复制的自然语言指令

以下指令都由你在 Codex 中发送，不需要自己执行 CLI。

1. “进入软件工程团队，先检查项目健康和当前授权阶段。”
2. “进入软件工程团队，帮我实现这个需求：……；先完成七问派活，再开始。”
3. “把这个想法整理成可验收的派活单，先不要写代码。”
4. “开始任务 20260809-001，按 L1/L2/L3 判断风险并说明验收门。”
5. “继续上次未完成的任务；先核对 handoff、Git、锁、Blocker 和恢复点。”
6. “查看当前任务状态，按事实、证据、风险和下一步汇报。”
7. “查看当前任务状态，并告诉我分支、Worktree、HEAD 和最近一次测试结果。”
8. “哪些事项等我批准？逐项说明动作、风险、目标 SHA 和不批准的影响。”
9. “暂停任务 20260809-001；先让写入 Agent 到达安全检查点。”
10. “继续任务 20260809-001；恢复前重新检查 Git、锁和原阻塞条件。”
11. “列出当前所有阻塞，说明负责人、解除条件、证据和下一步。”
12. “请解决这个 Blocker；如果需要扩大权限，先转为 NEEDS_HUMAN_APPROVAL。”
13. “让独立 Reviewer 审查当前结果，执行者不能自我放行。”
14. “Claude 是否已经完成最终验收？如果没有，请明确写成待验收，不要降级。”
15. “把本轮测试、Review、提交 SHA 和残余风险登记为证据。”
16. “检查当前结果是否满足 Definition of Done；缺任何证据都不要标记完成。”
17. “完成这个任务，但整合前先给我看 Review 结论和待审批项。”
18. “盘点本次项目，交给 Mimo 形成总结，再由 Codex 审阅知识候选。”
19. “Worktree 创建失败了。按 Minor 流程先做 Doctor inspect，不要删除任何目录或分支。”
20. “查看 Minor 检查结果；只有明确可修分类才 repair，否则保持 BLOCKED。”
21. “检查是否存在未完成的 PREPARED 操作；先 reconcile，再决定是否继续写入。”
22. “给我一份今天的软件工程团队摘要：进行中、Review 中、阻塞和待批准。”
23. “给我一份本周工程盘点：交付证据、质量问题、返工原因和可沉淀规则。”
24. “先暂停，不要 merge、push、删除 Worktree 或配置 GitHub，等我下一步指令。”

## 4. Codex 在后台自动执行的完整闭环

### 4.1 七问派活与风险判断

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

### 4.2 Worktree、Agent 和 Review

一个写入任务对应：一个短分支、一个独立 Worktree、一个写入 Agent。Codex 独占 `main`、合并、冲突处理、Worktree 生命周期和交接更新；Agent 只能在自己的 Worktree 内改动和提交。

执行完成后，Codex检查改动范围、提交 SHA、测试结果和证据，再交给独立 Reviewer。Reviewer 输出 `ACCEPT / MODIFY / BLOCK / ESCALATE`：

- `ACCEPT`：仍需 Codex 核对门禁，不代表自动合并。
- `MODIFY`：回到同一 Worktree 修改，并重新 Review。
- `BLOCK`：保留现场和证据，停止整合。
- `ESCALATE`：提交 Human 或更高等级 Reviewer 决策。

### 4.3 验收、证据和知识闭环

“完成”必须有：实际文件路径、提交 SHA、测试命令与结果、Review 结论、必要审批、残余风险和整合后验证。进度百分比不能代替这些证据。

长期保留的是蒸馏后的报告与索引，不是密码、Token、私钥或敏感原文。结束时由 Mimo 做项目盘点，Codex 审阅后才形成知识候选；不会把聊天全文自动当成正式知识。

## 5. 你会看到哪些状态和字段

### 5.1 状态报告的核心字段

| 字段 | 你应如何理解 |
|---|---|
| `dispatch_id` | 任务唯一编号，用于查询、暂停、继续和审批 |
| `title` / `objective` | 标题与可验收目标 |
| `risk_level` | L1/L2/L3 风险等级 |
| `state` | 当前生命周期状态 |
| `owner` | 工程控制 Owner，固定为 Codex |
| `task_base_sha` | 任务开始时的基线提交 |
| `head_sha` | 当前任务分支实际提交 |
| `branch` / `worktree_path` | 隔离分支和工作目录 |
| `agents` | 执行者、角色、状态、最后汇报时间 |
| `blockers` | 原因、责任人、解除条件和下一步 |
| `reviews` | Reviewer、结论、严重级别和绑定 SHA |
| `evidence` | 证据路径、摘要、哈希和来源 SHA |
| `approvals` | 待审批或已消费审批及其目标 |
| `head_drift` | 数据库记录与实际 Git HEAD 是否漂移 |

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
| `NEEDS_HUMAN_APPROVAL` | 某个动作命中人工门禁，动作不得执行 |
| `REVIEWING` | 执行完成，正在等待独立审查 |
| `ACCEPTED` | 质量门通过，等待 Codex 安全整合 |
| `INTEGRATED` | 已整合，仍需整合后验证 |
| `RELEASED` | 已按授权交付或发布 |
| `CLOSED` | 已完成盘点、证据归档和知识回流 |
| `UNKNOWN` | 事实不足，禁止猜测性推进 |

暂停是两阶段动作：先进入 `PAUSE_REQUESTED`，所有写入者确认安全后才是 `PAUSED`。继续任务也不是简单“开机”，Codex 必须重查 Git、锁、Blocker 和保存的恢复状态。

`NEEDS_HUMAN_APPROVAL` 是动作门禁：它可以出现在任何阶段，不等同于任务已经失败或已经验收。

## 6. 哪些事情需要你审批，应该怎么回复

以下情况通常必须暂停并请求 Human：

- 删除、覆盖关键原件、批量迁移或不可逆操作；
- 生产系统、真实业务操作、外部发送或首次发布；
- 凭据、敏感数据、新数据源或权限扩大；
- 目标或范围发生战略级变化；
- 配置 GitHub Remote、创建远端仓库或首次 push；
- 从 MVP 0 进入 MVP 1/2/3；
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

GitHub Remote 尚未配置，MVP 0 本地运行不依赖 GitHub。当前不能声称已经创建远端、设置 `origin`、首次 push、分支保护或 CI。

未来启用时，应作为独立高影响任务，由你明确确认：

1. GitHub 个人账户或组织；
2. 仓库名称；
3. Private 或 Public（默认建议 Private）；
4. 认证方式；
5. 是否允许创建远端仓库和首次 push；
6. 主分支保护、PR Review、必要检查和恢复方案。

你届时可以说：

> 我确认启动 GitHub Remote 独立任务。先给我配置方案和风险，不要创建仓库或 push，等我确认账户、仓库名、可见性和认证方式。

## 9. 前端界面现在是什么，未来是什么

MVP 0 当前界面是 Codex：你在 Codex 里发自然语言请求，Codex 操作本地控制平面并把状态翻译成人能读懂的汇报。

MVP 1 是本地只读前端工作台，尚未实现，也没有 `apps/dashboard/` 可供使用。设计目标是展示项目、任务、Agent、审批、Review 和证据，并显示来源 HEAD 与刷新时间；浏览器不能直接改 Git、调 Agent、合并或发布。

从 MVP 0 进入 MVP 1 需要 Human 明确确认，并且应先完成 MVP 0 的 Task 12 验收与主线整合。不能因为“想看界面”就绕过当前验收门。

## 10. 数据位置、隐私与 threat model

### 10.1 数据在哪里

- 代码与协议：当前本地 Git 仓库。
- 运行状态：`<git-common-dir>/team/runtime/team.db`。
- 单仓库写锁：`<git-common-dir>/team/runtime/control-plane.lock`。
- 可提交的蒸馏证据：`artifacts/dispatches/<dispatch-id>/`。
- 所有主 Worktree 和任务 Worktree 共享同一 Git common directory 状态库，但运行数据库不会进入 Git 提交。

SQLite 是本地运行状态索引，不是唯一不可替代事实源。Git 是代码事实源；长期状态还要靠派活记录、提交、测试和蒸馏证据重建。

### 10.2 隐私边界

- 密码、Token、私钥和敏感原文不得写入状态库、事件、Evidence 或仓库。
- Evidence 保存受控路径、摘要、哈希、时间和关联 SHA，而不是任意复制原始内容。
- Agent 只得到完成任务所需的最小上下文。
- 本地优先；MVP 0 不要求云服务或 GitHub。

### 10.3 threat model（威胁模型）

控制锁、单写者、Worktree 隔离、路径校验和操作日志，主要防止**遵守项目协议的受控协作者**发生并发冲突、重复执行、状态漂移或误操作。

它不是同一 macOS 账户下对抗恶意进程的安全沙箱。拥有同等文件权限的恶意或不受控进程，仍可能绕过协作锁直接修改 Git、数据库或文件。发现外部漂移时，系统应 fail closed、转为 `BLOCKED` 并要求重新核验，而不是声称绝对安全。

## 11. 常见问题与故障排查

### Q1：以后每次都要输入一大段命令吗？

不用。你只需说“进入软件工程团队……”或其他自然语言指令。项目 Skill 告诉 Codex 自动读取规则和调用底层工具。

### Q2：Codex 说数据库不存在，我该怎么办？

你可以说：“安全初始化控制平面，然后继续查看当前任务状态。”Codex 不应因为数据库不存在就停止状态请求；它应先核对仓库，再安全初始化。

### Q3：出现 `PREPARED` 是失败了吗？

不一定。它表示 Git 与 SQLite 跨系统操作已登记但还没完成确认。Codex 必须先 reconcile：事实证明已完成就补记，证明未发生才安全处理，无法判断则 `BLOCKED`，绝不能直接重放未知 Git 动作。

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

因为它属于 MVP 1，尚未实现。当前从 Codex 查看状态；完成 MVP 0 验收后，再由你决定是否启动只读工作台。

## 12. 如何验证 Codex 的汇报是否可信

你不需要自己运行命令，但可以要求 Codex提供下列证据：

1. 当前主线、任务分支、Worktree 路径和实际 HEAD。
2. 工作区是否干净，是否存在范围外改动。
3. 当前任务状态、最近事件、Agent、Blocker 和待审批项。
4. 测试命令、测试数量、通过/失败结果和未覆盖区域。
5. Review 报告路径、Reviewer 身份、结论和绑定 SHA。
6. Evidence 路径、SHA-256、来源 SHA 和是否发生漂移。
7. 如果声称已整合：合并 SHA、主线全量验证和交接更新。

推荐直接说：

> 不要只给结论。请给我当前状态的 Git SHA、文件路径、测试数量、Review disposition、待审批项和残余风险，并区分已验证事实与建议。

## 13. CLI 附录（仅供 Codex 和故障复现；本人不需执行）

以下命令是底层确定性接口。你本人不需要执行，也不需要记忆；它们用于 Codex 自动操作、测试或故障复现。

```bash
# 仓库健康
scripts/repo-health.sh

# 安全初始化本地控制平面
scripts/team-control init

# 启动任务
scripts/team-control start --dispatch-id ID --title TITLE --objective OBJECTIVE --risk L1 --agent AGENT --slug SLUG

# 查询任务与待审批项
scripts/team-control status --dispatch-id ID
scripts/team-control approvals --dispatch-id ID

# 受控状态转换
scripts/team-control transition --dispatch-id ID --to STATE --reason REASON

# Minor 只读检查；repair 只能在 inspect 明确可修后使用
scripts/worktree-doctor inspect --dispatch-id ID --agent AGENT --slug SLUG --base-sha SHA
scripts/worktree-doctor repair --dispatch-id ID --agent AGENT --slug SLUG --base-sha SHA
```

这些 wrapper 已绑定所属仓库，输出机器可读 JSON。即使如此，Codex仍必须先读 `handoff.md`、检查健康、reconcile `PREPARED`，并遵守审批门禁；命令存在不等于动作已获授权。

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

> 进入软件工程团队。给我今天的任务状态：进行中、Review 中、阻塞、待批准和建议优先级；先做只读检查。

准备开始新工作：

> 为这个需求建立七问派活单和风险等级，告诉我哪些部分可以自动推进、哪些需要我确认。

结束工作：

> 暂停所有未完成写入任务到安全检查点，汇总今天的提交、测试、Review、Blocker 和明天恢复条件。

### 每周推荐用法

每周盘点：

> 汇总本周任务：已交付、返工、阻塞、Review 发现、测试缺口和残余风险。由 Mimo 盘点，Codex 审阅后只输出可复用知识候选，不自动写入长期事实源。

路线图检查：

> 检查 MVP 0 当前验收证据和未完成门禁。不要启动 MVP 1、GitHub 或任何外部动作，只给我进入下一阶段所需的确认清单。

这套用法的核心是：你负责方向和明确授权，Codex负责工程控制与证据闭环，Agent负责专业执行，Reviewer负责独立挑战；没有证据就不宣称完成，无法证明安全就停止推进。
