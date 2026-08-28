import assert from "node:assert/strict";
import test from "node:test";

import {
  blockedResearchOutcome,
  classifyOutcome,
  isTerminalOutcome,
  recoveryAwareStageStopDecision,
  shouldReturnTerminalOutcome,
  shouldWaitForTransientModelRecovery,
} from "../../research/review/evals/terminal_outcome.mjs";
import {
  RECOVERY_LEDGER_STATES,
  currentRecoveryLedgerStorageKey,
  recoveryLedgerStorageKey,
  transitionRecoveryLedger,
} from "../src/lib/runRecovery.js";

test("does not count a research terminal protocol as a scientific answer", () => {
  const result = classifyOutcome(
    { status: "idle" },
    {
      values: {
        messages: [
          {
            type: "ai",
            content:
              "[RESEARCH REVIEW TERMINAL] status=blocked; " +
              "reason=REQUIRED_SPECIALIST_FAILED_TWICE.",
          },
        ],
      },
    },
    { status: "success" }
  );

  assert.equal(result.outcome, "research_blocked");
  assert.equal(result.terminal_status, "blocked");
  assert.equal(result.has_answer, false);
  assert.equal(result.assistant_answer_count, 0);
});

test("does not count a research blocked protocol as a scientific answer", () => {
  const result = classifyOutcome(
    { status: "idle" },
    {
      values: {
        messages: [
          {
            type: "ai",
            content:
              "[RESEARCH REVIEW BLOCKED] the final draft was empty and could not enter the release gate",
          },
        ],
      },
    },
    { status: "success" }
  );

  assert.equal(result.outcome, "research_blocked");
  assert.equal(result.terminal_status, "blocked");
  assert.equal(result.has_answer, false);
  assert.equal(result.assistant_answer_count, 0);
});

test("keeps ordinary assistant output as a completed answer", () => {
  const result = classifyOutcome(
    { status: "idle" },
    { values: { messages: [{ type: "ai", content: "科学假设结果" }] } },
    { status: "success" }
  );

  assert.equal(result.outcome, "completed_with_answer");
  assert.equal(result.has_answer, true);
});

test("treats a blocked research protocol as a polling terminal", () => {
  assert.equal(isTerminalOutcome("research_blocked"), true);
});

test("turns the persisted blocked review status into an immediate terminal", () => {
  const result = blockedResearchOutcome(
    {
      status: "blocked",
      currentStage: "data",
      terminal: {
        reasonCode: "REQUIRED_SPECIALIST_FAILED_TWICE",
        summary: "Evidence output could not be persisted.",
      },
    },
    { has_answer: false, assistant_answer_count: 0 }
  );

  assert.deepEqual(result, {
    outcome: "research_blocked",
    terminal_status: "blocked",
    has_answer: false,
    assistant_answer_count: 0,
    error_summary:
      "REQUIRED_SPECIALIST_FAILED_TWICE: Evidence output could not be persisted.",
    blocked_stage: "data",
  });
});

test("waits only for the same failure's explicitly pending recovery ledger", () => {
  const state = {
    next: ["model"],
    tasks: [
      {
        name: "model",
        error: "APIConnectionError('Connection error.')",
      },
    ],
    checkpoint: {
      checkpoint_id: "cp-1",
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [{ id: "u1", type: "human", content: "question" }],
    },
  };
  const runtimeError = { outcome: "runtime_error" };
  const pendingLedger = {
    version: 1,
    state: RECOVERY_LEDGER_STATES.SCHEDULED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: "cp-1",
    recoveryRunId: null,
  };
  const latestRun = { run_id: "run-1", status: "error" };

  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [latestRun],
      JSON.stringify(pendingLedger)
    ),
    true
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [latestRun],
      JSON.stringify(
        transitionRecoveryLedger(
          pendingLedger,
          RECOVERY_LEDGER_STATES.FAILED
        )
      )
    ),
    false
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [latestRun],
      JSON.stringify({ ...pendingLedger, checkpointId: "cp-other" })
    ),
    false
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [{ run_id: "run-other", status: "error" }],
      JSON.stringify(pendingLedger)
    ),
    false
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [latestRun],
      null
    ),
    false
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      { outcome: "completed_with_answer" },
      [latestRun],
      JSON.stringify(pendingLedger)
    ),
    true
  );
});

test("selects only the exact current-turn ledger key and rejects malformed pending data", () => {
  const state = {
    next: ["model"],
    tasks: [{ name: "model", error: "APIConnectionError" }],
    checkpoint: {
      checkpoint_id: "cp-2",
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [
        { id: "u1", type: "human", content: "old" },
        { id: "u2", type: "human", content: "current" },
      ],
    },
  };
  assert.equal(
    currentRecoveryLedgerStorageKey("thread-1", state),
    recoveryLedgerStorageKey("thread-1", "u2")
  );

  const malformedPending = JSON.stringify({
    state: RECOVERY_LEDGER_STATES.STARTED,
    threadId: "thread-1",
    turnId: "u2",
    runId: "run-2",
    checkpointId: "cp-2",
    recoveryRunId: null,
  });
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      { outcome: "runtime_error" },
      [{ run_id: "run-2", status: "error" }],
      malformedPending
    ),
    false
  );

  const stalePending = JSON.stringify({
    version: 1,
    state: RECOVERY_LEDGER_STATES.STARTED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: "cp-1",
    recoveryRunId: null,
  });
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      { outcome: "runtime_error" },
      [{ run_id: "run-2", status: "error" }],
      stalePending
    ),
    false
  );
});

