# Scientific Hypothesis Frontend Integration Plan

> **For Codex:** REQUIRED SKILL: Use subagent-driven-development to execute this plan task-by-task. Apply systematic-debugging and test-driven-development to every behavior change, and verification-before-completion before claiming success.

**Goal:** Make the current Jinwu WebUI reliably runnable on Windows and expose the validated Scientific Hypothesis Agent 1.0 workflow through `solar-hypothesis`, with before/after frontend evidence and a reviewable pull request.

**Architecture:** Keep the existing session-local LangChain bridge and closed-contract specialist boundary. Fix blocking detection at the workspace-cache source instead of globally disabling LangGraph safeguards. Extend only `solar-hypothesis` with deterministic upstream inspection and optional ranking; keep teammate-owned `solar-evidence` unchanged. Port standalone quality gates selectively so Jinwu-specific behavior remains intact.

**Tech Stack:** Python 3.11+, uv, pytest, BlockBuster, LangChain/LangGraph, YAML sub-agent configuration, React WebUI, npm, GitHub CLI.

---

## Success criteria and evidence layers

1. **Static/code:** Ruff and formatting checks pass for changed Python files; WebUI production build passes.
2. **Automated behavior:** Targeted regression tests and the full backend test suite pass.
3. **Real runtime:** Backend and frontend start on ports 6174 and 4716 without globally enabling blocking filesystem operations.
4. **Real Agent execution:** The same frozen hypothesis prompt is submitted through the WebUI before and after the hypothesis changes; transcript, tool calls, run id, and artifact paths are retained.
5. **Scientific quality:** A fixed rubric compares grounding, candidate distinction, falsifiability, next-test discrimination, uncertainty honesty, and artifact completeness. This is an evaluation result, not a claim of scientific truth.
6. **Delivery:** Changes are committed on `fix/hypothesis-frontend-integration`, pushed to a branch, and submitted as a pull request; `main` is not pushed directly.

## Task 1: Prove and fix the Windows async workspace blocker

**Files:**
- Modify: `tests/test_workspaces.py`
- Modify only if the regression proves necessary: `tests/test_backends.py`
- Modify: `jw/workspaces.py`
- Modify only if later blocking traces prove necessary: `jw/agent.py`, `jw/backends.py`

**Step 1: Write the failing regression test**

Add a BlockBuster-backed test that warms a workspace binding and calls the cached lookup from an async context. Assert that no `os.getcwd`, `Path.resolve`, directory scan, or other blocking filesystem call occurs on the event-loop thread.

**Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_workspaces.py -k "cached_binding and nonblocking" -vv
```

Expected: FAIL on current `origin/main`, with the blocking call trace pointing at `_binding_cache_key`.

**Step 3: Implement the smallest source fix**

Replace filesystem-resolving cache-key construction with purely lexical normalization:

- accept `PathLike` via `os.fspath`;
- require an absolute workspace path at the cache boundary;
- normalize with `os.path.normpath`;
- keep existing cache identity and session scoping.

Do not add global `--allow-blocking`.

**Step 4: Verify GREEN and nearby behavior**

Run:

```bash
uv run pytest tests/test_workspaces.py tests/test_backends.py -vv
```

If a new blocking trace appears outside `_binding_cache_key`, add a focused failing test before changing that source.

## Task 2: Establish the clean WebUI baseline

**Files:**
- Add: `docs/evals/hypothesis-frontend/2026-07-26-baseline-prompt.md`
- Add: `docs/evals/hypothesis-frontend/2026-07-26-rubric.md`
- Add: `docs/evals/hypothesis-frontend/2026-07-26-baseline-result.md`
- Add only sanitized, non-secret artifacts under: `docs/evals/hypothesis-frontend/artifacts/baseline/`

**Step 1: Install and verify the clean baseline**

Run:

```bash
uv sync
uv run ruff check --select F .
uv run ruff format --check jw tests
uv run pytest
npm --prefix webui install
npm --prefix webui run build
```

Record any repository-baseline failure separately from new regressions.

**Step 2: Start the real application**

Start backend on 6174 and frontend on 4716 using the repository-supported commands and inherited local provider configuration. Never print or persist credential values.

**Step 3: Freeze one discriminating prompt and rubric**

Use a prompt that supplies:

- a concrete research question;
- at least one inspectable upstream experiment artifact;
- competing mechanisms;
- a request for evidence-grounded candidates, ranking, falsification conditions, and the next discriminating experiment.

Freeze a rubric before inspecting the answer.

**Step 4: Execute through the frontend**

Use the WebUI as a user would. Save a sanitized transcript, observed tool calls, run id, output paths, and rubric score. If current `solar-hypothesis` cannot inspect or rank, record that as baseline evidence rather than manually compensating.

## Task 3: Expose deterministic upstream inspection and optional ranking

**Files:**
- Modify: `jw/tools/scientific_hypothesis.py`
- Modify: `jw/middleware/contract_tool_allowlist.py`
- Modify: `jw/subagents/solar/solar_hypothesis.yaml`
- Test: `tests/test_hypothesis.py`
- Test as needed: `tests/test_contract_tool_allowlist.py`

**Step 1: Write failing bridge tests**

Cover:

- `scientific_hypothesis_inspect_upstream` resolves only scoped project/workspace paths and returns `inspect_experiment_run` output;
- ranking is rejected before a hypotheses-ready response has passed preflight;
- a valid ranking is cached with its response hash;
- freeze rejects stale ranking/response state and includes a valid ranking;
- only `solar-hypothesis` receives the two new tools.

**Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_hypothesis.py tests/test_contract_tool_allowlist.py -k "upstream or ranking or solar_hypothesis" -vv
```

