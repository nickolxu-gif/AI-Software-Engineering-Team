# Hermes Review Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 建立全局 hermes-review-verify，以隔离 Hermes Profile 为任意已注册模型提供一次、无工具、严格 JSON 的候补代码审阅。

**Architecture:** Skill 负责授权路由；Python 运行器生成和校验 V4 packet、检查隔离 Profile、固定本次 provider/model 和 fingerprint；Hermes 仅作为 one-shot 推理宿主。模型与凭据仅由 Hermes Profile 管理。

**Tech Stack:** Python 3 stdlib、unittest、Git、Hermes CLI。

---

## 文件布局

- ~/.codex/skills/hermes-review-verify/SKILL.md：全局触发条件、授权门和候补审阅边界。
- ~/.codex/skills/hermes-review-verify/agents/openai.yaml：Skill UI 元数据。
- ~/.codex/skills/hermes-review-verify/scripts/review_contract.py：V4 schema、fingerprint 和安全 receipt。
- ~/.codex/skills/hermes-review-verify/scripts/hermes_review_verify.py：profile preflight、一次调用、验签和报告。
- ~/.codex/skills/hermes-review-verify/scripts/install_review_profile.py：只创建空 Profile；不复制日常配置、技能、记忆或凭据。
- ~/.codex/skills/hermes-review-verify/tests/test_review_contract.py：合约测试。
- ~/.codex/skills/hermes-review-verify/tests/test_hermes_review_verify.py：fake Hermes 集成测试。
- ~/.codex/skills/hermes-review-verify/tests/test_skill_policy.py：Skill 策略测试。

### Task 1: 建立可测试的 V4 合约

**Files:**

- Create: scripts/review_contract.py
- Create: tests/test_review_contract.py

- [ ] Step 1: Write the failing test

    def test_validate_verdict_accepts_only_v4_shape():
        assert validate_verdict(valid_verdict)["verdict"] == "PASS"
        with self.assertRaises(ContractError):
            validate_verdict({"verdict": "PASS", "findings": "none"})

    def test_fingerprint_changes_when_model_changes():
        assert fingerprint("alibaba/qwen3.7-max", "packet-a") != fingerprint("other/model", "packet-a")

- [ ] Step 2: Run test to verify it fails

Run: python3 -m unittest tests.test_review_contract -v

Expected: FAIL because review_contract does not exist.

- [ ] Step 3: Write minimal implementation

    def validate_verdict(value: object) -> dict[str, object]:
        # Require verdict, findings, and scope_ack.

    def fingerprint(model: str, packet_digest: str) -> str:
        return hashlib.sha256(f"{RUNNER_VERSION}\0{model}\0{packet_digest}".encode()).hexdigest()

- [ ] Step 4: Run test to verify it passes

Run: python3 -m unittest tests.test_review_contract -v

Expected: PASS.

- [ ] Step 5: Commit

    git add scripts/review_contract.py tests/test_review_contract.py
    git commit -m "feat: add Hermes review contract"

### Task 2: Profile 安装与 preflight

**Files:**

- Create: scripts/install_review_profile.py
- Create: scripts/hermes_review_verify.py
- Create: tests/test_hermes_review_verify.py

- [ ] Step 1: Write the failing test

    def test_preflight_rejects_profile_with_fallbacks(self):
        result = run_fake_hermes(fallback_output="Fallback chain (1 entries):")
        self.assertEqual(result["reason_code"], "fallback_configured")

    def test_preflight_rejects_any_enabled_tool(self):
        result = run_fake_hermes(tools_output="enabled terminal")
        self.assertEqual(result["reason_code"], "tools_enabled")

    def test_installer_never_uses_clone_flags(self):
        self.assertEqual(
            build_install_command("hermes", "hermesreview"),
            ["hermes", "profile", "create", "hermesreview", "--no-alias", "--no-skills"],
        )

- [ ] Step 2: Run test to verify it fails

Run: python3 -m unittest tests.test_hermes_review_verify -v

Expected: FAIL because installer and runner do not exist.

