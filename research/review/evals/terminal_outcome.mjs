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

export function classifyOutcome(thread, state, latestRun) {
  const taskErrors = (state?.tasks ?? [])
    .map((task) => task?.error)
    .filter((value) => typeof value === "string" && value.length > 0);
  const errorText = [thread?.error?.error, thread?.error?.message, ...taskErrors]
    .filter(Boolean)
    .join("\n");
  const messages = state?.values?.messages ?? thread?.values?.messages ?? [];
  const answerMessages = messages.filter(
    (message) => message?.type === "ai" && messageText(message.content),
  );
  const hasAnswer = answerMessages.length > 0;
  const terminalStatus = latestRun?.status ?? thread?.status ?? "unknown";

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
