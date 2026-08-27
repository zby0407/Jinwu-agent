import {
  RECOVERY_LEDGER_STATES,
  isPendingRecoveryLedger,
  latestHumanTurnId,
  parseRecoveryLedger,
  recoveryLedgerMatchesFailure,
} from "../../../webui/src/lib/runRecovery.js";

export function messageText(content) {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part.text === "string") return part.text;
      return "";
    })
    .join("\n")
    .trim();
}

const TERMINAL_OUTCOMES = new Set([
  "completed_with_answer",
  "completed_without_answer",
  "provider_error",
  "runtime_error",
  "interrupted",
  "unknown_terminal_state",
  "research_blocked",
]);
const ACTIVE_RUNTIME_STATUSES = new Set([
  "busy",
  "pending",
  "running",
  "queued",
]);

export function isActiveRuntimeStatus(status) {
  return ACTIVE_RUNTIME_STATUSES.has(status);
}

export function isTerminalOutcome(outcome) {
  return TERMINAL_OUTCOMES.has(outcome);
}

export function shouldWaitForTransientModelRecovery(
  threadId,
  state,
  evidence,
  runs,
  rawLedger
) {
  if (!evidence?.outcome || !Array.isArray(runs)) {
    return false;
  }
  const turnId = latestHumanTurnId(state?.values?.messages);
  const ledger = parseRecoveryLedger(rawLedger, { threadId, turnId });
  if (!ledger) return false;
  const failedRun = runs.find((run) => run?.run_id === ledger.runId);
  if (
    !recoveryLedgerMatchesFailure(
      ledger,
      {
        threadId,
        state,
        run: failedRun,
      },
      { requirePending: false }
    )
  ) {
    return false;
  }
  if (isPendingRecoveryLedger(ledger) && !ledger.recoveryRunId) return true;
  if (
    [RECOVERY_LEDGER_STATES.STARTED, RECOVERY_LEDGER_STATES.SUCCEEDED].includes(
      ledger.state
    ) &&
    ledger.recoveryRunId
  ) {
    const recoveryRun = runs.find(
      (run) => run?.run_id === ledger.recoveryRunId
    );
    return (
      isActiveRuntimeStatus(recoveryRun?.status) ||
      recoveryRun?.status === "success"
    );
  }
  return false;
}

export function shouldReturnTerminalOutcome(thread, evidence, latestRun) {
  return (
    isTerminalOutcome(evidence?.outcome) &&
    !isActiveRuntimeStatus(thread?.status) &&
    !isActiveRuntimeStatus(latestRun?.status)
  );
}

export function recoveryAwareStageStopDecision(
  threadId,
  state,
  evidence,
  runs,
  rawLedger
) {
  if (
    shouldWaitForTransientModelRecovery(
      threadId,
      state,
      evidence,
      runs,
      rawLedger
    )
  ) {
    return "wait_for_recovery";
  }
  return evidence?.outcome === "runtime_error"
    ? "preserve_runtime_error"
    : "evaluate_stage_stop";
}

export function blockedResearchOutcome(reviewStatus, evidence) {
  if (reviewStatus?.status !== "blocked") return null;
  const reason = reviewStatus?.terminal?.reasonCode || "UNRESOLVED_REVIEW_GATE";
  const summary = reviewStatus?.terminal?.summary;
  return {
    outcome: "research_blocked",
    terminal_status: "blocked",
    has_answer: evidence.has_answer,
    assistant_answer_count: evidence.assistant_answer_count,
    error_summary: summary ? `${reason}: ${summary}` : reason,
    blocked_stage:
      reviewStatus?.terminal?.stage || reviewStatus?.currentStage || null,
  };
}

export function classifyOutcome(thread, state, latestRun) {
  const taskErrors = (state?.tasks ?? [])
    .map((task) => task?.error)
    .filter((value) => typeof value === "string" && value.length > 0);
  const errorText = [
    thread?.error?.error,
    thread?.error?.message,
    ...taskErrors,
  ]
    .filter(Boolean)
    .join("\n");
  const messages = state?.values?.messages ?? thread?.values?.messages ?? [];
  const aiMessages = messages.filter(
    (message) => message?.type === "ai" && messageText(message.content)
  );
  const isTerminalProtocolMessage = (message) => {
    const text = messageText(message.content);
    return (
      text.startsWith("[RESEARCH REVIEW TERMINAL]") ||
      text.startsWith("[RESEARCH REVIEW BLOCKED]") ||
      text.startsWith("[RESEARCH REVIEW TOOL FAILURE STOP]")
    );
  };
  const terminalProtocolMessages = aiMessages.filter(isTerminalProtocolMessage);
  const answerMessages = aiMessages.filter(
    (message) => !isTerminalProtocolMessage(message)
  );
  const hasAnswer = answerMessages.length > 0;
  const terminalStatus = latestRun?.status ?? thread?.status ?? "unknown";

  if (terminalProtocolMessages.length > 0) {
    const protocolText = messageText(
      terminalProtocolMessages[terminalProtocolMessages.length - 1].content
    );
    const statusMatch = protocolText.match(/\bstatus=([^;\s.]+)/);
    return {
      outcome: "research_blocked",
      terminal_status: statusMatch?.[1] ?? "blocked",
      has_answer: hasAnswer,
      assistant_answer_count: answerMessages.length,
      error_summary: protocolText,
    };
  }

  if (thread?.status === "interrupted") {
    const approvalInterrupt = (state?.tasks ?? []).some((task) =>
      String(task?.name ?? "").includes("HumanInTheLoopMiddleware")
    );
    return {
      outcome: approvalInterrupt ? "interrupted_approval" : "interrupted",
      terminal_status: "interrupted",
      has_answer: hasAnswer,
      assistant_answer_count: answerMessages.length,
      error_summary: errorText || null,
    };
  }

  if (terminalStatus === "error" || thread?.status === "error") {
    const providerPattern =
      /AccessDenied|PermissionDenied|AuthenticationError|RateLimit|Arrearage|invalid_api_key|API-key|\b40[13]\b|\b429\b/i;
    return {
      outcome: providerPattern.test(errorText)
        ? "provider_error"
        : "runtime_error",
      terminal_status: terminalStatus,
      has_answer: hasAnswer,
      assistant_answer_count: answerMessages.length,
      error_summary:
        errorText || "Run ended with error but exposed no error detail.",
    };
  }

  if (
    ["success", "idle"].includes(terminalStatus) ||
    thread?.status === "idle"
  ) {
    return {
      outcome: hasAnswer ? "completed_with_answer" : "completed_without_answer",
      terminal_status: terminalStatus,
      has_answer: hasAnswer,
      assistant_answer_count: answerMessages.length,
      error_summary: null,
    };
  }

  return {
    outcome: "unknown_terminal_state",
    terminal_status: terminalStatus,
    has_answer: hasAnswer,
    assistant_answer_count: answerMessages.length,
    error_summary: errorText || null,
  };
}
