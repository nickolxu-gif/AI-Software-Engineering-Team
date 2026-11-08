# 职责地图

合同版本：1.4

本文件保留全部逻辑职责。分层是职责分组，不是安全域，也不是 Bot 分组。

## 公开状态

公开状态码 `role_contracts_applied_and_readback_verified`：外部界面已更新 5 个既有 Bot 的 description 岗位合同，并创建 1 个知识生活岗位；6 份 profile 回读成功；13 项静态配置核验通过。原专业描述保留，并与统一协调、路由建议、PM 跟踪、限定执行和私域未获数据访问权限相一致。功能状态 `unverified`。这次静态回读不是技术 ACL、独立审查账号接入、端到端调度、模型执行、自动化、CLI、私域连接或驻留隔离的功能验证。实际 Bot 指令不自动等同角色安全沙箱。合成试点状态 `draft_not_started`。不记载「配置已通过测试」。本仓库只版本化公开合同，不收录身份 ID、原描述或私人路径，也没有在本目录复读外部 profile。

## 三层与十四个职责

| 层 | 名称 | duty_id | 标题 | 首轮激活 |
|---|---|---|---|---|
| L1 | 幕僚治理 | `coordination-entry` | 协调入口 | 每个任务的入口槽 |
| L1 | 幕僚治理 | `strategic-planning` | 战略计划 | 只保留定义 |
| L1 | 幕僚治理 | `resource-cost-advisor` | 资源成本顾问 | 只保留定义 |
| L1 | 幕僚治理 | `project-manager` | 项目经理 | 只保留定义 |
| L1 | 幕僚治理 | `independent-quality` | 反调独立质检 | 每个任务的独立验收槽 |
| L2 | 专业执行 | `engineering` | 工程研发 | 被选为执行槽时 |
| L2 | 专业执行 | `product-ui` | 产品 UI | 被选为执行槽时 |
| L2 | 专业执行 | `modeling-data` | 建模数据 | 被选为执行槽时 |
| L2 | 专业执行 | `multimedia-content` | 多媒介内容 | 被选为执行槽时 |
| L2 | 专业执行 | `research-materials` | 研究资料 | 被选为执行槽时 |
| L3 | 个人知识生活服务 | `knowledge-management` | 知识管理 | 被选为执行槽时 |
| L3 | 个人知识生活服务 | `life-records` | 生活记录 | 被选为执行槽时 |
| L3 | 个人知识生活服务 | `health-records` | 健康记录 | 隔离证实前不可激活 |
| L3 | 个人知识生活服务 | `private-communications` | 私人通信 | 隔离证实前不可激活 |

```text
duty_id: coordination-entry | layer: L1 | activation: per_task_entry_slot | title: 协调入口
duty_id: strategic-planning | layer: L1 | activation: map_only | title: 战略计划
duty_id: resource-cost-advisor | layer: L1 | activation: map_only | title: 资源成本顾问
duty_id: project-manager | layer: L1 | activation: map_only | title: 项目经理
duty_id: independent-quality | layer: L1 | activation: per_task_independent_acceptance_slot | title: 反调独立质检
duty_id: engineering | layer: L2 | activation: per_task_execution_slot_when_selected | title: 工程研发
duty_id: product-ui | layer: L2 | activation: per_task_execution_slot_when_selected | title: 产品 UI
duty_id: modeling-data | layer: L2 | activation: per_task_execution_slot_when_selected | title: 建模数据
duty_id: multimedia-content | layer: L2 | activation: per_task_execution_slot_when_selected | title: 多媒介内容
duty_id: research-materials | layer: L2 | activation: per_task_execution_slot_when_selected | title: 研究资料
duty_id: knowledge-management | layer: L3 | activation: per_task_execution_slot_when_selected | title: 知识管理
duty_id: life-records | layer: L3 | activation: per_task_execution_slot_when_selected | title: 生活记录
duty_id: health-records | layer: L3 | activation: blocked_until_isolation_proven | title: 健康记录
duty_id: private-communications | layer: L3 | activation: blocked_until_isolation_proven | title: 私人通信
```