test("recovery is decided before stage-stop and a final failure stays runtime_error", () => {
  const state = {
    next: ["model"],
    tasks: [{ name: "model", error: "APIConnectionError" }],
    checkpoint: {
      checkpoint_id: "cp-1",
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [{ id: "u1", type: "human", content: "question" }],
    },
  };
  const evidence = {
    outcome: "runtime_error",
    terminal_status: "error",
    has_answer: false,
    assistant_answer_count: 0,
    error_summary: "APIConnectionError",
  };
  const run = { run_id: "run-1", status: "error" };
  const pending = JSON.stringify({
    version: 1,
    state: RECOVERY_LEDGER_STATES.SCHEDULED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: "cp-1",
    recoveryRunId: null,
  });
  assert.equal(
    recoveryAwareStageStopDecision(
      "thread-1",
      state,
      evidence,
      [run],
      pending
    ),
    "wait_for_recovery"
  );

  const failed = JSON.stringify({
    ...JSON.parse(pending),
    state: RECOVERY_LEDGER_STATES.FAILED,
  });
  assert.equal(
    recoveryAwareStageStopDecision(
      "thread-1",
      state,
      evidence,
      [run],
      failed
    ),
    "preserve_runtime_error"
  );
  assert.equal(
    recoveryAwareStageStopDecision(
      "thread-1",
      { ...state, next: [] },
      { ...evidence, outcome: "completed_without_answer" },
      [{ run_id: "run-1", status: "success" }],
      null
    ),
    "evaluate_stage_stop"
  );
});

test("waits for the identified active recovery run and guards ordinary active runs", () => {
  const state = {
    next: ["model"],
    tasks: [{ name: "model", error: "APIConnectionError" }],
    checkpoint: {
      checkpoint_id: "cp-1",
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [{ id: "u1", type: "human", content: "question" }],
    },
  };
  const runtimeError = { outcome: "runtime_error" };
  const originalRun = { run_id: "failed-run", status: "error" };
  const startedLedger = JSON.stringify({
    version: 1,
    state: RECOVERY_LEDGER_STATES.STARTED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "failed-run",
    checkpointId: "cp-1",
    recoveryRunId: "recovery-run",
  });

  for (const status of ["pending", "running"]) {
    const recoveryRun = { run_id: "recovery-run", status };
    assert.equal(
      shouldWaitForTransientModelRecovery(
        "thread-1",
        state,
        runtimeError,
        [recoveryRun, originalRun],
        startedLedger
      ),
      true
    );
    assert.equal(
      shouldReturnTerminalOutcome(
        { status: "error" },
        runtimeError,
        recoveryRun
      ),
      false
    );
  }

  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [{ run_id: "recovery-run", status: "success" }, originalRun],
      startedLedger
    ),
    true
  );
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [{ run_id: "recovery-run", status: "error" }, originalRun],
      startedLedger
    ),
    false
  );

  const succeededLedger = JSON.stringify({
    ...JSON.parse(startedLedger),
    state: RECOVERY_LEDGER_STATES.SUCCEEDED,
  });
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [{ run_id: "recovery-run", status: "success" }, originalRun],
      succeededLedger
    ),
    true
  );

  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [{ run_id: "other-recovery", status: "running" }, originalRun],
      startedLedger
    ),
    false
  );

  const scheduledLedger = JSON.stringify({
    ...JSON.parse(startedLedger),
    state: RECOVERY_LEDGER_STATES.SCHEDULED,
    recoveryRunId: null,
  });
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [originalRun],
      scheduledLedger
    ),
    true
  );

  const ordinaryActiveRun = { run_id: "ordinary-run", status: "running" };
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      state,
      runtimeError,
      [ordinaryActiveRun, originalRun],
      null
    ),
    false
  );
  assert.equal(
    shouldReturnTerminalOutcome(
      { status: "error" },
      runtimeError,
      ordinaryActiveRun
    ),
    false
  );
  assert.equal(
    shouldReturnTerminalOutcome(
      { status: "error" },
      runtimeError,
      originalRun
    ),
    true
  );

  const convergedState = {
    next: [],
    tasks: [],
    checkpoint: {
      checkpoint_id: "cp-recovered",
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [
        { id: "u1", type: "human", content: "question" },
        { id: "a1", type: "ai", content: "recovered answer" },
      ],
    },
  };
  const completed = classifyOutcome(
    { status: "idle" },
    convergedState,
    { run_id: "recovery-run", status: "success" }
  );
  assert.equal(completed.outcome, "completed_with_answer");
  assert.equal(
    shouldWaitForTransientModelRecovery(
      "thread-1",
      convergedState,
      completed,
      [{ run_id: "recovery-run", status: "success" }, originalRun],
      succeededLedger
    ),
    false
  );
  assert.equal(
    shouldReturnTerminalOutcome(
      { status: "idle" },
      completed,
      { run_id: "recovery-run", status: "success" }
    ),
    true
  );
});
