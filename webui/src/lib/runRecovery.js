const TRANSIENT_PROVIDER_ERROR_MARKERS = [
  "APIConnectionError",
  "APITimeoutError",
  "ConnectError",
  "ConnectTimeout",
  "ReadError",
  "ReadTimeout",
  "RemoteProtocolError",
  "Server disconnected",
  "Connection reset",
  "temporarily unavailable",
];

export const MAX_TRANSIENT_MODEL_RECOVERIES_PER_TURN = 1;
export const TRANSIENT_MODEL_RECOVERY_BACKOFF_MS = [8_000];
export const RECOVERY_LEDGER_STATES = Object.freeze({
  DISCOVERING: "discovering",
  SCHEDULED: "scheduled",
  STARTED: "started",
  SUCCEEDED: "succeeded",
  FAILED: "failed",
  CANCELLED: "cancelled",
});

const RECOVERY_LEDGER_STATE_VALUES = new Set(
  Object.values(RECOVERY_LEDGER_STATES)
);
const PENDING_RECOVERY_LEDGER_STATES = new Set([
  RECOVERY_LEDGER_STATES.DISCOVERING,
  RECOVERY_LEDGER_STATES.SCHEDULED,
  RECOVERY_LEDGER_STATES.STARTED,
]);

export function transientModelRecoveryDelayMs(completedRecoveryCount) {
  const numericCount = Number.isFinite(completedRecoveryCount)
    ? Math.max(0, Math.trunc(completedRecoveryCount))
    : 0;
  return TRANSIENT_MODEL_RECOVERY_BACKOFF_MS[
    Math.min(numericCount, TRANSIENT_MODEL_RECOVERY_BACKOFF_MS.length - 1)
  ];
}

function errorText(value, seen = new Set()) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object" || seen.has(value)) return "";
  seen.add(value);
  const parts = [];
  for (const key of ["name", "message", "error", "detail", "cause"]) {
    const text = errorText(value[key], seen);
    if (text) parts.push(text);
  }
  return parts.join(" ");
}

export function isTransientProviderError(error) {
  const text = errorText(error);
  return TRANSIENT_PROVIDER_ERROR_MARKERS.some((marker) =>
    text.includes(marker)
  );
}

export function latestHumanTurnId(messages) {
  if (!Array.isArray(messages)) return null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message || (message.type !== "human" && message.type !== "user")) {
      continue;
    }
    return typeof message.id === "string" && message.id ? message.id : null;
  }
  return null;
}

export function recoveryLedgerStoragePrefix(threadId) {
  return `jw:transient-model-recovery:${threadId}:turn:`;
}

export function recoveryLedgerStorageKey(threadId, turnId) {
  return `${recoveryLedgerStoragePrefix(threadId)}${turnId}:ledger`;
}

export function currentRecoveryLedgerStorageKey(threadId, state) {
  const turnId = latestHumanTurnId(state?.values?.messages);
  return turnId ? recoveryLedgerStorageKey(threadId, turnId) : null;
}

function recoveryLedger(identity, state) {
  return {
    version: 1,
    state,
    threadId: identity.threadId,
    turnId: identity.turnId,
    runId: identity.runId,
    checkpointId: identity.checkpointId ?? null,
    recoveryRunId: identity.recoveryRunId ?? null,
  };
}

export function parseRecoveryLedger(raw, expected = {}) {
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    const keys = Object.keys(parsed ?? {}).sort();
    const expectedKeys = [
      "checkpointId",
      "recoveryRunId",
      "runId",
      "state",
      "threadId",
      "turnId",
      "version",
    ];
    if (
      keys.length !== expectedKeys.length ||
      keys.some((key, index) => key !== expectedKeys[index]) ||
      parsed?.version !== 1 ||
      !RECOVERY_LEDGER_STATE_VALUES.has(parsed.state) ||
      typeof parsed.threadId !== "string" ||
      !parsed.threadId ||
      typeof parsed.turnId !== "string" ||
      !parsed.turnId ||
      typeof parsed.runId !== "string" ||
      !parsed.runId ||
      !(
        parsed.checkpointId === null ||
        (typeof parsed.checkpointId === "string" && parsed.checkpointId)
      ) ||
      !(
        parsed.recoveryRunId === null ||
        (typeof parsed.recoveryRunId === "string" && parsed.recoveryRunId)
      ) ||
      (expected.threadId !== undefined &&
        parsed.threadId !== expected.threadId) ||
      (expected.turnId !== undefined && parsed.turnId !== expected.turnId)
    ) {
      return null;
    }
    if (
      [RECOVERY_LEDGER_STATES.SCHEDULED, RECOVERY_LEDGER_STATES.STARTED].includes(
        parsed.state
      ) &&
      parsed.checkpointId === null
    ) {
      return null;
    }
    return recoveryLedger(parsed, parsed.state);
  } catch {
    return null;
  }
}

export function readRecoveryLedger(storage, threadId, turnId) {
  return parseRecoveryLedger(
    storage.getItem(recoveryLedgerStorageKey(threadId, turnId)),
    { threadId, turnId }
  );
}

