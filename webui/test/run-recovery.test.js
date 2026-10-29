import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_TRANSIENT_MODEL_RECOVERIES_PER_TURN,
  RECOVERY_LEDGER_STATES,
  beginTransientModelRecovery,
  releaseUnsubmittedRecovery,
  createRecoveryCancellation,
  isTransientProviderError,
  isRecoverableProviderError,
  isPendingRecoveryLedger,
  latestHumanTurnId,
  parseRecoveryLedger,
  recoveryLedgerMatchesFailure,
  recoveryLedgerStorageKey,
  recoverableTransientModelCheckpoint,
  settleRecoveryAttempt,
  transitionRecoveryLedger,
  transitionOwnedRecoveryLedger,
  transientModelRecoveryDelayMs,
} from "../src/lib/runRecovery.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) { values.delete(key); },
  };
}

test("discovery can be retried after cancellation or refresh, but ambiguous submission cannot", () => {
  const storage = memoryStorage();
  const identity = { threadId: 'thread-1', turnId: 'u1', runId: 'run-1', checkpointId: 'cp-1' };
  const first = beginTransientModelRecovery(storage, identity);
  assert.equal(releaseUnsubmittedRecovery(storage, 'thread-1', 'u1'), true);
  assert.ok(beginTransientModelRecovery(storage, identity));
  const started = transitionRecoveryLedger(first, RECOVERY_LEDGER_STATES.STARTED);
  storage.setItem(recoveryLedgerStorageKey('thread-1', 'u1'), JSON.stringify(started));
  assert.equal(releaseUnsubmittedRecovery(storage, 'thread-1', 'u1'), false);
  assert.equal(beginTransientModelRecovery(storage, identity), null);
});

function failedState(checkpointId = "cp-1", turnId = "u1") {
  return {
    next: ["model"],
    tasks: [
      {
        name: "model",
        error: "APIConnectionError('Connection error.')",
      },
    ],
    checkpoint: {
      checkpoint_id: checkpointId,
      checkpoint_ns: "",
      thread_id: "thread-1",
    },
    values: {
      messages: [{ id: turnId, type: "human", content: "question" }],
    },
  };
}

test("recognizes nested transient provider failures without matching domain errors", () => {
  assert.equal(
    isTransientProviderError({
      name: "StreamError",
      error: { message: "openai.APIConnectionError: Connection error." },
    }),
    true
  );
  assert.equal(
    isTransientProviderError(new Error("hypothesis schema is incomplete")),
    false
  );
});

test("stable provider codes permit explicit model-step recovery after settings are repaired", () => {
  for (const code of ["AUTH", "QUOTA", "MODEL_UNAVAILABLE", "TLS_HANDSHAKE", "PROXY_CONNECT", "TIMEOUT", "CONNECTION"]) {
    const error = `[JW_PROVIDER:${code}] synthetic diagnostic`;
    assert.equal(isRecoverableProviderError(error), true);
    const state = failedState();
    state.tasks[0].error = error;
    assert.equal(recoverableTransientModelCheckpoint(state).checkpoint_id, "cp-1");
    state.next = ["tools"];
    assert.equal(recoverableTransientModelCheckpoint(state), null);
  }
  assert.equal(isTransientProviderError('[JW_PROVIDER:AUTH]'), false);
});

test("failed new recovery run can be manually recovered once, but repeated clicks are rejected", () => {
  const storage = memoryStorage();
  const identity = { threadId: 'thread-1', turnId: 'u1', runId: 'run-1', checkpointId: 'cp-1' };
  const first = beginTransientModelRecovery(storage, identity);
  const failed = transitionRecoveryLedger(first, RECOVERY_LEDGER_STATES.FAILED, { recoveryRunId: 'run-2' });
  storage.setItem(recoveryLedgerStorageKey('thread-1', 'u1'), JSON.stringify(failed));
  assert.equal(beginTransientModelRecovery(storage, identity), null);
  assert.ok(beginTransientModelRecovery(storage, { ...identity, runId: 'run-2' }));
  assert.equal(beginTransientModelRecovery(storage, { ...identity, runId: 'run-2' }), null);
});

