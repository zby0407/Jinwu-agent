"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type Assistant,
  type Checkpoint,
  type Message,
  type Thread,
  type ThreadState,
} from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import type { UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { TodoItem } from "@/app/types/types";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { useClient } from "@/providers/ClientProvider";
import { useQueryState } from "nuqs";
import {
  extractSubAgentSteps,
  type SubAgentStep,
} from "@/lib/subAgentActivity";
import {
  isSummarizationMessage,
  parseSummarizationEvent,
} from "@/lib/summarization";
import { toast } from "sonner";
import {
  MODEL_OVERRIDE_METADATA_KEY,
  type ModelOverride,
} from "@/lib/modelCommand";
import {
  isTerminalRunStatus,
  resumableRunStorageKey,
} from "@/lib/runLifecycle";
import {
  RECOVERY_LEDGER_STATES,
  beginTransientModelRecovery,
  createRecoveryCancellation,
  isTransientProviderError,
  latestHumanTurnId,
  recoveryLedgerMatchesFailure,
  recoverableTransientModelCheckpoint,
  settleRecoveryAttempt,
  transitionOwnedRecoveryLedger,
  transientModelRecoveryDelayMs,
  writeRecoveryLedger,
} from "@/lib/runRecovery.js";
import { setThreadModelOverride } from "@/app/hooks/useThreads";
import { bindThreadWorkspace } from "@/lib/workspaceBinding.js";

export type StateType = {
  messages: Message[];
  todos: TodoItem[];
  files: Record<string, string>;
  email?: {
    id?: string;
    subject?: string;
    page_content?: string;
  };
  // Background async sub-agents (writing-agent / data-analysis-agent) this
  // conversation launched, keyed by task_id. Shape = deepagents' AsyncTask.
  async_tasks?: Record<string, unknown>;
  // Private state field set by the deepagents SummarizationMiddleware when the
  // conversation is compacted. langgraph dev exposes it over the SDK; the UI
  // surfaces it as a collapsible "Conversation compacted" block.
  _summarization_event?: unknown;
  __interrupt__?: unknown[];
  ui?: any;
};

// Keep empty fallbacks referentially stable.  Recreating [] / {} in the hook's
// return value makes bridge effects see a "new" value on every provider render.
// During reconnect (before LangGraph has hydrated values) that can feed a
// provider setState back into this hook indefinitely and trigger React #185.
const EMPTY_TODOS: TodoItem[] = [];
const EMPTY_FILES: Record<string, string> = {};
const EMPTY_ASYNC_TASKS: Record<string, unknown> = {};
const BRANCH_HISTORY_LIMIT = 10;
const RUN_STATUS_POLL_MS = 5_000;

/**
 * Keep only state channels the WebUI reads.
 *
 * Some graph middleware persists private runtime snapshots alongside the chat
 * state. In a long research thread `_quickjs_snapshot_payload` alone can be
 * megabytes, and retaining ten copies of it in React makes hydration and every
 * branch-metadata render unnecessarily expensive. The backend keeps the full
 * checkpoint; this projection only trims the browser-side copy.
 */
function projectWebUiState(values: StateType | null | undefined): StateType {
  // A thread created by the upload flow exists before its first checkpoint, so
  // LangGraph legitimately returns `values: null` despite the SDK's generic
  // type. Treat that shell as an empty chat state. Throwing here leaves the
  // progressive loader's `isLoading` flag stuck and the transcript hidden even
  // after a run has started.
  const source = values ?? ({} as Partial<StateType>);
  return {
    messages: Array.isArray(source.messages) ? source.messages : [],
    todos: Array.isArray(source.todos) ? source.todos : [],
    files: source.files && typeof source.files === "object" ? source.files : {},
    ...(source.email !== undefined ? { email: source.email } : {}),
    ...(source.async_tasks !== undefined
      ? { async_tasks: source.async_tasks }
      : {}),
    ...(source._summarization_event !== undefined
      ? { _summarization_event: source._summarization_event }
      : {}),
    ...(source.__interrupt__ !== undefined
      ? { __interrupt__: source.__interrupt__ }
      : {}),
    ...(source.ui !== undefined ? { ui: source.ui } : {}),
  };
}

function projectThreadState(
  state: ThreadState<StateType>
): ThreadState<StateType> {
  return { ...state, values: projectWebUiState(state.values) };
}

/**
 * A thread record already contains the latest values and is much cheaper for
 * langgraph dev to read than materialising a checkpoint state. Use it as an
 * immediate display snapshot; getState replaces this placeholder in the
 * background before any checkpoint-sensitive operation needs it.
 */
function threadRecordSnapshot(
  record: Thread<StateType>
): ThreadState<StateType> {
  const tasks = Object.entries(record.interrupts ?? {}).map(
    ([taskId, interrupts]) => ({
      id: taskId,
      name: "",
      error: null,
      interrupts,
      checkpoint: null,
      state: null,
    })
  );
  return {
    values: projectWebUiState(record.values),
    next: record.status === "interrupted" ? ["__pending__"] : [],
    checkpoint: {
      thread_id: record.thread_id,
      checkpoint_ns: "",
      checkpoint_id: null,
      checkpoint_map: null,
    },
    metadata: {},
    created_at: record.updated_at,
    parent_checkpoint: null,
    tasks,
  };
}

/**
 * Hydrate an existing thread in two stages.
 *
 * LangGraph checkpoint history repeats the conversation state at every step.
 * Asking for a large checkpoint window before rendering therefore makes threads
 * download and parse the same long conversation many times while the UI remains
 * stuck behind `isThreadLoading`. Fetch the latest checkpoint first so messages
 * can render immediately, then replace it with the recent branch history in the
 * background. Ten checkpoints matches the SDK default, preserves nearby
 * input/answer versions, and avoids an unbounded 100-state payload for long runs.
 */
function useProgressiveThreadHistory(
  client: ReturnType<typeof useClient>,
  threadId: string | null,
  enabled: boolean,
  initialRecord: Thread<StateType> | null
): UseStreamThread<StateType> {
  const [data, setData] = useState<UseStreamThread<StateType>["data"]>();
  const [error, setError] = useState<unknown>();
  const [isLoading, setIsLoading] = useState(false);
  const requestIdRef = useRef(0);
  const dataThreadIdRef = useRef<string | null>(null);

  const mutate = useCallback(
    async (mutateId?: string) => {
      const targetThreadId = mutateId ?? threadId;
      const requestId = ++requestIdRef.current;

      if (!enabled || !targetThreadId) {
        dataThreadIdRef.current = null;
        setData(undefined);
        setError(undefined);
        setIsLoading(false);
        return [];
      }

      if (dataThreadIdRef.current !== targetThreadId) {
        dataThreadIdRef.current = targetThreadId;
        setData(undefined);
      }
      setError(undefined);
      setIsLoading(true);

      try {
        if (initialRecord?.thread_id === targetThreadId) {
          // The existence preflight has already downloaded the latest values.
          // Publishing them here avoids a second, slower checkpoint read before
          // the transcript becomes visible. Keep this inside the guarded block
          // so even a malformed shell can never strand the loading flag.
          setData([threadRecordSnapshot(initialRecord)]);
        }

        const latest = projectThreadState(
          await client.threads.getState<StateType>(targetThreadId)
        );
        const latestHistory = latest.checkpoint == null ? [] : [latest];
        if (requestIdRef.current !== requestId) return latestHistory;

        // A non-null history makes useStream.isThreadLoading false even while
        // the branch history continues loading, so the current conversation is
        // usable as soon as this single checkpoint arrives.
        setData(latestHistory);

        try {
          const history = (
            await client.threads.getHistory<StateType>(targetThreadId, {
              limit: BRANCH_HISTORY_LIMIT,
            })
          ).map(projectThreadState);
          if (requestIdRef.current === requestId) {
            setData(history);
            setIsLoading(false);
          }
          return history;
        } catch (historyError) {
          // The latest checkpoint is already enough for normal chat use. A
          // branch-history failure must not put the whole conversation back
          // behind the loading screen.
          if (requestIdRef.current === requestId) {
            setIsLoading(false);
            console.warn(
              "Couldn't load the full checkpoint history; showing the latest state.",
              historyError
            );
          }
          return latestHistory;
        }
      } catch (latestError) {
        if (requestIdRef.current === requestId) {
          setError(latestError);
          setIsLoading(false);
        }
        throw latestError;
      }
    },
    [client, enabled, initialRecord, threadId]
  );

  useEffect(() => {
    void mutate().catch(() => undefined);
    return () => {
      requestIdRef.current += 1;
    };
  }, [mutate]);

  return {
    data,
    error,
    isLoading:
      isLoading ||
      Boolean(enabled && threadId && dataThreadIdRef.current !== threadId),
    mutate,
  };
}

/**
 * Sanitize a raw interrupt pulled from `client.threads.getState` before it is
 * surfaced to the UI. The live SDK normalizes `stream.interrupt`, but the raw
 * persisted task interrupt is unvalidated — if its `value.action_requests`
 * (or `review_configs`) is present but NOT an array, ChatInterface's
 * `actionRequests.map(...)` / `for (const rc of review_configs)` throws and
 * blanks the entire page (the hard crash seen when deleting a file). Require an
 * object with an object `value`, and coerce any malformed list field to `[]` so
 * the worst case is "no card" instead of a render crash.
 */
function normalizePendingInterrupt(
  pending: unknown
): { value: Record<string, unknown> } | undefined {
  if (!pending || typeof pending !== "object") return undefined;
  const value = (pending as { value?: unknown }).value;
  if (!value || typeof value !== "object") return undefined;
  const v = value as Record<string, unknown>;
  const normalizedValue: Record<string, unknown> = { ...v };
  if ("action_requests" in v && !Array.isArray(v.action_requests)) {
    normalizedValue.action_requests = [];
  }
  if ("review_configs" in v && !Array.isArray(v.review_configs)) {
    normalizedValue.review_configs = [];
  }
  // Preserve the interrupt's other fields (id, ns, …); only the value is fixed.
  return { ...(pending as object), value: normalizedValue } as {
    value: Record<string, unknown>;
  };
}

/**
 * Total visible text length across a message list. Used to detect when the live
 * stream dropped tail CONTENT without dropping the message COUNT — e.g. the
 * final assistant turn arrives as an empty/partial AI message (same count) while
 * the persisted server snapshot has the full text. A pure length compare misses
 * that; comparing total text catches it.
 */
function totalTextLength(msgs: Message[]): number {
  let n = 0;
  for (const m of msgs) {
    const c = (m as { content?: unknown }).content;
    if (typeof c === "string") {
      n += c.length;
    } else if (Array.isArray(c)) {
      for (const part of c) {
        const t = (part as { text?: unknown })?.text;
        if (typeof t === "string") n += t.length;
      }
    }
  }
  return n;
}

/**
 * A content key for an interrupt, used to tell "the stale interrupt the server
 * already resolved" apart from "a genuinely new interrupt". We key on the
 * `value` payload because both the live SDK interrupt and the getState-fetched
 * one share it (and a fresh object identity each poll can't be compared).
 */
function interruptValueKey(i: unknown): string | null {
  if (!i || typeof i !== "object") return null;
  try {
    return JSON.stringify((i as { value?: unknown }).value ?? null);
  } catch {
    return null;
  }
}

function hasActionableInterrupt(i: unknown): boolean {
  if (!i || typeof i !== "object") return false;
  const value = (i as { value?: unknown }).value;
  if (!value || typeof value !== "object") return false;
  const v = value as { type?: unknown; action_requests?: unknown };
  return (
    v.type === "ask_user" ||
    (Array.isArray(v.action_requests) && v.action_requests.length > 0)
  );
}

function latestTaskInterrupt(
  tasks: Array<{ interrupts?: unknown[] }> | undefined
): unknown {
  if (!Array.isArray(tasks)) return undefined;
  for (let i = tasks.length - 1; i >= 0; i--) {
    const interrupts = tasks[i]?.interrupts;
    if (Array.isArray(interrupts) && interrupts.length > 0) {
      return interrupts[interrupts.length - 1];
    }
  }
  return undefined;
}

// Build a human-readable summary from the SDK's `onError` payload, which can
// be a plain Error, a StreamError (structured `{ name, error, message }`),
// or a raw string. We try in order: structured `name: message`, plain
// `message`, JSON-of-`.error`, the raw string, finally a generic fallback.
// Capped at 300 chars so a giant stack trace doesn't blow up the toast; the
// full text is still available in the thread JSON via the export affordance.
function formatStreamError(error: unknown): string {
  const cap = (s: string) => (s.length > 300 ? s.slice(0, 297) + "..." : s);
  if (typeof error === "string" && error.trim()) return cap(error.trim());
  if (error && typeof error === "object") {
    const e = error as { name?: unknown; message?: unknown; error?: unknown };
    const name = typeof e.name === "string" ? e.name.trim() : null;
    const msg = typeof e.message === "string" ? e.message.trim() : null;
    let inner: string | null = null;
    if (typeof e.error === "string" && e.error.trim()) {
      inner = e.error.trim();
    } else if (e.error && typeof e.error === "object") {
      try {
        inner = JSON.stringify(e.error);
      } catch {
        inner = null;
      }
    }
    const body = msg ?? inner;
    const combined = name && body ? `${name}: ${body}` : name ?? body ?? "";
    if (combined) return cap(combined);
  }
  return "运行失败。";
}

function hasHttpStatus(error: unknown, status: number): boolean {
  if (typeof error === "string") {
    return (
      error.includes(`HTTP ${status}`) ||
      error.includes(`"status":${status}`) ||
      error.includes(`"status": ${status}`)
    );
  }
  if (!error || typeof error !== "object") return false;
  const value = error as {
    status?: unknown;
    message?: unknown;
    error?: unknown;
  };
  if (value.status === status) return true;
  if (
    typeof value.message === "string" &&
    hasHttpStatus(value.message, status)
  ) {
    return true;
  }
  return value.error !== error && hasHttpStatus(value.error, status);
}

export function useChat({
  activeAssistant,
  onHistoryRevalidate,
  thread,
}: {
  activeAssistant: Assistant | null;
  onHistoryRevalidate?: () => void;
  thread?: UseStreamThread<StateType>;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const client = useClient();
  // Never hand an unverified URL id to useStream. The SDK starts hydration
  // immediately and a missing thread rejects an internal promise before its
  // onError callback can fully contain it. Preflighting the record keeps a
  // stale post-restart URL out of the streaming state machine altogether.
  const [streamThreadId, setStreamThreadId] = useState<string | null>(null);
  const [streamThreadMetadata, setStreamThreadMetadata] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [streamThreadRecord, setStreamThreadRecord] =
    useState<Thread<StateType> | null>(null);
  const missingThreadIdsRef = useRef<Set<string>>(new Set());
  const recoverMissingThread = useCallback(
    (missingThreadId: string) => {
      // LangGraph dev can legitimately lose an in-memory thread after restart.
      // A stale URL must not leave useStream hydrating forever: detach the dead
      // id and return to a clean composer. Keep the notification idempotent
      // because SDK hydration, metadata loading, and recovery polling can all
      // observe the same 404 concurrently.
      if (threadId === missingThreadId) {
        void setThreadId(null);
      }
      setStreamThreadId((current) =>
        current === missingThreadId ? null : current
      );
      setStreamThreadMetadata(null);
      setStreamThreadRecord(null);
      if (!missingThreadIdsRef.current.has(missingThreadId)) {
        missingThreadIdsRef.current.add(missingThreadId);
        toast.warning("原任务已在服务重启后失效，已切换到新任务。");
      }
      onHistoryRevalidate?.();
    },
    [onHistoryRevalidate, setThreadId, threadId]
  );
  useEffect(() => {
    if (!threadId) {
      setStreamThreadId(null);
      setStreamThreadMetadata(null);
      setStreamThreadRecord(null);
      return;
    }
    if (streamThreadId === threadId) return;

    let cancelled = false;
    void client.threads
      .search<StateType>({
        ids: [threadId],
        limit: 1,
        // This request already verifies the URL id. Reuse its current values
        // for instant hydration instead of discarding them and waiting for a
        // second, slower /state request before rendering.
        select: [
          "thread_id",
          "updated_at",
          "metadata",
          "status",
          "values",
          "interrupts",
        ],
      })
      .then((matches) => {
        if (cancelled) return;
        const match = matches.find((item) => item.thread_id === threadId);
        if (!match) {
          recoverMissingThread(threadId);
          return;
        }
        setStreamThreadMetadata(
          (match.metadata as Record<string, unknown> | undefined) ?? {}
        );
        setStreamThreadRecord(match);
        setStreamThreadId(threadId);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(formatStreamError(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, recoverMissingThread, streamThreadId, threadId]);
  const handleStreamThreadId = useCallback(
    (createdThreadId: string) => {
      setStreamThreadId(createdThreadId);
      setStreamThreadMetadata({});
      setStreamThreadRecord(null);
      void setThreadId(createdThreadId);
      // A fresh useStream submission creates the thread and reports its id
      // before substantive workspace-backed tools run. Bind immediately so
      // the backend and the WebUI status/artifact routes share one task root.
      void bindThreadWorkspace(createdThreadId).catch((error) => {
        toast.error(
          error instanceof Error ? error.message : "无法绑定任务工作区。"
        );
      });
    },
    [setThreadId]
  );

  // Live sub-agent activity captured from subgraph stream events, keyed by the
  // subgraph namespace (e.g. "tools:<id>"). Ephemeral: it resets when the chat
  // session remounts on thread switch, and is not persisted (lost on reload).
  const [subAgentActivity, setSubAgentActivity] = useState<
    Record<string, SubAgentStep[]>
  >({});
  const activeRunRef = useRef<{ run_id: string; thread_id: string } | null>(
    null
  );
  const [activeRun, setActiveRun] = useState<{
    run_id: string;
    thread_id: string;
  } | null>(null);
  const streamStopRef = useRef<(() => Promise<void>) | null>(null);
  const stoppedMessagesRef = useRef<{
    threadId: string;
    messages: Message[];
  } | null>(null);
  const stoppedCheckpointRef = useRef<{
    threadId: string;
    checkpoint: Omit<Checkpoint, "thread_id">;
  } | null>(null);
  const transientModelRecoveryRef = useRef<
    (error: unknown, run?: { run_id: string; thread_id: string }) => boolean
  >(() => false);
  const transientModelRecoveryCreatedRef = useRef<
    (run: { run_id: string; thread_id: string }) => void
  >(() => {});
  const transientModelRecoverySettledRef = useRef<
    (
      state: "succeeded" | "failed",
      run: { run_id: string; thread_id: string } | undefined,
      callbackThreadId: string | null
    ) => boolean
  >(() => false);
  const pendingTransientModelRecoveryRef = useRef<{
    ledger: {
      version: number;
      state: string;
      threadId: string;
      turnId: string;
      runId: string;
      checkpointId: string | null;
      recoveryRunId: string | null;
    };
    cancellation: ReturnType<typeof createRecoveryCancellation>;
    submitted: boolean;
  } | null>(null);
  const progressiveThread = useProgressiveThreadHistory(
    client,
    streamThreadId,
    thread == null,
    streamThreadRecord
  );
  const hydratedThread = thread ?? progressiveThread;
  useEffect(() => {
    if (thread != null || !progressiveThread.error || !streamThreadId) return;
    if (hasHttpStatus(progressiveThread.error, 404)) {
      recoverMissingThread(streamThreadId);
      return;
    }
    toast.error(formatStreamError(progressiveThread.error));
  }, [progressiveThread.error, recoverMissingThread, streamThreadId, thread]);

  const stream = useStream<StateType>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    reconnectOnMount: true,
    threadId: streamThreadId,
    onThreadId: handleStreamThreadId,
    defaultHeaders: { "x-auth-scheme": "langsmith" },
    // Regenerated answers are real LangGraph branches. The external progressive
    // loader supplies one checkpoint immediately and fills this history in the
    // background, avoiding a loading screen that waits on many large states.
    fetchStateHistory: { limit: BRANCH_HISTORY_LIMIT },
    // Revalidate thread list when stream finishes, errors, or creates new
    // thread. Errors additionally surface a toast with the SDK's payload -
    // without this the user only sees React's generic "An internal error
    // occurred" and has to dig into the server log to learn that, e.g., a
    // model provider returned a quota error.
    onFinish: (_state, run) => {
      transientModelRecoverySettledRef.current("succeeded", run, threadId);
      if (!run || activeRunRef.current?.run_id === run.run_id) {
        activeRunRef.current = null;
      }
      setActiveRun((current) =>
        !run || current?.run_id === run.run_id ? null : current
      );
      onHistoryRevalidate?.();
    },
    onError: (error, run) => {
      if (!run || activeRunRef.current?.run_id === run.run_id) {
        activeRunRef.current = null;
      }
      setActiveRun((current) =>
        !run || current?.run_id === run.run_id ? null : current
      );
      onHistoryRevalidate?.();
      if (hasHttpStatus(error, 404) && threadId) {
        recoverMissingThread(threadId);
        return;
      }
      if (
        transientModelRecoverySettledRef.current("failed", run, threadId)
      ) {
        toast.error(formatStreamError(error));
        return;
      }
      if (transientModelRecoveryRef.current(error, run)) return;
      toast.error(formatStreamError(error));
    },
    onCreated: (run) => {
      transientModelRecoveryCreatedRef.current(run);
      activeRunRef.current = run;
      setActiveRun(run);
      if (stoppedMessagesRef.current?.threadId === run.thread_id) {
        stoppedMessagesRef.current = null;
      }
      if (stoppedCheckpointRef.current?.threadId === run.thread_id) {
        stoppedCheckpointRef.current = null;
      }
      onHistoryRevalidate?.();
    },
    // Capture sub-agent (subgraph) node outputs as they stream. `namespace` is
    // non-empty (e.g. ["tools:<id>"]) for subgraphs and empty for the main graph,
    // which we skip.
    onUpdateEvent: (data, options) => {
      const ns = options?.namespace;
      if (!ns || ns.length === 0) return;
      const steps = extractSubAgentSteps(data);
      if (steps.length === 0) return;
      const key = ns.join("|");
      // Defer out of the SDK store's synchronous notify cycle. Calling
      // setState inline here re-enters the store update and trips React
      // error #185 (maximum update depth exceeded), which kills the live
      // stream mid-run and leaves the UI looking idle while the run continues.
      queueMicrotask(() => {
        setSubAgentActivity((prev) => ({
          ...prev,
          [key]: [...(prev[key] ?? []), ...steps],
        }));
      });
    },
    experimental_thread: hydratedThread,
  });
  streamStopRef.current = stream.stop;

  // `useStream` resumes a run from sessionStorage without firing `onCreated`,
  // so restore the exact run identity for the reconciliation loop below.
  useEffect(() => {
    if (!stream.isLoading || !threadId || activeRun) return;
    try {
      const runId = window.sessionStorage.getItem(
        resumableRunStorageKey(threadId)
      );
      if (runId) {
        const resumedRun = { run_id: runId, thread_id: threadId };
        activeRunRef.current = resumedRun;
        setActiveRun(resumedRun);
      }
    } catch {
      // Restricted storage only disables reload recovery; newly created runs
      // are still tracked by `onCreated`.
    }
  }, [activeRun, stream.isLoading, threadId]);

  // A resumable SSE connection can occasionally miss its terminal tail even
  // though LangGraph has already settled the run. In that case the SDK's local
  // StreamManager never leaves `isLoading=true`, so the composer, activity
  // panel, and stale tool calls remain locked forever. Reconcile against the
  // exact run captured by `onCreated`; never infer completion from workspace
  // files or an empty checkpoint while the run is still pending/running.
  useEffect(() => {
    if (
      !stream.isLoading ||
      !threadId ||
      !activeRun ||
      activeRun.thread_id !== threadId
    ) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const reconcile = async () => {
      try {
        const persistedRun = await client.runs.get(
          activeRun.thread_id,
          activeRun.run_id
        );
        if (cancelled) return;
        if (isTerminalRunStatus(persistedRun.status)) {
          // `useStream.stop()` aborts the stale local reader. Its wrapper also
          // cancels the run named in sessionStorage, so remove that marker only
          // after the server has independently confirmed this exact run is
          // terminal. This avoids sending a redundant cancellation request.
          try {
            window.sessionStorage.removeItem(
              resumableRunStorageKey(activeRun.thread_id)
            );
          } catch {
            // Storage can be unavailable in restricted browser contexts. The
            // local abort is still necessary; cancelling a terminal run is a
            // harmless no-op on supported LangGraph servers.
          }
          await streamStopRef.current?.();
          onHistoryRevalidate?.();
          return;
        }
      } catch (error) {
        // A transient status read must not unlock or cancel a live run. Existing
        // stream error handling remains responsible for user-visible failures.
        if (hasHttpStatus(error, 404)) return;
      }
      if (!cancelled) {
        timer = setTimeout(reconcile, RUN_STATUS_POLL_MS);
      }
    };

    timer = setTimeout(reconcile, RUN_STATUS_POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeRun, client, onHistoryRevalidate, stream.isLoading, threadId]);

  // --- Resilient pending-state fallback ------------------------------------
  // The live SSE stream can end (isLoading flips false) BEFORE the run actually
  // pauses on a tool-approval interrupt server-side — e.g. the backend's
  // auxiliary tool-selector model emits into the stream and desyncs it. When
  // that happens, `stream.interrupt` stays empty AND `stream.messages` is stale
  // (missing the final `execute` tool-call message), so the approval card never
  // renders until a manual thread switch re-fetches history.
  //
  // Bridge it by reading thread state directly once the stream settles: while
  // the run is still pending (`next` non-empty) but no live interrupt is shown,
  // poll a BOUNDED number of times until the interrupt is persisted, then
  // surface BOTH the interrupt and that snapshot's messages. Stops as soon as
  // the interrupt is found or the run is truly done (`next` empty); a new run
  // (isLoading→true) clears it. Not an unbounded poll — that would race the
  // live stream and revive resolved interrupts.
  const [fetchedInterrupt, setFetchedInterrupt] =
    useState<typeof stream.interrupt>(undefined);
  // Content key of an interrupt the server confirmed RESOLVED. The getter
  // suppresses a stale `stream.interrupt` (e.g. one re-surfaced from SDK history
  // after approving) ONLY when it matches this key — so a genuinely new
  // interrupt is never hidden (the old global-null sentinel hid everything).
  const [resolvedInterruptKey, setResolvedInterruptKey] = useState<
    string | null
  >(null);
  const [fetchedMessages, setFetchedMessages] = useState<Message[] | null>(
    null
  );
  // Thread records preserve compacted history, but can include message roots
  // from sibling checkpoint branches. Graph-state snapshots are scoped to the
  // branch currently selected by useStream. Remember the source so a
  // thread-wide fallback cannot leak an old edited input into the active view.
  const [fetchedMessagesScope, setFetchedMessagesScope] = useState<
    "thread" | "branch" | null
  >(null);
  const [regenerationPreview, setRegenerationPreview] = useState<
    Message[] | null
  >(null);
  const [fetchedThreadId, setFetchedThreadId] = useState<string | null>(null);
  // `useStream` can reconnect to a busy thread with isLoading=false even though
  // the persisted checkpoint still has work in `next`. Mirror that server fact
  // so a refresh cannot unlock the composer while the original run is active.
  const [serverPending, setServerPending] = useState(false);
  const [stopState, setStopState] = useState<"idle" | "stopping" | "stopped">(
    "idle"
  );
  const stopRequestedRef = useRef(false);
  const recoveryRunRef = useRef(0);
  // Recovery polling is only for a live run whose SSE tail may have been
  // dropped. Initial conversation hydration already comes from useStream's
  // latest-state request; polling again there downloads the same large state
  // plus the full thread record for no benefit.
  const recoveryNeededRef = useRef(false);
  const recoveryThreadRef = useRef<string | null>(null);

  // Per-thread model override. When set, gets folded into
  // `configurable.model` on every `stream.submit` — the backend's
  // `configurable_model` middleware
  // (jw/middleware/configurable_model.py) is what actually swaps
  // the chat model per request.
  //
  // The persistence dance gets a wrinkle for fresh chats: the thread row
  // doesn't exist server-side until the first `stream.submit` creates it,
  // so we can't write `model_override` into thread metadata yet. We stash
  // any pre-thread pick in `pendingOverrideRef`, fold it into the first
  // run's config via `buildRunConfig`, and write it through to metadata
  // when `threadId` actually shows up. Without this, the user's first
  // message goes to the deployment default even after they picked a model
  // from the empty composer.
  const [modelOverride, setModelOverrideState] = useState<ModelOverride | null>(
    null
  );
  const pendingOverrideRef = useRef<ModelOverride | null>(null);
  useEffect(() => {
    if (!threadId || streamThreadId !== threadId) {
      // Don't clobber a pending pre-thread override — `buildRunConfig` still
      // needs to read it for the first send.
      if (!pendingOverrideRef.current) setModelOverrideState(null);
      return;
    }
    // Thread just came into existence (or we switched onto an existing one).
    // If we have a pending pre-thread override, write it through to metadata
    // and keep the local state as-is. Otherwise fetch the thread's persisted
    // override and seed local state from it.
    if (pendingOverrideRef.current) {
      const pending = pendingOverrideRef.current;
      pendingOverrideRef.current = null;
      void (async () => {
        try {
          await setThreadModelOverride(threadId, pending);
        } catch {
          // The local state still reflects the pick; the next `setModelOverride`
          // call (or thread reopen) gets another chance to persist it.
        }
      })();
      return;
    }
    const raw = (streamThreadMetadata ?? {})[MODEL_OVERRIDE_METADATA_KEY];
    if (
      raw &&
      typeof raw === "object" &&
      typeof (raw as { model?: unknown }).model === "string"
    ) {
      const r = raw as { model: string; model_provider?: unknown };
      setModelOverrideState({
        model: r.model,
        model_provider:
          typeof r.model_provider === "string" ? r.model_provider : undefined,
      });
    } else {
      setModelOverrideState(null);
    }
  }, [streamThreadId, streamThreadMetadata, threadId]);

  // Persist + apply locally. When the thread row exists, writes metadata
  // first so a reload keeps the choice. Pre-thread (new chat with no
  // threadId yet), stashes the override in a ref so the next send picks it
  // up via `buildRunConfig` and the thread-id effect can persist it as soon
  // as the row is created server-side.
  const setModelOverride = useCallback(
    async (next: ModelOverride | null) => {
      setModelOverrideState(next);
      if (!threadId) {
        pendingOverrideRef.current = next;
        return;
      }
      pendingOverrideRef.current = null;
      await setThreadModelOverride(threadId, next);
    },
    [threadId]
  );
  const streamInterruptKey = interruptValueKey(stream.interrupt);
  useEffect(() => {
    if (!threadId || streamThreadId !== threadId) {
      recoveryThreadRef.current = threadId;
      recoveryNeededRef.current = false;
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      setRegenerationPreview(null);
      setResolvedInterruptKey(null);
      setServerPending(false);
      stopRequestedRef.current = false;
      stoppedMessagesRef.current = null;
      stoppedCheckpointRef.current = null;
      setStopState("idle");
      return;
    }
    if (recoveryThreadRef.current !== threadId) {
      recoveryThreadRef.current = threadId;
      recoveryNeededRef.current = false;
    }
    if (stream.isLoading) {
      recoveryNeededRef.current = true;
      recoveryRunRef.current += 1;
      setFetchedInterrupt(undefined);
      setRegenerationPreview(null);
      setServerPending(false);
      return;
    }
    if (stopRequestedRef.current) {
      recoveryNeededRef.current = false;
      setServerPending(false);
      return;
    }
    if (!recoveryNeededRef.current) return;
    recoveryNeededRef.current = false;
    // The live stream count at the moment it settled. If the server's persisted
    // state has MORE messages than this, the stream ended early and dropped the
    // tail — either the final assistant text, or the `execute` tool-call message
    // plus its approval interrupt. Either way we backfill from thread state
    // (the same data a thread-switch re-fetch would pull in).
    const baseline = stream.messages.length;
    const recoveryRunId = ++recoveryRunRef.current;
    let cancelled = false;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const MAX_TRIES = 15;
    const attempt = async () => {
      tries += 1;
      try {
        // `getState` returns the GRAPH CHECKPOINT state — which the backend
        // windows/compacts for memory, so its `values.messages` is only the
        // recent slice. `threads.get` returns the persisted THREAD RECORD with
        // the full message history. We need both: state for run status
        // (`next` / `tasks` / `interrupts`), record for the messages the UI
        // displays. Done in parallel to keep the round trip tight.
        const [state, threadRecord] = await Promise.all([
          client.threads.getState(threadId) as Promise<{
            tasks?: Array<{ interrupts?: unknown[] }>;
            next?: unknown[];
            values?: { messages?: Message[] };
          }>,
          client.threads.get(threadId) as Promise<{
            values?: { messages?: Message[] };
          }>,
        ]);
        if (cancelled || recoveryRunRef.current !== recoveryRunId) return;
        const selectedBranchIsForked = stream.messages.some(
          (message, index) =>
            (stream.getMessagesMetadata(message, index)?.branchOptions
              ?.length ?? 0) > 1
        );
        const branchMessages = state.values?.messages;
        const useBranchSnapshot =
          selectedBranchIsForked && Array.isArray(branchMessages);
        const msgs = useBranchSnapshot
          ? branchMessages
          : threadRecord.values?.messages;
        const messageScope = useBranchSnapshot ? "branch" : "thread";
        const pending = latestTaskInterrupt(state.tasks);
        const stillPending = Array.isArray(state.next) && state.next.length > 0;
        const safePending = normalizePendingInterrupt(pending);
        if (safePending && hasActionableInterrupt(safePending)) {
          // An actionable interrupt is waiting on the user, not the server.
          // Keeping serverPending=true here feeds into the public isLoading
          // value and disables Approve/Edit/Reject, deadlocking the run.
          setServerPending(false);
          // Tool-approval interrupt reached — surface it and its matching message
          // snapshot together. Mixing live messages with fetched interrupts is the
          // race that hides approval cards for repeated execute calls.
          setFetchedInterrupt(
            safePending as unknown as typeof stream.interrupt
          );
          setResolvedInterruptKey(null);
          if (Array.isArray(msgs)) {
            setFetchedThreadId(threadId);
            setFetchedMessages(msgs);
            setFetchedMessagesScope(messageScope);
          }
          return;
        }
        setServerPending(stillPending);
        // Backfill only after the live stream is idle. During active streaming the
        // live message list owns rendering; this recovery loop is for dropped tail
        // state after the stream has settled.
        if (Array.isArray(msgs) && msgs.length > baseline) {
          setFetchedThreadId(threadId);
          setFetchedMessages(msgs);
          setFetchedMessagesScope(messageScope);
        }
        if (!stillPending) {
          // The server has no pending task/interrupt anymore. Record the stale
          // live interrupt's identity so the getter suppresses ONLY that one
          // (composer unlocks after approving) — a new interrupt still shows.
          setFetchedInterrupt(undefined);
          setResolvedInterruptKey(streamInterruptKey);
          if (Array.isArray(msgs)) {
            setFetchedThreadId(threadId);
            setFetchedMessages(msgs);
            setFetchedMessagesScope(messageScope);
          }
          return;
        }
        // Keep polling only while the run is still working server-side; a
        // finished run (next empty) won't produce anything more.
        if (stillPending && tries < MAX_TRIES && !cancelled) {
          timer = setTimeout(attempt, 1000);
        }
      } catch (error) {
        // `langgraph dev` keeps threads in memory. After a backend restart the
        // browser can still have a valid-looking local task URL while the
        // corresponding server thread is gone. Retrying that permanent 404
        // makes the composer appear busy for the full recovery window.
        if (hasHttpStatus(error, 404)) {
          setServerPending(false);
          setFetchedInterrupt(undefined);
          recoverMissingThread(threadId);
          return;
        }
        if (
          !cancelled &&
          recoveryRunRef.current === recoveryRunId &&
          tries < MAX_TRIES
        ) {
          timer = setTimeout(attempt, 1000);
        }
      }
    };
    void attempt();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Precise deps on purpose: re-running on the whole `stream` object (new each
    // render) would loop the getState fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    threadId,
    streamInterruptKey,
    stream.isLoading,
    client,
    recoverMissingThread,
    streamThreadId,
  ]);

  // Show the live interrupt unless it's the exact one the server told us was
  // resolved (then fall through to the fetched one, usually undefined → composer
  // unlocks). A new live interrupt has a different key, so it's never suppressed.
  const liveInterrupt = stream.interrupt;
  const interrupt =
    liveInterrupt &&
    (resolvedInterruptKey === null ||
      interruptValueKey(liveInterrupt) !== resolvedInterruptKey)
      ? liveInterrupt
      : fetchedThreadId === threadId
      ? fetchedInterrupt ?? undefined
      : undefined;
  // Prefer the backfilled snapshot when it is "ahead" of the live stream — i.e.
  // the stream ended early and dropped the tail. "Ahead" means either MORE
  // messages, or (once settled) the SAME number of messages but MORE total text:
  // the final assistant turn often arrives as an empty/partial AI message with
  // the right count but no content, so a pure length compare would keep showing
  // the blank live version (the bug where the answer only appears after a manual
  // refresh). The equal-count/more-text rule is gated on `!isLoading` so a
  // mid-stream poll snapshot never flickers over the actively updating stream.
  //
  // Once the run has settled AND we have a snapshot, ALWAYS prefer the snapshot.
  // `stream.messages` can carry subgraph noise (streamSubgraphs: true) plus
  // stale per-message metadata from earlier runs, inflating its length above the
  // persisted main-thread state. A pure `>` compare against that bloated count
  // would keep us on the stream — which makes the downstream subgraph-namespace
  // filter (ChatInterface.processedMessages) drop legitimate main-thread
  // history that's only tagged subgraph in stale stream metadata.
  const messages = (() => {
    if (regenerationPreview && serverPending && !stream.isLoading) {
      return regenerationPreview;
    }
    const selectedBranchIsForked = stream.messages.some(
      (message, index) =>
        (stream.getMessagesMetadata(message, index)?.branchOptions?.length ??
          0) > 1
    );
    // `threads.get()` returns a thread-wide persistence record, not one
    // checkpoint path. It can therefore contain both the original and edited
    // human inputs. Once the SDK exposes a fork, only its selected values or a
    // branch-scoped recovery snapshot may drive the conversation UI.
    if (selectedBranchIsForked && fetchedMessagesScope !== "branch") {
      return stream.messages;
    }
    if (!fetchedMessages || fetchedThreadId !== threadId)
      return stream.messages;
    if (fetchedInterrupt) return fetchedMessages;
    if (!stream.isLoading) return fetchedMessages;
    if (fetchedMessages.length > stream.messages.length) return fetchedMessages;
    if (
      fetchedMessages.length === stream.messages.length &&
      totalTextLength(fetchedMessages) > totalTextLength(stream.messages)
    ) {
      return fetchedMessages;
    }
    return stream.messages;
  })();

  // Fold the per-thread model override into the assistant's base config. The
  // backend reads `configurable.model` + `configurable.model_provider` per
  // request. We always send a `configurable` object (possibly empty) so the
  // override leaves no trace on runs that don't need it.
  const buildRunConfig = useCallback(() => {
    const base = activeAssistant?.config ?? {};
    const baseConfigurable =
      (base as { configurable?: Record<string, unknown> }).configurable ?? {};
    const configurable: Record<string, unknown> = { ...baseConfigurable };
    // All WebUI research tasks participate in the task-workspace registry.
    // A future project picker can replace this value without changing the
    // thread/run binding contract.
    configurable.project_id =
      typeof configurable.project_id === "string" && configurable.project_id
        ? configurable.project_id
        : "default";
    if (modelOverride) {
      configurable.model = modelOverride.model;
      if (modelOverride.model_provider) {
        configurable.model_provider = modelOverride.model_provider;
      }
    }
    return { ...base, configurable };
  }, [activeAssistant?.config, modelOverride]);

  const persistPendingRecovery = useCallback(
    (
      pending: NonNullable<
        typeof pendingTransientModelRecoveryRef.current
      >,
      state: string,
      updates: Record<string, string | null> = {}
    ) => {
      const ledger = transitionOwnedRecoveryLedger(
        pendingTransientModelRecoveryRef.current,
        pending,
        state,
        updates
      );
      if (!ledger) return null;
      pending.ledger = writeRecoveryLedger(window.sessionStorage, ledger);
      return pending.ledger;
    },
    []
  );

  const cancelTransientModelRecovery = useCallback(
    (pending = pendingTransientModelRecoveryRef.current) => {
      if (
        !pending ||
        pendingTransientModelRecoveryRef.current !== pending
      ) {
        return false;
      }
      pending.cancellation.cancel();
      try {
        persistPendingRecovery(pending, RECOVERY_LEDGER_STATES.CANCELLED);
      } finally {
        if (pendingTransientModelRecoveryRef.current === pending) {
          pendingTransientModelRecoveryRef.current = null;
        }
      }
      return true;
    },
    [persistPendingRecovery]
  );

  useEffect(() => {
    const effectThreadId = threadId;
    return () => {
      const pending = pendingTransientModelRecoveryRef.current;
      if (pending?.ledger.threadId === effectThreadId) {
        cancelTransientModelRecovery(pending);
      }
    };
  }, [cancelTransientModelRecovery, threadId]);

  const recoverTransientModelRun = useCallback(
    (error: unknown, run?: { run_id: string; thread_id: string }): boolean => {
      if (
        !threadId ||
        !run ||
        run.thread_id !== threadId ||
        !isTransientProviderError(error)
      ) {
        return false;
      }

      const turnId = latestHumanTurnId(messages);
      if (!turnId) return false;
      let ledger;
      try {
        ledger = beginTransientModelRecovery(window.sessionStorage, {
          threadId,
          turnId,
          runId: run.run_id,
          checkpointId: null,
        });
      } catch {
        // Without a persisted ledger the harness cannot distinguish a pending
        // recovery from a settled runtime error, so fail closed.
        return false;
      }
      if (!ledger) return false;

      const cancellation = createRecoveryCancellation();
      const pending = {
        ledger,
        cancellation,
        submitted: false,
      };
      pendingTransientModelRecoveryRef.current = pending;
      setServerPending(true);
      recoveryNeededRef.current = false;
      recoveryRunRef.current += 1;

      void (async () => {
        const [failedState, failedRun] = await Promise.all([
          client.threads.getState<StateType>(threadId),
          client.runs.get(threadId, run.run_id),
        ]);
        if (
          cancellation.isCancelled() ||
          stopRequestedRef.current ||
          recoveryThreadRef.current !== threadId
        ) {
          cancelTransientModelRecovery(pending);
          return;
        }
        const failedCheckpoint =
          recoverableTransientModelCheckpoint(failedState);
        if (!failedCheckpoint) {
          throw new Error("当前失败检查点不满足安全自动重试条件。");
        }
        const scheduledLedger = persistPendingRecovery(
          pending,
          RECOVERY_LEDGER_STATES.SCHEDULED,
          { checkpointId: failedCheckpoint.checkpoint_id }
        );
        if (
          !scheduledLedger ||
          !recoveryLedgerMatchesFailure(scheduledLedger, {
            threadId,
            state: failedState,
            run: failedRun,
          })
        ) {
          throw new Error("失败运行身份与持久化检查点不一致。");
        }

        const recoveryDelayMs = transientModelRecoveryDelayMs(0);
        toast.warning(
          `上游连接中断，将在 ${Math.round(recoveryDelayMs / 1_000)} 秒后从失败检查点自动恢复。`
        );
        if (!(await cancellation.wait(recoveryDelayMs))) return;
        if (
          stopRequestedRef.current ||
          recoveryThreadRef.current !== threadId
        ) {
          cancelTransientModelRecovery(pending);
          return;
        }

        const [currentState, currentFailedRun] = await Promise.all([
          client.threads.getState<StateType>(threadId),
          client.runs.get(threadId, run.run_id),
        ]);
        if (
          cancellation.isCancelled() ||
          stopRequestedRef.current ||
          recoveryThreadRef.current !== threadId ||
          !recoveryLedgerMatchesFailure(scheduledLedger, {
            threadId,
            state: currentState,
            run: currentFailedRun,
          })
        ) {
          cancelTransientModelRecovery(pending);
          return;
        }
        const currentCheckpoint =
          recoverableTransientModelCheckpoint(currentState);
        if (!currentCheckpoint) {
          cancelTransientModelRecovery(pending);
          return;
        }

        if (
          !persistPendingRecovery(pending, RECOVERY_LEDGER_STATES.STARTED) ||
          stopRequestedRef.current
        ) {
          cancelTransientModelRecovery(pending);
          return;
        }
        pending.submitted = true;
        const { thread_id: _threadId, ...resumeCheckpoint } = currentCheckpoint;
        setFetchedInterrupt(undefined);
        setFetchedMessages(null);
        setFetchedMessagesScope(null);
        setFetchedThreadId(null);
        setRegenerationPreview(null);
        setResolvedInterruptKey(null);
        stream.submit(null, {
          checkpoint: resumeCheckpoint,
          config: buildRunConfig(),
          streamSubgraphs: true,
          streamMode: ["updates"],
          streamResumable: true,
          onDisconnect: "continue",
        });
        onHistoryRevalidate?.();
      })().catch((recoveryError) => {
        if (
          pendingTransientModelRecoveryRef.current === pending &&
          !pending.cancellation.isCancelled()
        ) {
          try {
            persistPendingRecovery(pending, RECOVERY_LEDGER_STATES.FAILED);
          } finally {
            if (pendingTransientModelRecoveryRef.current === pending) {
              pendingTransientModelRecoveryRef.current = null;
            }
          }
          setServerPending(false);
          toast.error(`自动恢复失败：${formatStreamError(recoveryError)}`);
        }
      });
      return true;
    },
    [
      buildRunConfig,
      cancelTransientModelRecovery,
      client.runs,
      client.threads,
      messages,
      onHistoryRevalidate,
      persistPendingRecovery,
      stream,
      threadId,
    ]
  );
  transientModelRecoveryRef.current = recoverTransientModelRun;
  transientModelRecoveryCreatedRef.current = (run) => {
    const pending = pendingTransientModelRecoveryRef.current;
    if (
      !pending?.submitted ||
      pending.ledger.threadId !== run.thread_id ||
      pending.ledger.recoveryRunId
    ) {
      return;
    }
    persistPendingRecovery(pending, RECOVERY_LEDGER_STATES.STARTED, {
      recoveryRunId: run.run_id,
    });
  };
  transientModelRecoverySettledRef.current = (
    state,
    run,
    callbackThreadId
  ) => {
    const pending = pendingTransientModelRecoveryRef.current;
    if (!pending) return false;
    const settledLedger = settleRecoveryAttempt(
      pending,
      state,
      run,
      callbackThreadId
    );
    if (!settledLedger) return false;
    if (pendingTransientModelRecoveryRef.current !== pending) return false;
    pending.ledger = writeRecoveryLedger(
      window.sessionStorage,
      settledLedger
    );
    pendingTransientModelRecoveryRef.current = null;
    setServerPending(false);
    return true;
  };

  const sendMessage = useCallback(
    (content: string) => {
      const stoppedSnapshot =
        stoppedMessagesRef.current?.threadId === threadId
          ? stoppedMessagesRef.current.messages
          : null;
      const stoppedCheckpoint =
        stoppedCheckpointRef.current?.threadId === threadId
          ? stoppedCheckpointRef.current.checkpoint
          : undefined;
      // Drop any settled-run snapshot up front. Otherwise, until `isLoading`
      // flips true (and the effect above clears it), a previous run's
      // `fetchedMessages` can still out-count `stream.messages` and shadow the
      // just-added optimistic user message — making it flicker/vanish.
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      setRegenerationPreview(null);
      setResolvedInterruptKey(null);
      stopRequestedRef.current = false;
      setStopState("idle");
      recoveryRunRef.current += 1;
      const newMessage: Message = { id: uuidv4(), type: "human", content };
      setServerPending(true);
      void (async () => {
        if (threadId) {
          const matches = await client.threads.search({
            ids: [threadId],
            limit: 1,
            select: ["thread_id"],
          });
          if (!matches.some((item) => item.thread_id === threadId)) {
            // Recreate only the missing server-side shell. Restore the visible
            // conversation, but deliberately do NOT restore async_tasks or
            // other execution state: those belong to the dead backend process
            // and reviving them can relaunch stale background sub-agents.
            await client.threads.create({
              threadId,
              graphId: activeAssistant?.graph_id,
              metadata: {
                project_id: "default",
                title: content.trim().slice(0, 80),
              },
              ifExists: "do_nothing",
            });
            const restoreMessages = stoppedSnapshot ?? stream.messages;
            if (restoreMessages.length > 0) {
              await client.threads.updateState(threadId, {
                values: { messages: restoreMessages },
              });
            }
          }
        }

        stream.submit(
          { messages: [newMessage] },
          {
            optimisticValues: (prev) => ({
              messages: [
                ...(stoppedSnapshot ?? prev.messages ?? []),
                newMessage,
              ],
            }),
            // fetchStateHistory makes useStream otherwise submit from its
            // cached branch head. After a manual stop that head predates the
            // checkpoint where we saved the partial AI response, so the first
            // server update would erase the optimistic copy. Pin this one turn
            // to the newly saved checkpoint instead.
            checkpoint: stoppedCheckpoint,
            config: buildRunConfig(),
            metadata:
              threadId === null
                ? {
                    project_id: "default",
                    title: content.trim().slice(0, 80),
                  }
                : undefined,
            streamSubgraphs: true,
            streamMode: ["updates"],
            streamResumable: true,
            onDisconnect: "continue",
          }
        );
      })().catch((error) => {
        setServerPending(false);
        toast.error(formatStreamError(error));
      });
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [
      stream,
      buildRunConfig,
      onHistoryRevalidate,
      threadId,
      client,
      activeAssistant?.graph_id,
    ]
  );

  const regenerateMessage = useCallback(
    (messageId: string) => {
      if (!threadId || stream.isLoading || serverPending) return;
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      setRegenerationPreview(null);
      setResolvedInterruptKey(null);
      stopRequestedRef.current = false;
      setStopState("idle");
      recoveryRunRef.current += 1;
      setServerPending(true);

      void (async () => {
        const targetIndex = messages.findIndex(
          (message) => message.id === messageId
        );
        if (targetIndex < 0) {
          throw new Error("此回答已不在当前活动分支中。");
        }
        let turnHumanIndex = -1;
        for (let index = targetIndex - 1; index >= 0; index -= 1) {
          if (messages[index].type === "human") {
            turnHumanIndex = index;
            break;
          }
        }
        const turnFirstAssistantIndex = messages.findIndex(
          (message, index) =>
            index > turnHumanIndex &&
            index <= targetIndex &&
            message.type === "ai"
        );
        const turnAnchorId =
          turnFirstAssistantIndex >= 0
            ? messages[turnFirstAssistantIndex].id
            : messageId;
        if (!turnAnchorId) {
          throw new Error("找不到此回答轮次的起点。");
        }
        const optimisticMessages =
          turnFirstAssistantIndex >= 0
            ? messages.slice(0, turnFirstAssistantIndex)
            : messages.slice(0, targetIndex);
        // Hide the old answer immediately after confirmation, including while
        // workspace artifacts are being removed before the replacement run.
        setRegenerationPreview(optimisticMessages);

        const history = await client.threads.getHistory<StateType>(threadId, {
          limit: 100,
        });
        const firstSeenState = [...history]
          .reverse()
          .find((state) =>
            (state.values.messages ?? []).some(
              (message) => message.id === turnAnchorId
            )
          );
        const checkpoint = firstSeenState?.parent_checkpoint;
        if (!checkpoint) {
          throw new Error("此回答之前的检查点已不可用。");
        }

        const resetResponse = await fetch("/api/regenerate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threadId }),
        });
        if (!resetResponse.ok) {
          const payload = (await resetResponse.json().catch(() => null)) as {
            error?: unknown;
          } | null;
          throw new Error(
            typeof payload?.error === "string"
              ? payload.error
              : "无法清除已生成的产物。"
          );
        }

        // Continue directly from the checkpoint immediately before the model
        // produced this answer. Creating an intermediate updateState checkpoint
        // here is incorrect: it can inherit the old answer and lose the pending
        // model node, turning regeneration into an empty, unrelated new turn.
        await stream.submit(null, {
          checkpoint,
          optimisticValues: {
            messages: optimisticMessages,
          },
          config: buildRunConfig(),
          streamSubgraphs: true,
          streamMode: ["updates"],
          streamResumable: true,
          onDisconnect: "continue",
        });
      })().catch((error) => {
        setRegenerationPreview(null);
        setServerPending(false);
        toast.error(
          error instanceof Error
            ? `Couldn't regenerate response: ${error.message}`
            : "Couldn't regenerate response."
        );
      });
      onHistoryRevalidate?.();
    },
    [
      threadId,
      stream,
      serverPending,
      client,
      messages,
      buildRunConfig,
      onHistoryRevalidate,
    ]
  );

  const editMessage = useCallback(
    (messageId: string, content: string) => {
      const editedContent = content.trim();
      if (!threadId || !editedContent || stream.isLoading || serverPending) {
        return;
      }
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      setRegenerationPreview(null);
      setResolvedInterruptKey(null);
      stopRequestedRef.current = false;
      setStopState("idle");
      recoveryRunRef.current += 1;
      setServerPending(true);

      void (async () => {
        const targetIndex = messages.findIndex(
          (message) => message.id === messageId && message.type === "human"
        );
        if (targetIndex < 0) {
          throw new Error("此消息已不在当前活动分支中。");
        }
        const targetMessage = messages[targetIndex];
        if (
          extractStringFromMessageContent(targetMessage).trim() ===
          editedContent
        ) {
          setServerPending(false);
          return;
        }

        const history = await client.threads.getHistory<StateType>(threadId, {
          limit: 100,
        });
        const firstSeenState = [...history]
          .reverse()
          .find((state) =>
            (state.values.messages ?? []).some(
              (message) => message.id === messageId
            )
          );
        const checkpoint = firstSeenState?.parent_checkpoint;
        if (!checkpoint) {
          throw new Error("此消息之前的检查点已不可用。");
        }

        const editedMessage: Message = {
          id: uuidv4(),
          type: "human",
          content: editedContent,
        };
        const optimisticMessages = [
          ...messages.slice(0, targetIndex),
          editedMessage,
        ];
        // Everything after the edited user turn belongs to the sibling branch.
        // Hide it immediately; the old branch remains available through the
        // SDK's branch metadata and can be restored with the version switcher.
        setRegenerationPreview(optimisticMessages);

        await stream.submit(
          { messages: [editedMessage] },
          {
            checkpoint,
            optimisticValues: { messages: optimisticMessages },
            config: buildRunConfig(),
            streamSubgraphs: true,
            streamMode: ["updates"],
            streamResumable: true,
            onDisconnect: "continue",
          }
        );
      })().catch((error) => {
        setRegenerationPreview(null);
        setServerPending(false);
        toast.error(
          error instanceof Error
            ? `Couldn't edit message: ${error.message}`
            : "Couldn't edit message."
        );
      });
      onHistoryRevalidate?.();
    },
    [
      threadId,
      stream,
      serverPending,
      messages,
      client,
      buildRunConfig,
      onHistoryRevalidate,
    ]
  );

  const setFiles = useCallback(
    async (files: Record<string, string>) => {
      if (!threadId) return;
      // TODO: missing a way how to revalidate the internal state
      // I think we do want to have the ability to externally manage the state
      await client.threads.updateState(threadId, { values: { files } });
    },
    [client, threadId]
  );

  const selectBranch = useCallback(
    (branch: string) => {
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      setRegenerationPreview(null);
      setResolvedInterruptKey(null);
      stopRequestedRef.current = false;
      setStopState("idle");
      recoveryRunRef.current += 1;
      stream.setBranch(branch);
    },
    [stream]
  );

  const resumeInterrupt = useCallback(
    (value: any) => {
      // Same as sendMessage: clear the prior snapshot before resuming so a stale
      // fetchedInterrupt/fetchedMessages can't briefly re-surface a resolved
      // approval card or shadow the resumed run's messages.
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedMessagesScope(null);
      setFetchedThreadId(null);
      // Mark the interrupt being resumed as resolved immediately. The SDK can
      // keep that same object in `stream.interrupt` while the continuation run
      // is already active; clearing this key resurrects a stale approval card
      // ("Approving…") until the next full refresh.
      setResolvedInterruptKey(interruptValueKey(interrupt));
      stopRequestedRef.current = false;
      setStopState("idle");
      recoveryRunRef.current += 1;
      stream.submit(null, {
        command: { resume: value },
        config: buildRunConfig(),
        streamSubgraphs: true,
        streamMode: ["updates"],
        streamResumable: true,
        onDisconnect: "continue",
      });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, buildRunConfig, onHistoryRevalidate, interrupt]
  );

  const stopStream = useCallback(async () => {
    if (stopRequestedRef.current) return;
    stopRequestedRef.current = true;
    cancelTransientModelRecovery();
    setStopState("stopping");
    setServerPending(false);
    recoveryNeededRef.current = false;
    recoveryRunRef.current += 1;

    // Capture the live main-thread snapshot before aborting the SSE request.
    // The model node has not checkpointed its in-progress AI message yet, so a
    // later history reload would otherwise replace it with the pre-run state.
    const stoppedMessages = stream.messages.filter((message, index) => {
      if (message.type === "human") return true;
      const metadata = stream.getMessagesMetadata(message, index);
      const checkpointNamespace =
        metadata?.streamMetadata?.["langgraph_checkpoint_ns"];
      if (
        typeof checkpointNamespace === "string" &&
        checkpointNamespace.includes("|")
      ) {
        return false;
      }
      return !isSummarizationMessage(message);
    });
    const activeRun = activeRunRef.current;

    try {
      await stream.stop();

      if (threadId) {
        // `useStream.stop()` fires run cancellation without awaiting it. Wait
        // for the server-side run as well so updateState cannot race the model
        // and so the next user turn is accepted immediately.
        let runIds: string[] = [];
        if (activeRun?.thread_id === threadId) {
          runIds = [activeRun.run_id];
        } else {
          const [running, pending] = await Promise.all([
            client.runs.list(threadId, { status: "running", limit: 10 }),
            client.runs.list(threadId, { status: "pending", limit: 10 }),
          ]);
          runIds = [
            ...new Set([...running, ...pending].map((run) => run.run_id)),
          ];
        }
        await Promise.allSettled(
          runIds.map((runId) =>
            client.runs.cancel(threadId, runId, true, "interrupt")
          )
        );

        if (stoppedMessages.length > 0) {
          await client.threads.updateState(threadId, {
            values: { messages: stoppedMessages },
          });
          const savedState = await client.threads.getState<StateType>(threadId);
          if (savedState.checkpoint) {
            const { thread_id: _threadId, ...checkpoint } =
              savedState.checkpoint;
            stoppedCheckpointRef.current = { threadId, checkpoint };
          }
          stoppedMessagesRef.current = {
            threadId,
            messages: stoppedMessages,
          };
          setFetchedThreadId(threadId);
          setFetchedMessages(stoppedMessages);
          setFetchedMessagesScope("branch");
        }

        void fetch("/api/task-stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threadId }),
        }).catch(() => {
          // Parent cancellation and partial-message persistence already
          // succeeded. This route only propagates the stop to experiment
          // subprocesses and contract sub-agents.
        });
      }
    } catch (error) {
      toast.error(
        `Generation stopped, but the partial response could not be saved: ${formatStreamError(
          error
        )}`
      );
    } finally {
      activeRunRef.current = null;
      setActiveRun(null);
      setServerPending(false);
      setStopState("stopped");
      onHistoryRevalidate?.();
    }
  }, [
    cancelTransientModelRecovery,
    client,
    onHistoryRevalidate,
    stream,
    threadId,
  ]);

  return {
    stream,
    todos: stream.values.todos ?? EMPTY_TODOS,
    files: stream.values.files ?? EMPTY_FILES,
    email: stream.values.email,
    asyncTasks: stream.values.async_tasks ?? EMPTY_ASYNC_TASKS,
    summarizationEvent: parseSummarizationEvent(
      stream.values._summarization_event
    ),
    ui: stream.values.ui,
    setFiles,
    messages,
    isLoading:
      stopState === "stopping" ||
      (stopState !== "stopped" && (stream.isLoading || serverPending)),
    isStopping: stopState === "stopping",
    isThreadLoading:
      stream.isThreadLoading ||
      (threadId !== null && streamThreadId !== threadId),
    interrupt,
    sendMessage,
    editMessage,
    regenerateMessage,
    selectBranch,
    stopStream,
    resumeInterrupt,
    subAgentActivity,
    modelOverride,
    setModelOverride,
  };
}
