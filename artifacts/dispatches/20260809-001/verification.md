# Dispatch 20260809-001：MVP 1 验证记录

> 验证时间：2026-08-10 00:23 CST  
> Task base：`afb4fa217162aaaca3658b177ff901346a43e7da`  
> 实现候选：`f785ea1f40820481c19d1f8d5f511485576fc63d`  
> 验收状态：自动化、本地展示、两份 Codex 独立交叉复核与 Claude Code 最终实现审查均通过；Claude 结论为 `ACCEPT`。

## 1. 自动化验证

| 命令 | 结果 |
|---|---|
| `python3 -m unittest tests.test_dashboard_ui_contract tests.test_dashboard_read_model -q` | 35 tests，exit 0，OK |
| `python3 -m unittest tests.test_dashboard_end_to_end -v` | 1 test，exit 0，OK |
| `python3 -m unittest discover -s tests -q` | 273 tests，exit 0，OK |
| `python3.14 -m unittest discover -s tests -q` | 273 tests，exit 0，OK |
| `node --check apps/dashboard/app.js` | exit 0 |
| `git diff --check` | exit 0 |

端到端测试在真实临时 Git 仓库和真实 SQLite WAL 控制库上创建 normal、blocked、pending-approval 三类任务，随后覆盖七个 API、三个静态资源、七种拒绝写方法和 12 次并发读取。阻塞与审批任务按异常优先规则领先；响应不包含 approval/event/review 内部字段。

## 2. 真实本地启动与 API

候选通过以下入口启动，未打开外部浏览器：

```text
scripts/open-team-dashboard --no-open --port 0
```

最终候选的一次验证实例为 `http://127.0.0.1:59295`，仅绑定 loopback；启动输出包含仓库身份、候选完整 SHA 和 `reused=false`。验证结束后进程收到 SIGINT，exit 0。

| 路径 | HTTP | 结果 |
|---|---:|---|
| `/api/health` | 200 | 健康 envelope 与仓库身份存在 |
| `/api/project` | 200 | 真实项目状态；当前因任务记录 HEAD 未推进而显示 ATTENTION |
| `/api/tasks?limit=100&offset=0` | 200 | 找到真实任务 `20260809-001` |
| `/` | 200 | 包含“只读”标签，无 form 或 inline action handler |

动态端口只用于验收，地址不会持久化。默认启动使用由 Git common directory 身份派生的稳定本地端口。

## 3. 无副作用证明

在真实任务 Worktree 上，于启动器、四个真实请求和正常终止前后记录以下事实；前后结构完全相等：

| 事实 | 前后值 |
|---|---|
| database SHA-256 | `b16766faa2e190f5d625df446f10e5fe6b15f3425536c14d65b56721403db088` |
| main WAL SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Git index SHA-256 | `a6c4cc5a22d7cf5f09f821996a2ee15674958ce3bacbce0d7c451a8ce6b57c51` |
| Git index mtime ns | `1786257435512177337` |
| HEAD | `f785ea1f40820481c19d1f8d5f511485576fc63d` |
| refs 摘要 SHA-256 | `ab6f67c8bcfe42ec11473bc2de128368980e2d45e493e5dcd18770dd7ddf4f0d` |
| Git status | 前后相同，仅含本验证记录草稿 |
| SQLite sidecar | 空 WAL（0 bytes）与普通 SHM 协调文件，前后状态相同 |

验收探针本身使用 `GIT_OPTIONAL_LOCKS=0`、禁用 fsmonitor/maintenance、隔离全局 Git 配置，避免普通 `git status` 刷新 index。`-shm` 是 SQLite 协调区；不存在与空 WAL 具有相同业务内容语义，sidecar 的存在性和类型另行记录。Python 3.14 的真实 E2E 测试还覆盖了 sidecar 原本不存在、只读连接创建空 WAL/SHM 的路径。

## 4. 五视图与交互核验

Codex 内置浏览器对真实服务完成以下可见 DOM 检查：

