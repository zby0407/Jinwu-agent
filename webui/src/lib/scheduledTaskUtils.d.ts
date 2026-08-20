export type DisplayRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "interrupted"
  | "unknown";

export function browserTimezone(): string;
export function createTaskKey(): string;
export function messageText(message: unknown): string;
export function initialScheduledPrompt(values: unknown): string;
export function scheduledPromptFromRun(run: unknown): string;
export function finalScheduledFeedback(values: unknown): string;
export function classifyScheduledRunStatus(
  runStatus: unknown,
  feedback?: string
): DisplayRunStatus;
export function legacyTaskKeyForPrompt<T extends { prompt: string; task_key: string }>(
  tasks: T[],
  prompt: string
): string | null;
export function needsScheduledTaskMigration(task: {
  task_key?: string;
  timezone?: string | null;
  on_run_completed?: string;
  enabled?: boolean;
}): boolean;
