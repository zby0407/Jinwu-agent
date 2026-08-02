// Async sub-agents (writing-agent / data-analysis-agent) run as SEPARATE
// background LangGraph threads+runs when 金乌's `enable_async_subagents`
// is on. The originating conversation tracks them in its thread state under the
// `async_tasks` key (deepagents' AsyncTask shape). The Agents board reads that to
// show which background agents a conversation launched, their status, and — on
// expand — their live steps (fetched from the task's own thread).
//
// Memory workers (jwmemory-*-worker) are intentionally NOT shown here: they are
// ~2s ephemeral background runs whose only meaningful output (profile edits /
// observations) is already visible in the Memory panel.

export interface AsyncTaskItem {
  task_id: string;
  agent_name: string;
  thread_id: string;
  run_id: string;
  status: string;
  created_at?: string;
  last_updated_at?: string;
}

export type AsyncAgentStatus =
  | "running"
  | "success"
  | "error"
  | "cancelled"
  | "expired"
  | "unknown";

/** Collapse the various SDK run statuses into the 6 we render. "expired" is
 *  never sent by the backend — useAsyncAgents synthesizes it when the task's
 *  thread/run no longer exists (sub-agent threads aren't restored across
 *  backend restarts; only main-graph threads are). */
export function normalizeAsyncStatus(s: string | undefined): AsyncAgentStatus {
  switch (s) {
    case "running":
    case "pending":
    case "busy":
      return "running";
    case "success":
      return "success";
    case "error":
    case "timeout":
      return "error";
    case "cancelled":
    case "interrupted":
      return "cancelled";
    case "expired":
      return "expired";
    default:
      return "unknown";
  }
}

const AGENT_LABELS: Record<string, string> = {
  "writing-agent": "Writing agent",
  "data-analysis-agent": "Data analysis agent",
};

export function agentLabel(name: string): string {
  return AGENT_LABELS[name] ?? name;
}

// ---------------------------------------------------------------------------
// "Report to main chat" — loop a finished async task back to the main agent.
// ---------------------------------------------------------------------------
// The 金乌 main agent recognizes a synthetic "[Async tasks update]"
// USER message as a background-completion SIGNAL (not a new request) and responds
// by calling check_async_task(task_id) to fetch the real result from the
// sub-agent's own thread. This mirrors the backend's format_batch_message
// (cli/async_notifier.py): the message carries NO result, only the signal —
// the agent fetches the result itself. So the WebUI can loop a result back with
// zero backend change: submit this exact block as a user turn on the MAIN thread.

/** Prefix the main agent's ASYNC_NOTIFICATIONS prompt section keys on. */
export const ASYNC_UPDATE_MARKER = "[Async tasks update]";

export interface AsyncTaskReportTarget {
  agent_name: string;
  task_id: string;
  run_id?: string;
  liveStatus?: string;
  status: string;
}

export type MainChatReportResult =
  | "sent"
  | "busy"
  | "duplicate"
  | "wrong-thread";

export type MainChatReporter = (
  task: AsyncTaskReportTarget,
  expectedThreadId: string
) => MainChatReportResult;

interface ParsedAsyncUpdate {
  taskId: string;
  runId?: string;
}

export function asyncTaskReportKey(task: {
  task_id: string;
  run_id?: string;
}): string {
  return `${task.task_id}:${task.run_id || "legacy"}`;
}

export function parseAsyncUpdateMessage(
  text: string
): ParsedAsyncUpdate | null {
  const lines = text.trim().split(/\r?\n/);
  if (lines[0] !== ASYNC_UPDATE_MARKER || !lines[1]) return null;
  try {
    const payload = JSON.parse(lines[1]) as {
      task_id?: unknown;
      run_id?: unknown;
    };
    if (typeof payload.task_id !== "string" || !payload.task_id) return null;
    return {
      taskId: payload.task_id,
      runId:
        typeof payload.run_id === "string" && payload.run_id
          ? payload.run_id
          : undefined,
    };
  } catch {
    return null;
  }
}

/** True if a (human) message is one of our injected async-completion signals —
 *  used to render it as a system pill instead of a user bubble. */
export function isAsyncUpdateMessage(text: string): boolean {
  return parseAsyncUpdateMessage(text) !== null;
}

export function asyncUpdateMessageKey(text: string): string | null {
  const parsed = parseAsyncUpdateMessage(text);
  if (!parsed) return null;
  return `${parsed.taskId}:${parsed.runId || "legacy"}`;
}

export function asyncUpdateMatchesTask(
  text: string,
  task: { task_id: string; run_id?: string }
): boolean {
  const parsed = parseAsyncUpdateMessage(text);
  if (!parsed || parsed.taskId !== task.task_id) return false;
  return !parsed.runId || parsed.runId === task.run_id;
}

