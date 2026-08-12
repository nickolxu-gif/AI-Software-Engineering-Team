# Dispatch Record: 20260813-010

## L2 CodeBuddy V4.10 Project Adapter

- **Owner:** Codex
- **Execution location:** current isolated Worktree `agent/Codex/20260812-008-task-intake`
- **Base candidate:** `b16a2a7a7fd5c8f16dd9a66809a692dcedf988f2`
- **Risk:** L2 — local verifier adapter and bounded provider invocation; no production, remote, GitHub, or permission change.
- **Dependency:** The existing MVP 2D candidate remains blocked until its Claude quota failure is resolved or this explicitly authorized emergency review completes.

## Seven questions

1. **Goal and type:** Provide this project with a local CodeBuddy/GLM-5.2 V4.10.3 adapter so the already-authorized emergency review can use the global immutable-packet contract.
2. **Risk and acceptance:** L2. Acceptance requires a tested wrapper, pinned global-core hashes, deterministic packet preflight, strict single-result parsing, sanitized receipt, local full regression, and no scope expansion.
3. **Execution and review:** Codex implements. Independent review is the CodeBuddy emergency review only after the current Claude `quota_or_rate_limit` evidence and this user’s authorization. No automatic third provider.
4. **Context and isolation:** Use the current isolated Worktree and only the listed adapter/test/dispatch files. Keep the current candidate’s untracked `reports/` evidence untouched.
5. **Model route:** CodeBuddy with GLM-5.2 through the V4.10.3 global packet and stream-runner core; no K3, no model configuration change.
6. **Completion standard:** Wrapper passes contract tests and shell syntax; the target delta receives one parseable V4 result; all local tests pass. A warning, modification request, blocked transport, quota failure, or malformed result is not acceptance.
7. **Failure and knowledge handling:** Keep the candidate blocked on any non-PASS result. Record only safe receipt metadata and fixed reason codes. No Mimo or external knowledge writeback is required for this L2 adapter.

## Explicit boundaries

- No GitHub remote, push, pull request, release, deployment, production access, permission expansion, global Skill/config modification, credential inspection, or direct CodeBuddy CLI bypass.
- The adapter may reference the approved global V4.10.3 core by fixed path and SHA; it does not mutate the Hub checkout or global Skill.
- Provider output is accepted only through the wrapper’s strict V4 validator and sanitized receipt.

## Execution evidence

- Adapter commit: `55c195e4116bdf7264d19641c5dc1b39d4d3e91f`.
- Contract checks: 3 adapter tests, `bash -n`, and `git diff --check` passed before provider start.
- Provider: CodeBuddy / GLM-5.2 through the project adapter; `provider_started=true`, `event_count=5`, `final_result_seen=true`, `verdict_parse=pass`.
- Packet: 27,280 bytes, fingerprint `6f3de2128a2b22e1600162a5b16bc77c82fb8bee981878f2cc008ae13bb4f499`.
- Safe receipt: `.review-evidence/receipts/6f3de2128a2b22e1600162a5b16bc77c82fb8bee981878f2cc008ae13bb4f499.json`.
- Report: `reports/codebuddy-verifier-20260813-010.md`.
- Result: `PASS_WITH_WARNINGS`; this is not strict acceptance. The original MVP 2D candidate remains blocked and no merge is authorized by this result.