- 总览：健康、四张计数卡、异常优先队列、来源 HEAD、刷新时间和“请回到 Codex 处理”；
- 任务：搜索、风险筛选、真实任务列表、任意已列出任务详情；
- 任务详情：owner、executor、生命周期、三类 SHA、分支、Worktree、Agent/Blocker/Review/审批/证据摘要和事件 reason；
- Agents：无数据时明确空态；
- 审批：只读索引说明，无浏览器审批控件；
- 证据：绑定已选任务与当前 source HEAD，无数据时明确空态。

前端每 15 秒刷新；45 秒无成功响应时标记 stale 并保留最后成功快照。不同 HEAD 的基础、详情、事件或证据响应不会混合；过期的成功响应和错误响应均按 generation、task、captured HEAD 丢弃。

## 5. 变更范围

- 新增 Dashboard read model、HTTP server、launcher、原生 HTML/CSS/JavaScript；
- 新增 read model/server/UI/launcher/端到端测试；
- 更新只读进程与 SQLite 读取基础；
- 更新项目 Skill 和 `USER_OPERATING_GUIDE.md`；
- 新增 MVP 1 dispatch、设计、计划与设计审查证据。

完整文件清单由以下命令复核：

```text
git diff --name-status afb4fa217162aaaca3658b177ff901346a43e7da..f785ea1f40820481c19d1f8d5f511485576fc63d
```

未配置 Remote、未 push、未加入云服务、未实现 MVP 2/3。

## 6. 已知限制与门禁

- 工作台严格只读；不能修改状态、调 Agent、审批、修复、merge、push 或发布；
- 仅允许 `127.0.0.1`，不支持 LAN、远程访问或认证后外放；
- 任务列表和事件使用显式上限；首屏最多 100 个任务，事件超过 100 条会提示截断；
- 控制库没有自动秘密扫描，API 仅返回字段白名单；
- MVP 0 当前没有受控的 task `current_head_sha` 推进入口，因此真实任务在开发提交后会如实显示 HEAD drift，不能伪装成有效验收；
- GitHub Remote 仍未配置，属于后续独立高影响任务；
- MVP 2 自然语言动作意图和 MVP 3 更广协作均不在本次范围；
- 当前自动化尚未覆盖 dashboard 读取期间另一 writer 持续提交 WAL 事务的行为级场景；本次采用 SQLite 快照语义、代码走查与无并发 writer E2E 作为证据，后续应补并发 writer 回归；
- 前端竞态和并发上限已通过源码审阅、行为探针和人工浏览器核验，但仓库内尚无可重复运行的 Playwright/jsdom 浏览器测试；后续应补轻量行为级测试；
- `ThreadingHTTPServer` 未设置逐连接超时或最大线程数；当前仅限 loopback 的威胁模型下接受，未来若扩大访问范围必须重新设计；
- Claude Code 在 00:17 CST 额度恢复后完成实质审阅并给出 `ACCEPT`；原始证据见 `implementation-review-claude.md`。

## 7. Codex 独立交叉复核

候选 `f785ea1f40820481c19d1f8d5f511485576fc63d` 已取得两份独立 `ACCEPT`：

- SQLite/Git 只读边界审阅：Python 3.14.6（SQLite 3.53.3）全量 273 项通过；确认仅放行 reader 创建的空 WAL 和合法 SHM，非空 WAL、路径替换与符号链接仍失败关闭；
- UI/API 竞态审阅：旧成功与旧错误响应均不能覆盖新快照，task/source HEAD 缓存绑定有效，20 个二级请求的实测并发峰值为 4；默认 Python 全量 273 项通过。

两份交叉复核不是 Claude Code 最终验收的替代品。2026-08-10 00:17 CST 自动续跑后，Claude Code 对同一实现候选完成只读实质审阅，确认无阻塞性问题并给出 `ACCEPT`。其三项非阻塞观察已原样保留并纳入上述已知限制。