test("returns only a transient failed model checkpoint", () => {
  const checkpoint = {
    checkpoint_id: "cp-1",
    checkpoint_ns: "",
    thread_id: "thread-1",
  };
  assert.deepEqual(
    recoverableTransientModelCheckpoint({
      next: ["model"],
      tasks: [
        {
          name: "model",
          error: "APIConnectionError('Connection error.')",
        },
      ],
      checkpoint,
    }),
    checkpoint
  );
  assert.equal(
    recoverableTransientModelCheckpoint({
      next: ["tools"],
      tasks: [{ name: "tools", error: "APIConnectionError" }],
      checkpoint,
    }),
    null
  );
  assert.equal(
    recoverableTransientModelCheckpoint({
      next: ["model"],
      tasks: [{ name: "model", error: "ValueError('bad evidence')" }],
      checkpoint,
    }),
    null
  );
});

test("persists one non-secret recovery ledger before discovering the checkpoint", () => {
  const storage = memoryStorage();
  const identity = {
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: null,
    error: "API key must never be persisted",
  };

  const ledger = beginTransientModelRecovery(storage, identity);

  assert.deepEqual(ledger, {
    version: 1,
    state: RECOVERY_LEDGER_STATES.DISCOVERING,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: null,
    recoveryRunId: null,
  });
  assert.deepEqual(
    JSON.parse(
      storage.getItem(recoveryLedgerStorageKey("thread-1", "u1"))
    ),
    ledger
  );
  assert.equal(JSON.stringify(ledger).includes("API key"), false);
  assert.equal(MAX_TRANSIENT_MODEL_RECOVERIES_PER_TURN, 1);
});

test("a later checkpoint in the same human turn cannot schedule another recovery", () => {
  const storage = memoryStorage();
  assert.ok(
    beginTransientModelRecovery(storage, {
      threadId: "thread-1",
      turnId: "u1",
      runId: "run-1",
      checkpointId: null,
    })
  );
  assert.equal(
    beginTransientModelRecovery(storage, {
      threadId: "thread-1",
      turnId: "u1",
      runId: "run-2",
      checkpointId: "cp-created-by-recovery",
    }),
    null
  );
  assert.ok(
    beginTransientModelRecovery(storage, {
      threadId: "thread-1",
      turnId: "u2",
      runId: "run-3",
      checkpointId: null,
    })
  );
});

test("uses one bounded recovery delay", () => {
  assert.equal(transientModelRecoveryDelayMs(0), 8_000);
  assert.equal(transientModelRecoveryDelayMs(99), 8_000);
});

test("matches every captured failure identity field before recovery starts", () => {
  const scheduled = transitionRecoveryLedger(
    {
      version: 1,
      state: RECOVERY_LEDGER_STATES.DISCOVERING,
      threadId: "thread-1",
      turnId: "u1",
      runId: "run-1",
      checkpointId: null,
      recoveryRunId: null,
    },
    RECOVERY_LEDGER_STATES.SCHEDULED,
    { checkpointId: "cp-1" }
  );
  const context = {
    threadId: "thread-1",
    state: failedState(),
    run: { run_id: "run-1", status: "error" },
  };

  assert.equal(recoveryLedgerMatchesFailure(scheduled, context), true);
  assert.equal(
    recoveryLedgerMatchesFailure(scheduled, {
      ...context,
      state: failedState("cp-2"),
    }),
    false
  );
  assert.equal(
    recoveryLedgerMatchesFailure(scheduled, {
      ...context,
      state: failedState("cp-1", "u2"),
    }),
    false
  );
  assert.equal(
    recoveryLedgerMatchesFailure(scheduled, {
      ...context,
      run: { run_id: "run-2", status: "error" },
    }),
    false
  );
  assert.equal(
    recoveryLedgerMatchesFailure(scheduled, {
      ...context,
      run: { run_id: "run-1", status: "success" },
    }),
    false
  );
});

test("ledger pending states are explicit and terminal states do not wait", () => {
  const base = {
    version: 1,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: "cp-1",
    recoveryRunId: null,
  };
  for (const state of [
    RECOVERY_LEDGER_STATES.DISCOVERING,
    RECOVERY_LEDGER_STATES.SCHEDULED,
    RECOVERY_LEDGER_STATES.STARTED,
  ]) {
    assert.equal(isPendingRecoveryLedger({ ...base, state }), true);
  }
  for (const state of [
    RECOVERY_LEDGER_STATES.SUCCEEDED,
    RECOVERY_LEDGER_STATES.FAILED,
    RECOVERY_LEDGER_STATES.CANCELLED,
  ]) {
    assert.equal(isPendingRecoveryLedger({ ...base, state }), false);
  }
});