- [ ] Step 3: Implement minimal preflight

    def build_install_command(hermes_bin: str, profile: str) -> list[str]:
        return [hermes_bin, "profile", "create", profile, "--no-alias", "--no-skills"]

    def preflight(command: list[str]) -> ReceiptFields:
        # Run only fallback list and tools list; reject nonzero, fallback, or enabled tools.

- [ ] Step 4: Run test to verify it passes

Run: python3 -m unittest tests.test_hermes_review_verify -v

Expected: PASS.

- [ ] Step 5: Commit

    git add scripts/install_review_profile.py scripts/hermes_review_verify.py tests/test_hermes_review_verify.py
    git commit -m "feat: preflight isolated Hermes review profile"

### Task 3: 单次调用与 fail-closed receipt

**Files:**

- Modify: scripts/hermes_review_verify.py
- Modify: tests/test_hermes_review_verify.py

- [ ] Step 1: Write the failing test

    def test_runner_uses_one_shot_with_model_and_ignore_rules(self):
        result = run_fake_hermes(final_stdout=json.dumps(valid_verdict))
        self.assertEqual(result["status"], "PASS")
        self.assertIn("-z", result["observed_argv"])
        self.assertIn("--ignore-rules", result["observed_argv"])

    def test_non_json_is_protocol_block_without_raw_output(self):
        result = run_fake_hermes(final_stdout="I found one issue")
        self.assertEqual(result["failure_class"], "P")
        self.assertNotIn("I found one issue", json.dumps(result))

- [ ] Step 2: Run test to verify it fails

Run: python3 -m unittest tests.test_hermes_review_verify -v

Expected: FAIL because no one-shot execution exists.

- [ ] Step 3: Implement minimal runner

    argv = [profile_binary, "-z", prompt, "--ignore-rules", "--model", model, "--provider", provider]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    # Parse only stdout as one JSON document; discard raw stdout/stderr after classification.

- [ ] Step 4: Run test to verify it passes

Run: python3 -m unittest tests.test_hermes_review_verify -v

Expected: PASS.

- [ ] Step 5: Commit

    git add scripts/hermes_review_verify.py tests/test_hermes_review_verify.py
    git commit -m "feat: add single-shot Hermes review runner"

### Task 4: 全局 Skill 与验收

**Files:**

- Create: SKILL.md
- Create: agents/openai.yaml
- Create: tests/test_skill_policy.py

- [ ] Step 1: Write the failing test

    def test_skill_requires_quota_evidence_and_current_yes(self):
        self.assertIn("quota_or_rate_limit", skill_text)
        self.assertIn("明确 yes", skill_text)

    def test_skill_never_routes_to_codebuddy_or_auto_fallback(self):
        self.assertIn("CodeBuddy 已封存", skill_text)
        self.assertIn("不得自动切换", skill_text)

- [ ] Step 2: Run test to verify it fails

Run: python3 -m unittest tests.test_skill_policy -v

Expected: FAIL because SKILL.md does not exist.

- [ ] Step 3: Implement concise Skill instructions

    ---
    name: hermes-review-verify
    description: Use when Claude Code has explicit quota or rate-limit evidence and the user has approved a one-time Hermes candidate code review.
    ---

    Run isolated-profile preflight before every review. Stop at BLOCKED for every preflight, transport, or JSON failure.

- [ ] Step 4: Run validation

Run: python3 -m unittest discover -s tests -v && python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py ~/.codex/skills/hermes-review-verify && git diff --check

Expected: PASS, valid Skill, no whitespace errors.

- [ ] Step 5: Create empty Profile only after local green

Run: python3 scripts/install_review_profile.py --profile hermesreview --apply

Expected: only an empty no-skill Profile is created; no cloning, provider call, credential read or config copy occurs.

- [ ] Step 6: Commit

    git add SKILL.md agents/openai.yaml scripts tests
    git commit -m "feat: add Hermes review verification skill"

## 自检结论

本计划覆盖独立 Profile、可配置模型、无工具、无 fallback、V4 packet/receipt、单次调用和候补地位。首次真实模型 canary 仍需单独的当次 yes，不属于本实施计划。

