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

export function isTerminalOutcome(outcome) {
  return TERMINAL_OUTCOMES.has(outcome);
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
  const errorText = [thread?.error?.error, thread?.error?.message, ...taskErrors]
    .filter(Boolean)
    .join("\n");
  const messages = state?.values?.messages ?? thread?.values?.messages ?? [];
  const aiMessages = messages.filter(
    (message) => message?.type === "ai" && messageText(message.content),
  );
  const terminalProtocolMessages = aiMessages.filter((message) =>
    messageText(message.content).startsWith("[RESEARCH REVIEW TERMINAL]"),
  );
  const answerMessages = aiMessages.filter(
    (message) =>
      !messageText(message.content).startsWith("[RESEARCH REVIEW TERMINAL]"),
  );
  const hasAnswer = answerMessages.length > 0;
  const terminalStatus = latestRun?.status ?? thread?.status ?? "unknown";

  if (terminalProtocolMessages.length > 0) {
    const protocolText = messageText(
      terminalProtocolMessages[terminalProtocolMessages.length - 1].content,
    );
    const statusMatch = protocolText.match(/\bstatus=([^;\s.]+)/);
    return {
      outcome: "research_blocked",
      terminal_status: statusMatch?.[1] ?? terminalStatus,
      has_answer: hasAnswer,
      assistant_answer_count: answerMessages.length,
      error_summary: protocolText,
    };
  }

  if (thread?.status === "interrupted") {
    const approvalInterrupt = (state?.tasks ?? []).some((task) =>
      String(task?.name ?? "").includes("HumanInTheLoopMiddleware"),
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
      error_summary: errorText || "Run ended with error but exposed no error detail.",
    };
  }

  if (["success", "idle"].includes(terminalStatus) || thread?.status === "idle") {
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
