# Dispatch Record：20260814-001 MVP 3A 本地多项目控制台

> 状态：`REVIEWING`
> Owner / Builder：Codex
> 风险：L2（已授权的本地跨仓库只读汇总）
> 基线：`c2db9dfec1302716b420553ff6837955c208d819`
> 当前候选：以本任务隔离 Worktree 的 HEAD 为准；Claude 严格验收与本地整合仍待完成。

## Q1 目标、范围与完成标准

建立手动登记的本地项目目录和跨项目只读控制台。Codex 可显式登记一个已验证的本地 Git 仓库；工作台展示已登记项目的安全摘要、健康状态、HEAD 与受限任务计数。

不做自动目录扫描、浏览器登记/删除项目、跨项目写入、Git 操作、远程、GitHub、通知、WebSocket、App Server、账号或权限体系。

完成标准是：登记路径与仓库身份 fail closed；每个项目只读采样且故障隔离；浏览器不泄露绝对路径或私有上下文；默认 Python 和 Python 3.14 全量测试、主线健康检查及 Claude 独立验收通过。

## Q2 风险、授权与验收

L2。Human 已确认 MVP 3A 的手动 allowlist 路线。任何新项目目录都必须由 Codex 在本次运行时显式登记；不扩大为扫描用户目录或远程发现。Claude Code 是唯一最终独立 Reviewer；不可用时默认等待，不自动降级。

## Q3 执行者、Reviewer 与路由

- Builder / Integrator：Codex。
- Reviewer：Claude Code V4 不可变只读包。
- MiMo：若仍无可调用独立入口，只记录不可用事实，不伪造盘点。

## Q4 上下文、隔离与允许路径

- 分支：`agent/Codex/20260814-001-mvp3a-project-registry`。
- Worktree：`.worktrees/20260814-001-Codex-mvp3a-project-registry`。
- 允许路径：控制库、只读聚合器、CLI、Dashboard、相关测试、使用手册、任务工件、规格与计划。
- 禁止：仓库根 `main` 写入、未登记目录读取、远程配置、push、浏览器执行权、全局配置修改。

## Q5 执行、状态与纠偏

先以测试定义 allowlist、路径身份、只读采样和错误隔离；再实现最小 registry 与只读项目视图。发现路径漂移、符号链接、缺失/不兼容控制库或未覆盖的跨仓库边界时 fail closed，保留该项目状态并继续其他项目；不自动修复或初始化目标项目。

## Q6 验收

验证手动登记、重复/冲突登记、路径与 Git 身份校验、无自动初始化、无绝对路径泄露、每项目故障隔离、项目数与读取上限、Dashboard 只读边界，以及现有单项目功能回归。仅接受 Claude V4 的可解析 `PASS`；`PASS_WITH_WARNINGS`、空输出或 fallback 意见不整合。

## Q7 盘点与知识回流

沉淀“多项目能力先用显式本地 allowlist 和只读摘要验证需求，再考虑完整客户端”的原则。保存派活、规格、测试、Claude receipt 与 Codex 门禁；不得写入项目绝对路径、原始上下文或凭据。