export function writeRecoveryLedger(storage, ledger) {
  const safeLedger = recoveryLedger(ledger, ledger.state);
  storage.setItem(
    recoveryLedgerStorageKey(safeLedger.threadId, safeLedger.turnId),
    JSON.stringify(safeLedger)
  );
  return safeLedger;
}

export function beginTransientModelRecovery(storage, identity) {
  const key = recoveryLedgerStorageKey(identity.threadId, identity.turnId);
  if (storage.getItem(key) !== null) return null;
  return writeRecoveryLedger(
    storage,
    recoveryLedger(identity, RECOVERY_LEDGER_STATES.DISCOVERING)
  );
}

export function transitionRecoveryLedger(ledger, state, updates = {}) {
  if (!ledger || !RECOVERY_LEDGER_STATE_VALUES.has(state)) {
    throw new TypeError("Invalid recovery ledger transition.");
  }
  return recoveryLedger(
    {
      ...ledger,
      checkpointId:
        updates.checkpointId === undefined
          ? ledger.checkpointId
          : updates.checkpointId,
      recoveryRunId:
        updates.recoveryRunId === undefined
          ? ledger.recoveryRunId
          : updates.recoveryRunId,
    },
    state
  );
}

export function transitionOwnedRecoveryLedger(
  currentAttempt,
  capturedAttempt,
  state,
  updates = {}
) {
  if (!currentAttempt || currentAttempt !== capturedAttempt) return null;
  return transitionRecoveryLedger(capturedAttempt.ledger, state, updates);
}

export function settleRecoveryAttempt(
  attempt,
  outcome,
  run,
  callbackThreadId
) {
  if (!attempt?.submitted) return null;
  const ledger = parseRecoveryLedger(attempt.ledger);
  if (!ledger) return null;
  if (
    callbackThreadId !== undefined &&
    callbackThreadId !== ledger.threadId
  ) {
    return null;
  }
  if (!ledger.recoveryRunId) {
    return run == null
      ? transitionRecoveryLedger(ledger, RECOVERY_LEDGER_STATES.FAILED)
      : null;
  }
  if (
    !run ||
    run.thread_id !== ledger.threadId ||
    run.run_id !== ledger.recoveryRunId
  ) {
    return null;
  }
  return transitionRecoveryLedger(
    ledger,
    outcome === "succeeded"
      ? RECOVERY_LEDGER_STATES.SUCCEEDED
      : RECOVERY_LEDGER_STATES.FAILED
  );
}

export function isPendingRecoveryLedger(ledger) {
  return Boolean(
    ledger && PENDING_RECOVERY_LEDGER_STATES.has(ledger.state)
  );
}

export function recoverableTransientModelCheckpoint(state) {
  if (!state || typeof state !== "object") return null;
  if (
    !Array.isArray(state.next) ||
    state.next.length !== 1 ||
    state.next[0] !== "model"
  ) {
    return null;
  }
  const failedModel = Array.isArray(state.tasks)
    ? state.tasks.find(
        (task) =>
          task && task.name === "model" && isTransientProviderError(task.error)
      )
    : null;
  if (!failedModel) return null;
  const checkpoint = state.checkpoint;
  if (
    !checkpoint ||
    typeof checkpoint !== "object" ||
    typeof checkpoint.checkpoint_id !== "string" ||
    !checkpoint.checkpoint_id
  ) {
    return null;
  }
  return checkpoint;
}

export function recoveryLedgerMatchesFailure(
  ledger,
  { threadId, state, run },
  { requirePending = true } = {}
) {
  const turnId = latestHumanTurnId(state?.values?.messages);
  const parsedLedger = parseRecoveryLedger(ledger, { threadId, turnId });
  if (
    !parsedLedger ||
    (requirePending && !isPendingRecoveryLedger(parsedLedger))
  ) {
    return false;
  }
  if (
    !run ||
    run.run_id !== parsedLedger.runId ||
    run.status !== "error"
  ) {
    return false;
  }
  const checkpoint = recoverableTransientModelCheckpoint(state);
  if (!checkpoint || checkpoint.thread_id !== parsedLedger.threadId) {
    return false;
  }
  return (
    parsedLedger.state === RECOVERY_LEDGER_STATES.DISCOVERING ||
    checkpoint.checkpoint_id === parsedLedger.checkpointId
  );
}

export function createRecoveryCancellation({
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  let cancelled = false;
  let timer = null;
  let settle = null;
  return {
    wait(delayMs) {
      if (cancelled) return Promise.resolve(false);
      return new Promise((resolve) => {
        settle = resolve;
        timer = setTimer(() => {
          timer = null;
          settle = null;
          resolve(!cancelled);
        }, delayMs);
      });
    },
    cancel() {
      if (cancelled) return;
      cancelled = true;
      if (timer !== null) clearTimer(timer);
      timer = null;
      const resolve = settle;
      settle = null;
      resolve?.(false);
    },
    isCancelled() {
      return cancelled;
    },
  };
}
