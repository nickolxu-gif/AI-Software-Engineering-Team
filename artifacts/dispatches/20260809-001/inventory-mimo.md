# MiMo 项目盘点

> 时间：2026-08-10 00:28 CST  
> 角色：MiMo / Independent Project Analyst  
> 模型：`xiaomi/mimo-v2.5-pro`  
> 输入：提交 `11f4afd` 的隔离 `git archive` 快照，不含 `.git`、控制数据库或任务 Worktree 运行态  
> 结论：`INVENTORY_MODIFY`

## 1. 目标与交付物完成对照

| 派活单交付要求 | 实际快照证据 | 状态 |
|---|---|---|
| 七个只读 API | `dashboard_server.py` 实现 health、project、tasks、task detail、events、evidence、approvals | 已完成 |
| 五类中文视图 | `index.html` 与 `app.js` 实现总览、任务、Agents、审批、证据 | 已完成 |
| 一句话启动入口 | `scripts/open-team-dashboard` | 已完成 |
| 加载、空态、阻塞、审批、过期、不一致和错误态 | 前端状态机、read model 和 HTTP 错误契约 | 已完成 |
| 自动化测试 | MiMo 在隔离快照复跑 273 项全量测试及 56 项 Dashboard 专项测试 | 已完成 |
| 独立 Review | Claude 设计审查第二轮 `ACCEPT`，最终实现审查 `ACCEPT` | 已完成 |
| 验收工件 | dispatch、设计审查、实现审查和 verification 均存在 | 已完成 |
| Codex 整合后复验 | 快照生成时尚未合并 main | 待完成 |
| MiMo 盘点与 Codex 审阅 | 本盘点已完成，等待 Codex 审阅与整合 | 进行中 |

MiMo 判断：设计规范完成标准中的实现、自动化与独立验收已有直接证据；main 整合、整合后复验和 handoff 更新仍是关闭前必须完成的门禁。

## 2. 关键架构与决策

### Verified Fact

- MVP 1 使用 Python 标准库和原生 HTML/CSS/JavaScript，无前端框架运行依赖；
- HTTP 服务强制绑定 `127.0.0.1`，并检查 Host、Origin、方法和固定静态映射；
- SQLite 使用 `mode=ro`、`query_only=ON`、每请求独立连接和 sidecar 身份校验；
- Git 读取使用精确命令白名单、`GIT_OPTIONAL_LOCKS=0` 和隔离环境；
- 首页采用异常优先信息架构；前端为 15 秒刷新、45 秒过期；
- `current_head_sha` 未推进时，Dashboard 如实显示 `HEAD_DRIFT`，不会伪装有效验收。

### Inference

- 纯标准库方案在 MVP 1 只读范围内成本低且合理；若 MVP 2 引入表单、实时事件和复杂交互，应重新评估前端技术栈。

## 3. 返工与发现的问题

- 设计首轮 Claude `MODIFY` 指出 WAL 语义、Git 白名单、API schema、SQLite 并发、列表上限、Origin 和静态映射七项问题；修订后第二轮 `ACCEPT`；
- Python 3.14 只读 URI 会创建空 WAL/SHM，旧逻辑误报 503；最终实现仅放行 reader 创建的空 WAL 和合法 SHM；
- 前端旧成功或旧错误响应可能覆盖新快照；最终实现使用 generation、taskId 和 expectedHead 三重绑定，并限制二级请求全局并发为 4。

## 4. 验证与审阅证据

| 证据 | 结果 |
|---|---|
| MiMo 隔离快照全量测试 | 273 tests，OK |
| MiMo Dashboard 专项测试 | 56 tests，OK |
| MiMo JS 语法检查 | `node --check`，OK |
| 无副作用证明 | database、WAL、Git index、HEAD、refs 和 status 前后一致 |
| Claude 设计审查 | `ACCEPT` |
| Claude 实现审查 | `ACCEPT` |
| Codex 双重交叉复核 | 两份 `ACCEPT` |
| 真实 API 和浏览器核验 | API 200，五视图人工 DOM 核验完成 |

## 5. 残余风险与建议

- 并发 writer 场景缺自动化覆盖：低风险，建议后续增加持续 WAL commit 与并发读取回归；
- 前端竞态缺仓库内可重复浏览器测试：低风险，建议后续增加 Playwright 或 jsdom 行为测试；
- `ThreadingHTTPServer` 无逐连接超时和线程上限：当前 loopback 威胁模型下接受，扩大访问范围前必须重设安全边界；
- `current_head_sha` 无受控推进入口：会使真实任务保持 HEAD drift，应在后续控制面版本补齐；
- main 尚未整合、handoff 尚未更新：盘点时属于关闭阻断项；
- GitHub Remote 未配置：属于后续独立任务，不在 MVP 1 范围。

## 6. 可复用知识候选

### Verified Fact

1. Python 3.14 的 SQLite 只读 URI 可能创建空 WAL/SHM；只读校验必须区分 reader 协调文件与非空业务 WAL。
2. `GIT_OPTIONAL_LOCKS=0`、禁用 fsmonitor/maintenance、隔离环境和精确 argv 白名单，可形成可复用的 Git 只读模式。
3. 设计审查与实现审查两阶段门禁在本任务中提前发现并修正了架构和运行时兼容问题。

### Inference

4. 原生 JavaScript 在 MVP 1 足够，但 MVP 2 的复杂交互可能显著提高维护成本。
5. 异常优先、中文标签和“回到 Codex 处理”的信息架构可能降低非技术用户的操作门槛。

### Proposal

6. 建立标准并发 writer 测试模板：一个线程持续提交 WAL 事务，另一个线程并发读取并验证快照一致性和身份校验。

上述 Inference 与 Proposal 只是知识候选，不自动升级为团队事实或规范。

## 7. 关闭建议

MiMo 建议暂不关闭，先完成以下门禁：

1. 将任务分支整合到 `main`；
2. 在整合后的 `main` 重新运行全量测试；
3. 更新 `handoff.md`，记录 MVP 1 为 `ACCEPTED and integrated` 及集成 SHA；
4. 由 Codex 审阅本盘点并记录最终关闭判断。

完成前三项后，MiMo 建议关闭任务。

## 结论

`INVENTORY_MODIFY`
