# AI Software Engineering Team System
## Project Specification v0.1

## 1. 项目背景

本项目目标是建立一个个人 AI 软件工程团队体系。

系统不是单一 Coding Agent，而是一个由多个 AI Agent 组成的软件工程组织。

核心理念：

- 人类作为最终决策者；
- Hermes 作为个人 AI 幕僚长，负责跨领域协调；
- Codex 作为软件工程领域 AI CTO，负责全部技术决策；
- 多模型 Agent 作为不同专业角色，提供开发、审查、测试和研究能力。

---

# 2. 组织架构

```
Human Owner
    |
    |
Hermes
(Personal AI Chief of Staff)
    |
    |
Software Engineering Organization
    |
    |
Codex
(AI CTO)
    |
    ├── Architect Agent
    ├── Developer Agent
    ├── Debug Agent
    ├── Reviewer Agent
    └── Tester Agent
```

---

# 3. 角色定义

## 3.1 Human Owner

职责：

- 定义目标；
- 提供业务需求；
- 决定最终方向。

拥有最终决策权。

---

## 3.2 Hermes

定位：

Personal AI Chief of Staff。

职责：

- 管理个人 AI 工作体系；
- 跨领域任务协调；
- 项目状态同步；
- 信息汇总；
- Agent资源协调。

限制：

Hermes 不直接干预软件工程技术决策。

---

## 3.3 Codex

定位：

Software Engineering CTO。

职责：

- 技术方案设计；
- 架构决策；
- 代码实现规划；
- Agent调用；
- 工程流程管理。

Codex 在已授权的软件工程范围内拥有技术协调和工程决策权；涉及战略、高风险、不可逆或外部发布事项时，仍须 Human 最终授权。

---

# 4. 软件工程核心流程

```
Requirement
      |
      ↓
Codex Analysis
      |
      ↓
Architecture Design
      |
      ↓
Implementation
      |
      ↓
Code Review
      |
      ↓
Testing
      |
      ↓
Final Approval
```

---

# 5. Agent角色设计

## 5.1 Architect Agent

职责：

- 系统设计；
- 技术选型；
- 架构评估；
- 长期演进规划。


## 5.2 Developer Agent

职责：

- 编码；
- 重构；
- 功能实现；
- Bug修复。


## 5.3 Reviewer Agent

职责：

- 代码质量检查；
- 架构风险发现；
- 逻辑错误识别；
- 安全检查。


## 5.4 Tester Agent

职责：

- 测试设计；
- 自动化测试；
- Regression检查。


---

# 6. 模型角色分配

## Codex

主要工程执行者。

负责：

- 架构；
- 开发；
- 工程管理。


## Claude Code

定位：

Principal Reviewer / Independent Quality Gate。

负责：

- 高风险架构审查；
- 技术路线挑战；
- 最终质量审核。

Claude Code 提出 `ACCEPT / MODIFY / BLOCK`，不替代 Human 的战略授权；最终工程决定由 Codex/Nick 根据证据作出。


## MiMo

定位：

General Software Engineer。

负责：

- 独立开发；
- 第二技术方案；
- 综合代码审查。


## DeepSeek

定位：

Code Auditor。

负责：

- Bug发现；
- 逻辑审查；
- 边界条件分析。


## MiniMax

定位：

Execution Agent。

负责：

- 长流程任务；
- 测试辅助；
- 文档整理。


## Qwen

定位：

Chinese Engineering Assistant。

负责：

- 中文需求理解；
- 中文技术资料；
- 中文文档。


## Local Model

定位：

Private Engineer。

负责：

- 敏感代码；
- 私密资料；
- 离线环境。


## CodeBuddy GLM 5.2

定位：

Claude 限额后的应急 Reviewer。

限制：

- 只有 Claude 报告限额/配额失败且 Human 明确回复 `yes` 后才可调用；
- 只提供独立审阅意见，不继承 Claude 的最终验收权；
- 使用受控、最小必要的审阅上下文。


## CodeBuddy K3

定位：

实验性补充 Agent。

限制：

- 不进入默认 Reviewer 或自动降级路径；
- 只有在模型 ID、额度和调用参数可验证且 Codex 明确派活后，才可执行局部低风险任务；
- 不得自行放行高风险任务。


---

# 7. Review机制

CodeBuddy GLM 5.2 不是默认并行 Reviewer。只有 Claude Code 报告限额/配额失败，且 Human 明确回复 `yes` 后，才作为 CodeBuddy 应急 Reviewer 使用。K3 保留为实验性 Agent，不进入自动降级路径。

## Level 1：普通修改

流程：

```
Codex
 ↓
DeepSeek / MiMo Review
 ↓
PASS
```

适用于：

- 小功能；
- Bug修复；
- 文档调整。


---

## Level 2：模块级变化

流程：

```
Codex
 ↓
MiMo Review
 ↓
DeepSeek Audit
 ↓
PASS
```

适用于：

- 新模块；
- API变化；
- 数据结构调整。


---

## Level 3：架构级变化

流程：

```
Codex
 ↓
Claude Code Review
 ↓
MiMo / DeepSeek Second Opinion
 ↓
PASS
```

适用于：

- 架构重构；
- 核心系统修改；
- 高风险变化。

如果 Claude Code 不可用，默认等待或将结果标记为“待验收候选”。只有在 Human 明确批准降级后，才可用 CodeBuddy GLM 5.2 完成补充审查，并增加测试或人工复核；这不等价于 Claude Code 最终验收。K3 只能作为单独实验，不得静默替换。


---

# 8. Codex任务决策框架（初版）

Codex接收到任务后，需要回答：

## Q1：任务目标是什么？

分类：

- 新功能；
- Bug修复；
- 优化；
- 重构；
- 技术研究。


## Q2：任务复杂度？

等级：

L1：
简单修改。

L2：
模块级任务。

L3：
系统级任务。


## Q3：是否需要拆分？

判断：

- 单Agent完成；
- 多Agent协作。


## Q4：需要哪些角色？

选择：

- Architect；
- Developer；
- Reviewer；
- Tester；
- Researcher。


## Q5：调用哪个模型？

依据：

- 技术复杂度；
- 数据敏感度；
- 风险等级。


## Q6：完成标准是什么？

必须定义：

- 功能完成；
- 测试通过；
- Review通过；
- 文档更新。


## Q7：失败如何处理？

流程：

```
Review Fail
      |
      ↓
Return To Codex
      |
      ↓
Fix
      |
      ↓
Re-review
```

---

# 9. 系统设计原则

## 原则1

Codex是软件工程领域最高级技术Agent。

## 原则2

Hermes负责协调，不替代技术判断。

## 原则3

模型选择基于角色，不基于品牌。

## 原则4

保持模型供应链冗余。

## 原则5

高风险任务必须经过独立审查。

---

# 10. 后续开发目标

Phase 1：

建立 Codex 软件工程 SOP。

Phase 2：

建立 Agent Router。

Phase 3：

实现自动 Review Pipeline。

Phase 4：

扩展成为个人 AI Engineering Organization。

---

End.
