# B3 Main Acceptance Realignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the Cycle 26 evidence-maturity and mechanism question as the sole primary scientific acceptance task, while retaining the polar-length interaction question as an engineering stress benchmark.

**Architecture:** Keep all existing case IDs and historical run artifacts stable. Change only the evaluation-role metadata, tests, and current reader-facing interpretation; then run one fresh production WebUI primary acceptance after the temporary provider is verified.

**Tech Stack:** JSON evaluation suites, Python/pytest, Node.js WebUI harness, Markdown documentation, GitHub Draft PR #31.

## Global Constraints

- Preserve all r10-r52 run artifacts and untracked logs.
- Do not expose credentials or place them in the repository, command arguments, logs, or chat.
- Keep automated tests, real execution, model calls, and scientific validation as separate evidence layers.
- Do not mark PR #31 ready or merge it without a later explicit instruction.

---

### Task 1: Freeze the evaluation-role contract

**Files:**
- Modify: `tests/test_webui_eval_harness.py`
- Modify: `research/review/evals/main_task_frontend_v1.json`
- Modify: `research/review/evals/next_stage_closed_loop_frontend_v1.json`

- [x] Write assertions that make `MAIN-SC26-B06` the sole primary scientific acceptance case.
- [x] Run the focused tests and confirm they fail against the old role metadata.
- [x] Add explicit visible-suite and release-gate metadata without changing case IDs or prompts.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Align current reader-facing interpretation

**Files:**
- Modify: `docs/B3科研范围与真实前端测试案例审计_2026-08-20.md`
- Modify: `docs/真实前端科学问题与闭环结果.md`
- Modify: `research/review/README.md`

- [x] State the four-level hierarchy: official B3 topic, Cycle 26 primary task, visible capability/stress cases, and external hidden evaluation.
- [x] Preserve historical run wording while adding a current interpretation that the polar-length runs are engineering stress evidence.
- [x] Review all changed visible text for evidence status and internal-process leakage.

### Task 3: Verify and update Draft PR #31

- [x] Run focused and full pytest, Ruff, WebUI tests, production build, and `git diff --check`.
- [x] Run the reader-facing visible-text advisory scanner and review findings semantically.
- [ ] Commit only tracked task files and push the current branch to Draft PR #31.
- [ ] Confirm all GitHub CI checks complete successfully; leave the PR in Draft state.

### Task 4: Verify the temporary provider and run primary acceptance

- [x] After the user confirms hidden key injection, verify only config presence, endpoint, permissions, and error types without printing the key.
- [x] Run minimal real calls for the Qwen producer and auxiliary models through the temporary OpenAI-compatible endpoint.
- [x] Start one fresh headed production WebUI task using only the `MAIN-SC26-B06` prompt and record its observer URL immediately.
- [x] Accept negative, insufficient-evidence, or do-not-launch scientific outcomes; require complete artifacts and review records for an engineering pass.
- [x] Run the visible transfer benchmark once and reserve scientific generalization claims for an external sealed suite.

Execution result: the primary run reached an accepted experiment design but stopped before its first experiment attempt,
so Experiment Result was blocked and no final release was produced. The visible transfer run reached an accepted
hypothesis and then ended on two consecutive provider stream disconnects during experiment-design generation. Both are
preserved as negative runtime evidence; neither is an engineering pass or a scientific-generalization result.
