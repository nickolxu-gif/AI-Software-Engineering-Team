# 专岗协作组织

当前规范版本：`2026-11-08`

本目录现在以 2026-11-08 公开实施基线为当前目标组织。旧合同 v1.4 仍留在 `history/v1.4/`，只作为可追踪历史。旧的「6 入口 + 14 逻辑岗」不再是目标组织。

本仓库没有创建、配置或运行这些岗位的机器人。首批五个长期专岗要由父级工具正式创建；这里只提供可用的配置模板和合成文件校验。文件校验通过不是知识内容验收通过。

## 状态轴

这四轴分开记录，不能收成一个「状态」：

| 轴 | 取值 | 本仓库现状 |
|---|---|---|
| 分工 | `selected` / `tendency` / `candidate` / `undecided` | 按基线写入 `role-registry.json` |
| 配置 | `not_configured` / `configured` | 全部 `not_configured`。没有新的回读证据 |
| 运行 | `not_run` / `running` / `ran` | 全部 `not_run` |
| 验收 | `not_accepted` / `accepted` / `unverified` | 全部 `not_accepted` |

`configured` 必须有回读证据。`ran` 必须有运行证据。`accepted` 必须有独立验收证据，而且验收者不是作者。三层是幕僚判断、专业执行、个人生活与知识的职责域，不是审批链，也不是技术隔离证明。

## 历史 v1.4

提交 `a97ce946c46d47026d10edc445cec8dc58bbcc32` 记录过静态状态 `role_contracts_applied_and_readback_verified`：外部界面更新 5 个既有 Bot 的 description 岗位合同，创建 1 个知识生活岗位，6 份 profile 回读成功，13 项静态配置核验通过。功能状态当时就是 `unverified`，合成试点 `draft_not_started`。实际 Bot 指令不自动等同角色安全沙箱。

这段历史不配置当前注册表，也不再描述目标组织。正文里的「当前」「仍是」只对 v1.4 当时成立。

## 首批模板

已选、但尚未在本仓库创建的五个模板：

- 任务规划 `task-planning`
- 方案审查 `plan-review`
- 阅读整理 `reading-arrangement`
- 知识分析 `knowledge-analysis`
- 知识内容验收 `knowledge-content-acceptance`

模板正文在 `first-batch-role-templates.json`。`deployed`、`configured`、`created_in_this_repository` 都是 false。

## 合成校验 SYN-READ-01

`tests/ai_organization/fixtures/syn-read-01/` 是固定合成材料，不是实测报告。

- v1：固定设备 X、室温、10 个样本、平均 12 秒；能源未记录；跨设备检查未做。设备 Y、低温、8 个样本、平均 15 秒。一次失败，原因未知，没有重复。评论者认为甲可能更快并希望长期批处理，没有新测量，也不是使用者承诺。成本、能源、长期稳定、用户偏好都没有数据。
- v2：只把记录 A 的平均用时从 12 秒更正为 14 秒，保留 v1，并写明受影响产物。复验记录只有一次。
- `fault/error-copy.json`：标明故意错误副本。校验脚本必须报出直接比较、把缺失当成零或已知、评论当成承诺、失败原因被填上、作者自验收等缺陷。

```bash
python3 tests/ai_organization/check_syn_read.py
python3 -m unittest discover -s tests/ai_organization -v
```

脚本打印 `file_check=PASS` 时，同时打印 `professional_acceptance=not_performed`。前者只说明文件和负例符合校验规则。

## 文件

| 路径 | 作用 |
|---|---|
| `baseline-2026-11-08.md` | 当前规范正文 |
| `role-registry.json` | 机器可读岗位注册表 |
| `schemas/role-registry.schema.json` | 注册表 schema，版本 `2026-11-08` |
| `first-batch-role-templates.json` | 五个首批岗位的配置模板 |
| `schemas/record-schemas.json` | 任务、交接、验收、版本变更 schema |
| `templates/` | 未填任务标识的记录模板 |
| `history/v1.4/` | 已被取代的 v1.4 合同 |

校验脚本放在 `tests/ai_organization/`。它们只读本仓库里的合成夹具和 JSON，不接私域，不调用机器人接口，不改权限，也不改既有工程控制平面。

## 执行适配层

`adapters/local_execution/` 是可单独拷贝安装的本地运行器，当前包版本 `0.1.4`。Codex、Codex2、Kimi 仍使用已经核对过的帮助参数。Hermes 默认路由是 `chat --query-file - --oneshot --format stream-json --in WORKSPACE --max-turns LIMIT`，不带 `-m`、`--provider`、`-p`、`-z` 或 `--usage-file`。这条绑定依据网关客户端对参数的接受与拒绝，对照过 Hermes Agent v0.23.0；`chat --help` 仍可能列出 `--run-budget`，但该参数不再写入 argv，帮助文本单独不足以作为可执行合同。旧批准若带 `run_budget_seconds`，会被拒绝，不能按原合同静默重放，也不会写入用户的 Hermes 配置。`process_timeout_seconds` 只作为客户端进程期限。关掉或杀掉 CLI 不能证明网关侧回合已经停止，也不能证明远端工作或计费已经结束。DSH 的启动合同仍为空，Cursor 不进本地运行器，OpenClaw 保持暂停，Claude 保持停用且不会被探测。这里的测试使用假可执行文件。它不是已经接通的多模型系统，也不把岗位注册表改成已配置或已运行。本版本不是一次成功的 Hermes 通路测试，也不是独立验收。