外部界面上的 5 次 description 更新和 1 个新建知识生活岗位，是 profile 层事实。它们不把上表改成 6 个或 15 个逻辑职责，也不按职责逐一对应。已有入口不因此被删除、迁移或停用。

## 对象边界

每张岗位卡都是职责。卡上的工具身份为空，不代表某个 Bot。空预算不是政策阈值。读写边界是一次任务的范围，不是永久安全域。实际 Bot 指令不自动等同角色安全沙箱。

工具和环境可以替换。能力必须验证之后才记为可用。当前功能状态是 `unverified`。

本目录不读取、不迁移任何真实个人数据。云端入口不是本地隔离。

## 路由建议

路由顾问不是第 15 个职责，也不是第二个派单者。它是协调入口中的建议职能，可以引用资源成本顾问的职责定义。建议只包括三项：执行职责、独立复验、备用。建议不启动备用，也不创建另一张派单。需要协作时回到协调入口，由同一次编排处理。

## 共同停止条件

每张可定义的卡都使用下面的停止条件。这些是合同语句，不是已经接通的自动开关。

- 当前动作没有被已有授权覆盖。
- 范围、权限、驻留、费用上限或不可逆动作发生实质变化，且尚未再确认。
- 无法完成或需要协作时，停止扩权并回到主入口。主入口就是协调入口。
- 暂停或取消既没有端回执，也还没有记为明确未确认。
- 结果未知且可能已有副作用时，停止重放。
- 共享对象的版本基线不匹配，或重复事件尚未去重。
- 读取或迁移真实个人数据。
- 把外发草稿当作发送许可。

健康记录和私人通信另外停止于：权限隔离或驻留隔离尚未证实。

## 共同产物验收

产物落在该任务的写边界内。关键数字和来源可以引用。独立验收记录与实现记录分开。全部写入同一份最小任务证据包。

## 岗位卡

### 协调入口

- duty_id: `coordination-entry`
- layer: `L1`
- activation: `per_task_entry_slot`

#### 职责定义

默认的统一用户入口，也就是主入口。核对并汇总用户已经授予的授权，再编排这一次任务的入口、执行和独立验收。权限颁发不属于本职责。执行者无法完成或需要协作时回到主入口。路由建议在这里汇合，建议本身不是派单。单入口可以编排并行工作，并行不另开派单。云端入口不是本地隔离。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务已有的授权摘要、计划和不含真实个人数据的证据状态 |
| 写边界 | 编排记录、授权核对记录、回交记录 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 编排记录进入同一份证据包；计划的独立审查不由编写者兼任 |

### 战略计划

- duty_id: `strategic-planning`
- layer: `L1`
- activation: `map_only`

#### 职责定义

把目标整理成范围、非目标、完成标准和取舍建议。首轮只保留定义，不单独激活。不派单，不颁发权限。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 任务目标与约束的公开说明 |
| 写边界 | 无。首轮不激活 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 定义上的产物是计划建议，不是派单 |

### 资源成本顾问

- duty_id: `resource-cost-advisor`
- layer: `L1`
- activation: `map_only`

#### 职责定义

对资源、费用上限和替代路径提出建议。路由建议可以引用本定义来推荐执行职责、独立复验和备用。建议不是派单，也不是费用批准。未实测的数值不写成政策。首轮不单独激活。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 任务范围，以及仍为空的费用栏 |
| 写边界 | 无。首轮不激活 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 建议要能追溯到任务范围；空白费用栏保持空白 |

### 项目经理

- duty_id: `project-manager`
- layer: `L1`
- activation: `map_only`

#### 职责定义

整理进度、依赖、风险，并提出账本更新建议。建议写回同一份最小任务证据包，不另立三套账。不能第二派单。静态描述与 PM 跟踪相一致；功能未验证。首轮不单独激活。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 同一证据包中的进度事实 |
| 写边界 | 无。首轮不激活 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 定义上的产物是进度、依赖、风险和账本更新建议 |