Expected: FAIL because the bridge tools and allowlist entries do not yet exist.

**Step 3: Implement the bridge**

Add:

- `scientific_hypothesis_inspect_upstream`;
- `scientific_hypothesis_rank`;
- ranking state and response-hash binding;
- freeze handoff of the validated ranking;
- YAML prompt/tool-list and closed allowlist updates.

Do not expose generic filesystem or shell tools. Do not modify `solar-evidence`.

**Step 4: Verify GREEN**

Run the targeted test command again, then:

```bash
uv run pytest tests/test_hypothesis.py tests/test_contract_tool_allowlist.py -vv
```

## Task 4: Port only validated Scientific Hypothesis 1.0 quality gates

**Files:**
- Modify: `src/scientific_hypothesis/contracts.py`
- Modify: `src/scientific_hypothesis/harness.py`
- Modify: `src/scientific_hypothesis/ranking.py`
- Test: `hypothesis/tests/test_contracts.py`
- Test: `hypothesis/tests/test_harness.py`
- Test: `hypothesis/tests/test_hypothesis_edge_cases.py`
- Test: `hypothesis/tests/test_ranking.py`
- Test: `hypothesis/tests/test_upstream.py`
- Preserve: Jinwu-specific percentage and knowledge-base grounding behavior

**Step 1: Add one failing test group at a time**

Port and adapt standalone regression coverage for:

- evidence excerpts materially matching registered upstream material;
- medium/high confidence evidence and literature grounding gates;
- vacuous applicability, placeholder premises, and placeholder falsification conditions;
- aggregated ranking errors;
- ranking rubric round-trip and freeze integrity.

**Step 2: Verify each group RED before production edits**

Run the narrowest relevant test file and selector. Confirm the failure is the intended missing behavior, not fixture drift.

**Step 3: Implement minimally and preserve Jinwu additions**

Manually merge behavior; do not copy entire files. Preserve existing percentage-expression checks and knowledge-base fail-open warnings unless a focused test proves them wrong.

**Step 4: Verify each group GREEN**

Run the narrow test after each change, then:

```bash
uv run pytest hypothesis/tests tests/test_hypothesis.py -vv
```

## Task 5: Align the specialist operating guidance

**Files:**
- Modify: `jw/subagents/solar/solar_hypothesis.yaml`
- Add: `jw/subagents/solar/skills/scientific-hypothesis/SKILL.md`
- Add: `jw/subagents/solar/skills/scientific-hypothesis/references/工作模式与完成标准.md`
- Modify: `jw/subagents/solar/bundle.yaml`
- Test: bundle/skill discovery tests under `tests/`

**Step 1: Write a failing discovery/contract test**

Assert that the solar bundle exposes one dedicated scientific-hypothesis skill and that its guidance names the closed workflow: bind, inspect, bind evidence, validate, optionally rank, freeze.

**Step 2: Add the smallest curated skill**

Consolidate the validated standalone guidance into one packaged skill plus one reference. Avoid shipping nine overlapping instruction fragments. Keep stable tool names in English and human-facing guidance in Chinese.

**Step 3: Verify discovery and specialist tests**

Run the targeted bundle/skill tests and `tests/test_hypothesis.py`.

## Task 6: Run the same frontend task after the change

**Files:**
- Add: `docs/evals/hypothesis-frontend/2026-07-26-post-change-result.md`
- Add only sanitized artifacts under: `docs/evals/hypothesis-frontend/artifacts/post-change/`
- Modify: `docs/evals/hypothesis-frontend/2026-07-26-rubric.md`

**Step 1: Restart from a clean runtime**

Start backend and frontend using the same provider/model configuration and ports as the baseline.

**Step 2: Re-run the frozen prompt through the WebUI**

Do not tune the prompt after seeing the result. Record the same evidence fields as the baseline.

**Step 3: Score and compare**

Report per-dimension scores, contract repair count, tool path, run status, and artifact completeness. Distinguish deterministic correctness from model quality and scientific validity.

## Task 7: Full verification, review, and pull request

**Files:**
- Modify as needed: `docs/evals/hypothesis-frontend/2026-07-26-post-change-result.md`

**Step 1: Run full fresh verification**

```bash
uv run ruff check --select F .
uv run ruff format --check jw tests
uv run pytest
npm --prefix webui run build
git diff --check
git status --short
```

**Step 2: Review against scope and plan**

Check that:

- no secret or local credential/config value is tracked;
- no global blocking safeguard is disabled;
- no teammate-owned Agent behavior changed;
- no retired root/frontend path was revived;
- evaluation claims match saved evidence.

**Step 3: Commit and deliver through PR**

Use repository identity `Z Lee <229420557+oswaldobachman282180vke-alt@users.noreply.github.com>`, push only `fix/hypothesis-frontend-integration`, and create a PR with verification results and remaining evidence boundaries.
