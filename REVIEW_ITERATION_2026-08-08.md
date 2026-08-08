# AI Software Engineering Team：2026-08-08 审核迭代记录

## 结论

状态：`MODIFY`

团队治理文档可以继续使用，但必须把 Claude 的独立质量门、Codex/Nick 的最终工程决策、GLM 5.2 的应急路由和 K3 的实验边界写成一致契约。

## 证据

- 当前 CodeBuddy CLI 版本为 `2.128.0`，本机帮助列出 `glm-5.2` 和 `kimi-k3-2`，不列 `kimi-k3-1`。
- K3-1 运行日志出现“没有 `maxOutputTokens`，请求不带 `max_tokens`”以及 Extended Thinking/high effort 证据；实际审阅曾出现数分钟和百万字节级持续流。
- GLM 5.2 单次真实应急审阅耗时约 `71.58s`，生成完整约 `4KB` 报告，结果为 `PASS_WITH_WARNINGS`。
- Claude 主审阅器本轮最小文档审阅未在约三分钟内返回完整报告，终止后只留下 `Execution error`，因此本轮不视为 Claude 验收通过。
- CodeBuddy 两次受控尝试均按根目录契约拒绝：当前没有“Claude 限额后 + Human 明确 `yes`”的应急授权，因此没有把 CodeBuddy 结果当作本轮正式 Review。

## 已采取的修改

1. Claude 在团队文档中改为 `Principal Reviewer / Independent Quality Gate`，不拥有替代 Codex/Nick 最终工程决定的权力。
2. CodeBuddy GLM 5.2 明确为 Claude 限额后的应急 Reviewer，必须经过 Human 明确 `yes`，不自动并行、不静默替换。
3. CodeBuddy K3 改为实验性 Agent，不进入默认降级路径。
4. `scripts/claude-verify.sh` 增加对 `AI-Software-Engineering-Team/*.md` 的受控正文注入，避免审阅器只看到文件名。
5. `tests/test_codex_claude_verify.sh` 增加团队治理文档正文注入回归测试。
6. 增加项目文档范围参数；定向注入团队目录后，当前上下文从约 `141KB` 降至约 `73KB`，且会排除无关旧项目规格。

## 未决风险

- 本记录不是 Claude 最终验收报告；涉及高风险实现或发布仍须等待 Claude 或取得额外人工复核。
- GLM 5.2 的单次试跑证明运行可用性改善，不足以证明所有审阅质量优于 K3。
- 全局 `codex-claude-verify` 仍依赖 personal-chief-of-staff wrapper；两个 wrapper 的契约需要持续同步。
