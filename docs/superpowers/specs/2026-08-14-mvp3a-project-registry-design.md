# MVP 3A 本地多项目控制台设计

> 任务：`20260814-001`
> 风险：L2
> 决策：Human 已确认手动本地 allowlist 路线。

## 1. 目标与边界

MVP 3A 解决“一个 Codex 工程团队工作台只能看一个仓库”的可见性限制：在当前控制仓库中维护一个显式项目目录，汇总多个**已登记**本地 Git 仓库的安全只读状态。Codex 仍是所有工程动作的唯一控制者；浏览器只看状态和提交既有受控意图，不能登记项目或执行跨仓库操作。

本阶段不建设完整客户端。它不扫描磁盘、不连接 GitHub、不创建远程、不开启通知、WebSocket、App Server、账号、权限管理或后台 daemon；也不读取项目源码、证据正文、task-intake `context`、绝对路径或控制库之外的数据。

## 2. 架构

```text
Human → Codex 自然语言“登记项目”
             │
             ▼
当前控制仓库的 ControlStore / project_registry
             │
             ▼  逐个、有上限的只读采样
已登记项目的 Git 元数据 + 可选控制库安全摘要
             │
             ▼
本地 Dashboard Projects 视图（仅安全字段）
```

中央 registry 存放在当前控制仓库的 Git common-directory runtime SQLite 中，与现有任务状态同属本机控制面，且不提交 Git。登记记录包含随机 `project_id`、用户指定显示名、经验证的规范化仓库根路径、仓库 common-dir 身份、状态、创建/更新时间和退役时间。绝对路径只保存在本地控制库，不进入浏览器、日志、Git 工件或 Claude 审阅包。

项目最多保留 20 个 `ACTIVE` 条目。取消登记不物理删除，而是由 Codex 设为 `RETIRED` 并保留最小审计事实；MVP 3A 不提供浏览器侧取消登记。

## 3. Codex 登记契约

新增的 Codex-only CLI/API 接受 `display_name` 与绝对项目路径。它必须：

1. 拒绝空值、控制字符、过长字段、重复 label 或重复规范路径；
2. 用 `RepoContext.discover()` 验证目标是本地 Git 仓库，并绑定其实际 common-dir 身份；
3. 拒绝符号链接根、路径身份漂移、运行中被替换的仓库，或超出手动登记请求的路径；
4. 在当前控制库的单一事务中写入 registry 和安全事件；
5. 绝不初始化、迁移、写入或修复目标项目的控制库。

登记生命周期状态只有 `ACTIVE` 或 `RETIRED`。目标没有控制库或读取时发现 schema、身份或可用性异常时，采样卡片的控制状态才会显示 `UNINITIALIZED`、`UNAVAILABLE`、`UNSUPPORTED` 或 `IDENTITY_MISMATCH`；这些状态不是错误修复指令。

## 4. 只读采样与安全摘要

Dashboard 刷新时对 active 项目按稳定登记顺序逐个采样，最多读取 20 个项目。每个项目仅获得：

- `project_id`、显示名、登记状态和采样时间；
- 当前 HEAD SHA 或 `HEAD_UNAVAILABLE`；
- 控制库可用性：`HEALTHY`、`UNINITIALIZED`、`UNAVAILABLE`、`UNSUPPORTED` 或 `IDENTITY_MISMATCH`；
- 若控制库兼容，受限的任务状态计数和最近任务更新时间。

采样必须使用只读 Git 调用与只读 SQLite 连接。缺库、锁超时、权限问题、坏 schema、路径漂移或单项目异常只能影响该项目卡片；其余已登记项目继续返回。服务器设置总读取预算和每个项目固定超时，不递归跟随工作树、子模块或链接目录。

跨项目搜索只对已经返回的显示名、公开状态标签和任务计数在浏览器内过滤；不新增全文索引，不向未登记项目发起读取，也不向浏览器暴露任务标题。

## 5. Dashboard 与交互

Dashboard 新增 Projects 视图和总览项目计数。每张卡片显示显示名、健康状态、短 HEAD、任务计数、最后采样时间和一个“在 Codex 中继续”提示。前端不显示路径、仓库 remote、任务 context、证据内容、审批 nonce 或原始错误。

登记与退役操作仅通过 Codex 自然语言请求进入受控 CLI；浏览器没有 `POST /api/projects`、删除路由、目录选择器或任何跨项目写接口。现有 task-intake、intent 和单项目只读 API 的边界不变。

## 6. 失败语义与反作弊

不得以“项目卡片存在”冒充目标项目已初始化或健康；不得把 `UNAVAILABLE` 归并为零任务或健康；不得因读取失败自动 init、修改 SQLite、重新绑定不同仓库或绕过 allowlist。

项目数上限、稳定顺序与 per-project 隔离防止通过无限登记或扫描制造“多项目支持”假象。测试必须证明浏览器无法登记项目或读取绝对路径，且所有跨项目读取都来自显式 registry。

## 7. 验证与验收

自动化测试覆盖 registry 合同、唯一性、容量、退役、路径/Git 身份、符号链接拒绝、缺控制库、schema 不兼容、单项目故障隔离、只读查询、公开字段白名单、Dashboard 路由和单项目回归。

整合前必须通过 `git diff --check`、默认 Python 与 Python 3.14 全量测试，以及 Claude Code V4 不可变包的严格 `PASS`。整合后在 `main` 重新运行两套全量测试和 `./scripts/repo-health.sh`。GitHub remote、push、发布或下一阶段完整客户端不在此验收内。

## 8. 实施拆分

1. Registry schema、Codex-only 登记/退役合同和纯只读采样器。
2. Dashboard read model、HTTP 公开字段和只读 Projects 视图。
3. 端到端边界、双解释器回归、Claude 验收、盘点与本地整合。

任何一个阶段发现跨仓库访问边界无法用显式 allowlist 证明时，停止在 `BLOCKED`，不以完整客户端或远程接入替代修复。
