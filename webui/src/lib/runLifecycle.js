const TERMINAL_RUN_STATUSES = new Set([
  "error",
  "success",
  "timeout",
  "interrupted",
]);

export function isTerminalRunStatus(status) {
  return TERMINAL_RUN_STATUSES.has(status);
}

export function resumableRunStorageKey(threadId) {
  return `lg:stream:${threadId}`;
}

export function isSubAgentRunning(status, isLoading) {
  return isLoading && status === "pending";
}