/** Build the "[Async tasks update]" signal block for one finished task,
 *  mirroring the backend's single-task format_batch_message output and adding
 *  `run_id` for client-side deduplication. `task_id` must match the key the main
 *  agent tracks in its `async_tasks` state (that's how check_async_task resolves
 *  it). */
export function formatAsyncUpdateMessage(task: AsyncTaskReportTarget): string {
  const status = task.liveStatus || task.status || "success";
  const line = JSON.stringify({
    agent: task.agent_name,
    kind: "agent",
    ...(task.run_id ? { run_id: task.run_id } : {}),
    status,
    task_id: task.task_id,
  });
  return [
    ASYNC_UPDATE_MARKER,
    line,
    "(Signal only — fetch full result via check_async_task (sub-agents) if relevant to the current step, else acknowledge & continue.)",
  ].join("\n");
}

// Theme dot + label + pulse per status. CSS vars referenced via arbitrary-value
// classes (the base's semantic bg tokens are dead in this fork).
// NOTE: never put a bracketed class literal in a comment (Tailwind scans those).
export const ASYNC_STATUS_META: Record<
  AsyncAgentStatus,
  { dot: string; label: string; pulse: boolean }
> = {
  running: { dot: "bg-[var(--color-warning)]", label: "运行中", pulse: true },
  success: { dot: "bg-[var(--color-success)]", label: "已完成", pulse: false },
  error: { dot: "bg-[var(--color-error)]", label: "错误", pulse: false },
  cancelled: {
    dot: "bg-muted-foreground",
    label: "已取消",
    pulse: false,
  },
  expired: { dot: "bg-muted-foreground", label: "已失效", pulse: false },
  unknown: { dot: "bg-muted-foreground", label: "未知", pulse: false },
};

/** Read + validate the `async_tasks` map from a thread state's `values`. */
export function parseAsyncTasks(value: unknown): AsyncTaskItem[] {
  if (!value || typeof value !== "object") return [];
  const out: AsyncTaskItem[] = [];
  for (const v of Object.values(value as Record<string, unknown>)) {
    if (!v || typeof v !== "object") continue;
    const t = v as Record<string, unknown>;
    if (typeof t.task_id !== "string" || typeof t.agent_name !== "string") {
      continue;
    }
    out.push({
      task_id: t.task_id,
      agent_name: t.agent_name,
      thread_id: typeof t.thread_id === "string" ? t.thread_id : t.task_id,
      run_id: typeof t.run_id === "string" ? t.run_id : "",
      status: typeof t.status === "string" ? t.status : "",
      created_at: typeof t.created_at === "string" ? t.created_at : undefined,
      last_updated_at:
        typeof t.last_updated_at === "string" ? t.last_updated_at : undefined,
    });
  }
  // Newest first (ISO-8601 strings sort lexicographically).
  out.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return out;
}

// An async task enriched with its REAL run status (from `runs.get`), so the
// board/indicator don't depend on the main agent calling check_async_task to
// refresh the cached `status` in conversation state (which otherwise stays
// "running" forever — the timer never stops).
export interface EnrichedAsyncTask extends AsyncTaskItem {
  liveStatus: string;
  startedAt?: string;
  endedAt?: string;
}

/** "expired" is deliberately NOT terminal: the sub-agent's thread is gone, so
 *  reporting it to the main agent would only make check_async_task fail — the
 *  auto-report loop and the "Notify main chat" button both key off this. */
export function isTerminalStatus(s: string | undefined): boolean {
  const n = normalizeAsyncStatus(s);
  return n === "success" || n === "error" || n === "cancelled";
}

export function countRunning(
  tasks: { liveStatus?: string; status: string }[]
): number {
  return tasks.filter(
    (t) => normalizeAsyncStatus(t.liveStatus ?? t.status) === "running"
  ).length;
}

/** Elapsed "1m 23s" / "12s" between a start ISO time and an end (ms epoch). */
export function formatElapsed(
  startIso: string | undefined,
  endMs: number
): string {
  if (!startIso) return "";
  const start = Date.parse(startIso);
  if (Number.isNaN(start)) return "";
  const secs = Math.max(0, Math.round((endMs - start) / 1000));
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  if (mins >= 60) {
    const h = Math.floor(mins / 60);
    return `${h}h ${mins % 60}m`;
  }
  return mins > 0 ? `${mins}m ${rem}s` : `${secs}s`;
}

/** Compact relative time like "12s", "3m", "1h" from an ISO timestamp. */
export function relTime(iso: string | undefined, now: number): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  const secs = Math.max(0, Math.round((now - ts) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
