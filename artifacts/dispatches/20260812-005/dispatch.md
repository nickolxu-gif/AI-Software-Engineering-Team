# Dispatch 20260812-005：MVP 2B Codex 受控意图队列

## Q1：目标、范围与完成标准

将 MVP 2A 的单条 `process-intent` 升级为 Codex 可主动调用的有限批次队列处理：读取最早的 `PENDING` 意图，逐条复用既有完整核验链路，并输出安全汇总。工作台总览显示待处理意图数量。

非目标：后台守护进程、浏览器自动处理、Git/Agent/发布动作、审批批准或 nonce 消费、远程访问、GitHub。

完成标准：批次上限、稳定顺序、空队列、单条失败后继续、公开字段白名单和总览计数均有自动化回归；两套 Python 测试、健康检查和最终独立验收通过。

## Q2：风险、授权与验收

L2。Human 已授权 Codex 自主推进既定 MVP 路线。批次只处理 Human 已在本机工作台提交的三类低范围意图；原有 HEAD、状态、审批与 prepared-operation 门禁不变。任何 GitHub、远程、权限扩大、审批消费或发布仍需新授权。

## Q3：执行与审阅

Codex 是唯一写入者、队列执行者和 main 整合者。Claude Code 是最终独立 Reviewer；无法得到有效 `PASS` 时保持候选未整合，绝不自动 fallback。

## Q4：隔离与上下文

- 基线：`5b88730`（本地 main）。
- 分支：`agent/codex/20260812-005-mvp2b-intent-queue`。
- Worktree：`.worktrees/20260812-005-codex-mvp2b-intent-queue`。
- 范围：`team_control/`、工作台、测试、使用指南、handoff 和本任务工件。

## Q5：状态与纠偏

批处理不绕过单条 `IntentService.process`；每条意图仍独立获取控制锁并按既有状态机终结。预期业务拒绝或阻塞写入该意图终态后继续下一条；未预期异常停止批次并保留已完成的审计事实。

## Q6：验收

只接受有精确 scope acknowledgement 的 Claude V4 `PASS` 作为最终独立验收。`PASS_WITH_WARNINGS`、`BLOCKED`、空输出和 transport 探针都不是通过。

## Q7：知识回流

记录队列语义、验证证据和可复用验收经验。MiMo 没有可用独立入口时，明确标记未执行，不伪造结论。