### 反调独立质检

- duty_id: `independent-quality`
- layer: `L1`
- activation: `per_task_independent_acceptance_slot`

#### 职责定义

对计划做独立审查，对结果做独立验收。两次记录使用同一个职责槽。同一产物的实现者不兼任审查或验收。换一个模型不是充分的独立性保证。同一个模型的独立会话不因此自动无效。技术验收不代替用户接受或动作授权。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 计划、产物和证据包 |
| 写边界 | 独立审查记录和独立验收记录 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 审查者与实现者在记录上分开；不改写实现产物 |

### 工程研发

- duty_id: `engineering`
- layer: `L2`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

在任务写边界内完成工程产物，并留下可复核记录。只有被选为该任务的唯一执行槽时才激活。无法完成或需要协作时回到协调入口。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的工程产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收 |

### 产品 UI

- duty_id: `product-ui`
- layer: `L2`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

负责产品界面与交互产物。执行槽规则与工程研发相同。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的界面产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收 |

### 建模数据

- duty_id: `modeling-data`
- layer: `L2`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

负责建模和数据产物。本目录不记录真实模型、数据集或账号。工具可以替换；能力未验证前不算可用。执行槽规则与工程研发相同。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的建模或数据产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收；未验证的能力保持未验证 |

### 多媒介内容

- duty_id: `multimedia-content`
- layer: `L2`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

负责多媒介内容产物。对外内容默认是待用户审阅的草稿。执行槽规则与工程研发相同。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的内容产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收；外发停留在草稿 |

### 研究资料

- duty_id: `research-materials`
- layer: `L2`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

整理研究资料。关键数字带来源。执行槽规则与工程研发相同。证据模板把本职责放在执行槽，只为展示字段，不表示已经执行。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的资料产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收；关键数字有来源 |

### 知识管理

- duty_id: `knowledge-management`
- layer: `L3`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

管理知识候选。候选被复用之前，不把本职责写成已经产生复利。可以被选为执行槽。静态回读没有授予私域数据访问。外部新建的知识生活岗位不与本职责一一对应，也不证明本职责功能可用。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的知识候选 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收；不读取、不迁移真实个人数据 |

### 生活记录

- duty_id: `life-records`
- layer: `L3`
- activation: `per_task_execution_slot_when_selected`

#### 职责定义

生活记录的公开职责定义。可以被选为执行槽。本目录没有真实生活记录。私域数据访问未被授予。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 该任务明确给出、且不含真实个人数据的输入 |
| 写边界 | 该任务指定的生活记录模板产物 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件 |
| 产物验收 | 使用共同产物验收；不读取、不迁移真实个人数据 |

### 健康记录

- duty_id: `health-records`
- layer: `L3`
- activation: `blocked_until_isolation_proven`

#### 职责定义

地图保留本职责。权限隔离与驻留隔离都得到证实之前，不可激活。静态回读确认私域未获数据访问权限；这不是技术 ACL 或驻留隔离的功能验证。云端入口不是本地隔离。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 无 |
| 写边界 | 无 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件；隔离未证实时保持不可激活 |
| 产物验收 | 不可激活，因此没有产物 |

### 私人通信

- duty_id: `private-communications`
- layer: `L3`
- activation: `blocked_until_isolation_proven`

#### 职责定义

地图保留本职责。不可激活的条件与健康记录相同。外发默认是用户审阅草稿；走完工作流不是发送许可。不读取、不迁移真实通信。

#### 激活配置

| 项 | 模板值 |
|---|---|
| 工具身份 | 未指定 |
| 读边界 | 无 |
| 写边界 | 无 |
| 预算 | 未指定 |
| 停止 | 使用共同停止条件；隔离未证实时保持不可激活 |
| 产物验收 | 不可激活，因此没有产物 |
