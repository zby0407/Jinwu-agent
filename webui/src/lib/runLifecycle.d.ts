import type { RunStatus } from "@langchain/langgraph-sdk";

export function isTerminalRunStatus(status: RunStatus): boolean;
export function resumableRunStorageKey(threadId: string): string;
export function isSubAgentRunning(
  status: string | undefined,
  isLoading: boolean
): boolean;
