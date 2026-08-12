# MVP 2C Proactive Intent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** 将受限队列处理纳入 Codex 的正常工程请求启动循环，且不扩大浏览器或后台权限。

**Architecture:** 仅修改项目 Skill、使用手册和文档合同测试；运行时仍调用已验收的 MVP 2B CLI。

**Tech Stack:** Markdown、Python unittest。

---

### Task 1: 主动循环合同测试

- Modify: `tests/test_skill_contract.py`
- [ ] 先写失败测试：断言 Skill 明确包含 `process-pending-intents --limit 10`、严格只读与 Dashboard 豁免、非零退出停止写入。
- [ ] 运行 `python3 -m unittest tests/test_skill_contract.py -q`，确认缺少合同文本。

### Task 2: Skill 与用户手册

- Modify: `.agents/skills/ai-software-engineering-team/SKILL.md`
- Modify: `USER_OPERATING_GUIDE.md`
- [ ] 在健康检查和初始化之后加入一次受限队列循环；只记录单条业务结果，命令失败停止写入。
- [ ] 更新操作说明；不得增加浏览器处理或后台调度。
- [ ] 重跑 Skill 合同测试，提交实现。

### Task 3: 验收与整合

- Create: `artifacts/dispatches/20260812-006/verification.md`
- Modify: `handoff.md`
- [ ] 运行差异检查、两套 Python 全量测试、健康检查和 Claude V4 focused review。
- [ ] 保存安全 receipt、验证记录，no-ff 整合 main 后再次验证。
