# Claude Code 最终实现审阅

> 时间：2026-08-10 00:23 CST  
> Reviewer：Claude Code / Sonnet  
> 候选：`f785ea1f40820481c19d1f8d5f511485576fc63d`  
> 基线：`afb4fa217162aaaca3658b177ff901346a43e7da`  
> 模式：只读、Plan mode、无 session persistence  
> 结论：`ACCEPT`

以下为 Claude Code 原始审阅输出：

---

## 审查结果

### 1) SQLite WAL/SHM 与 Git 只读边界

- `_validate_storage_files`/`_verify_storage_identity`（`team_control/dashboard_read_model.py:296-372`）在 `snapshot()` 前后做身份校验，符号链接、非常规文件一律拒绝（`lstat` + `stat.S_ISLNK`）。
- 关键修复（f785ea1）：Python 3.14 上只读 URI 连接会自建空 `-wal`/`-shm`，旧逻辑会误判为"变更"而 503。新逻辑仅放行"expected 为 None 且 observed 为空/新建"的情形，非空 WAL 仍判定为变更并拒绝——**已在 Python 3.9 和 3.14 双版本实测通过**（我本地重跑 `tests.test_dashboard_end_to_end tests.test_dashboard_read_model`，3.14 下 28 test OK）。
- 身份比较 `identity()` 只取 `(dev, ino, mode)`，不含 size/mtime，这是有意为之：用于捕捉"文件被替换"（TOCTOU/符号链接掉包），而非阻止同 inode 上的正常并发写入——因为读快照的一致性由 SQLite 自身的 WAL 隔离在 `BEGIN` 时刻保证，这个设计是合理的，非缺陷。
- Git 侧命令白名单 + 环境隔离（`READONLY_GIT_PREFIX`、`_git_environment`、`inherit_env=False`）阻断了通过环境变量或非白名单命令产生副作用的路径。
- 我实际起了本地服务并对真实 `team.db` 反复请求后用 `git status`/`git diff --stat` 核实：仓库无脏改动、HEAD 未变。

**非阻塞观察**：现有端到端测试仅覆盖"无并发 writer"场景（design 文档 7.2 节也如实标注这一范围），没有一个测试在 dashboard 读取期间真的有另一线程/进程对同一 SQLite 库做 WAL 写入提交，验证"读快照在并发写入下仍保持一致、且身份校验不会被写入活动误判"。这是 WAL 只读设计的核心声称能力，目前完全靠代码走查佐证，未被自动化证据覆盖。建议后续补一个"并发 writer 线程持续 commit + dashboard 并发读取"的测试，但不属于本次阻塞项（MVP 1 场景下典型用法是单一 Codex 写入进程，不常与仪表盘并发写同一事务）。

### 2) HTTP loopback/Host/Origin/方法边界

- `create_server` 强制 `host == "127.0.0.1"`；Host 头必须精确匹配 `localhost:port` 或 `127.0.0.1:port`；Origin 存在时必须匹配同源 `http://` 前缀。
- 我用 curl 实测：伪造 `Host: evil.com` → 400；伪造 `Origin: http://evil.com` → 403；POST/PUT 等一律 405 `READ_ONLY`；路径穿越 `../../etc/passwd` → 404（`_safe_path` 对 `%`/`\\`/`%2f`/`..` 均拦截）。
- 异常处理统一捕获并返回通用 `INTERNAL_ERROR`，不泄露 traceback；错误响应仍附带 `source_head_sha`，符合"错误态也要展示来源 HEAD"的设计要求。

**非阻塞观察**：`ThreadingHTTPServer` 未设置逐连接超时/最大并发线程数，理论上可被同机进程通过慢连接耗尽线程。设计文档明确将威胁模型限定为"仅本机、非远程访问"，在这个前提下不构成实质风险，但如果未来放宽绑定范围需要重新评估。

### 3) 前端快照 HEAD 绑定、陈旧响应竞态、并发上限

- 代码走查 `apps/dashboard/app.js`：`assertSourceHead`、`generation`/`taskId`/`expectedHead` 三重校验贯穿 `loadTaskDetail`/`loadEvidence`/`refresh` 的成功和失败分支（`471019a`、`a2bbd4c` 两次修复的落点），逻辑自洽——陈旧成功响应和陈旧错误响应都会在写回 state 前被丢弃。
- `runSecondary` + `SECONDARY_REQUEST_LIMIT = 4` 实现了并发上限，`mapLimit` 用同一限额并发拉取任务详情。
- 我尝试用 Playwright 实际在浏览器里验证这些竞态路径，但当前处于 Plan 模式，浏览器工具被禁用，只能退回到读取源码 + 静态 `test_dashboard_ui_contract.py`（纯文本断言，不执行 JS）+ 用 curl 验证后端语义正确（如 HEAD_DRIFT 字段真实反映漂移）。

**非阻塞观察**：仓库里没有任何真正执行 `app.js`（jsdom/Playwright/headless browser）的自动化测试，"陈旧响应竞态"和"并发上限=4"目前只被源码存在性断言覆盖（`test_dashboard_ui_contract.py` 用正则检查字符串是否出现），不是行为级验证。验证记录里"Codex 内置浏览器对真实服务完成…DOM 检查"是人工/一次性观察，不是可重复的自动化证据。建议后续补一个轻量浏览器自动化测试，但考虑到 MVP 1 范围内是 L2 而非 L3，且核心竞态防护逻辑清晰可读，这不构成阻塞。

### 4) 测试是否足以证明无副作用

- 273 个测试在 Python 3.9 和 3.14 下全部通过（我本地复跑确认），覆盖只读边界失败关闭路径（符号链接、非空 WAL 缺 SHM、schema 不支持、DETAIL_LIMIT 超限等）。
- 端到端测试用真实临时 Git 仓库 + 真实 SQLite WAL 库，请求前后对数据库 SHA-256、WAL 内容、Git index、HEAD、refs、status 做整体比对；我额外手动起服务、发送多个真实请求后核对 `git status`/`git diff` 干净，`git log -1` HEAD 不变，与验证记录一致。
- 字段白名单测试确认 API 响应不含 `nonce_hash`、`payload_json`、`report_path` 等内部/敏感字段。

### 5) `current_head_sha` 缺少受控推进入口

- `verification.md` 第 92 行明确写明"MVP 0 当前没有受控的 task `current_head_sha` 推进入口，因此真实任务在开发提交后会如实显示 HEAD drift"——这是如实披露而非掩盖。
- 我用 curl 实测真实任务 `20260809-001`：`current_head_sha` 仍是 base SHA，`attention_reasons` 正确包含 `HEAD_DRIFT`，`/api/project` 因此展示 `ATTENTION`。代码路径（`_task_item`/`task()` 中 `valid_acceptance` 要求 `head_available and actual_head is not None`）在此状态下不会把过期状态伪装成"有效验收"，符合反作弊条款第 4 条的边界。

## 结论

未发现阻塞性问题。SQLite/Git 只读边界、HTTP loopback/Host/Origin/方法边界均已用真实运行环境实测验证；已知的 `current_head_sha` 推进缺口如实披露并在数据模型层面正确反映为 HEAD 漂移，不构成"伪造验收"。两点非阻塞观察（并发 writer 场景缺自动化覆盖、前端竞态逻辑仅有静态文本断言而无真实浏览器执行测试）建议记录为后续改进项，不影响本次验收。

**ACCEPT**