test("cancelling a pending delay prevents its recovery callback", async () => {
  let callback;
  let submitted = false;
  const cancellation = createRecoveryCancellation({
    setTimer(fn) {
      callback = fn;
      return 7;
    },
    clearTimer() {},
  });

  const waiting = cancellation.wait(8_000);
  cancellation.cancel();
  callback();
  if (await waiting) submitted = true;

  assert.equal(cancellation.isCancelled(), true);
  assert.equal(submitted, false);
});

test("a submitted attempt without a created run fails on anonymous settlement", () => {
  const startedLedger = {
    version: 1,
    state: RECOVERY_LEDGER_STATES.STARTED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "failed-run",
    checkpointId: "cp-1",
    recoveryRunId: null,
  };
  const attempt = { ledger: startedLedger, submitted: true };

  assert.equal(
    settleRecoveryAttempt(attempt, "failed", undefined).state,
    RECOVERY_LEDGER_STATES.FAILED
  );
  assert.equal(
    settleRecoveryAttempt(attempt, "succeeded", null).state,
    RECOVERY_LEDGER_STATES.FAILED
  );
  assert.equal(
    settleRecoveryAttempt(attempt, "failed", undefined, "thread-other"),
    null
  );

  const identifiedAttempt = {
    ...attempt,
    ledger: { ...startedLedger, recoveryRunId: "recovery-run" },
  };
  assert.equal(
    settleRecoveryAttempt(identifiedAttempt, "succeeded", {
      thread_id: "thread-1",
      run_id: "recovery-run",
    }).state,
    RECOVERY_LEDGER_STATES.SUCCEEDED
  );
  assert.equal(
    settleRecoveryAttempt(identifiedAttempt, "failed", {
      thread_id: "thread-1",
      run_id: "other-run",
    }),
    null
  );
});

test("an old generation cannot transition the current recovery attempt", () => {
  const oldAttempt = {
    generation: 1,
    ledger: {
      version: 1,
      state: RECOVERY_LEDGER_STATES.DISCOVERING,
      threadId: "thread-a",
      turnId: "turn-a",
      runId: "run-a",
      checkpointId: null,
      recoveryRunId: null,
    },
  };
  const currentAttempt = {
    generation: 2,
    ledger: {
      ...oldAttempt.ledger,
      threadId: "thread-b",
      turnId: "turn-b",
      runId: "run-b",
    },
  };

  assert.equal(
    transitionOwnedRecoveryLedger(
      currentAttempt,
      oldAttempt,
      RECOVERY_LEDGER_STATES.FAILED
    ),
    null
  );
  assert.equal(currentAttempt.ledger.state, RECOVERY_LEDGER_STATES.DISCOVERING);
  assert.equal(
    transitionOwnedRecoveryLedger(
      currentAttempt,
      currentAttempt,
      RECOVERY_LEDGER_STATES.SCHEDULED,
      { checkpointId: "cp-b" }
    ).checkpointId,
    "cp-b"
  );
});

test("the shared ledger parser rejects malformed and key-mismatched payloads", () => {
  const valid = {
    version: 1,
    state: RECOVERY_LEDGER_STATES.SCHEDULED,
    threadId: "thread-1",
    turnId: "u1",
    runId: "run-1",
    checkpointId: "cp-1",
    recoveryRunId: null,
  };

  assert.deepEqual(
    parseRecoveryLedger(JSON.stringify(valid), {
      threadId: "thread-1",
      turnId: "u1",
    }),
    valid
  );
  assert.equal(
    parseRecoveryLedger(JSON.stringify({ ...valid, version: undefined }), {
      threadId: "thread-1",
      turnId: "u1",
    }),
    null
  );
  assert.equal(
    parseRecoveryLedger(JSON.stringify(valid), {
      threadId: "thread-1",
      turnId: "u2",
    }),
    null
  );
  assert.equal(parseRecoveryLedger("{malformed", valid), null);
});
