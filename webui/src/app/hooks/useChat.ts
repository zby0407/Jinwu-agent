"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { type Message, type Assistant } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import type { UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { TodoItem } from "@/app/types/types";
import { useClient } from "@/providers/ClientProvider";
import { useQueryState } from "nuqs";
import {
  extractSubAgentSteps,
  type SubAgentStep,
} from "@/lib/subAgentActivity";
import { parseSummarizationEvent } from "@/lib/summarization";
import { toast } from "sonner";
import {
  MODEL_OVERRIDE_METADATA_KEY,
  type ModelOverride,
} from "@/lib/modelCommand";
import { setThreadModelOverride } from "@/app/hooks/useThreads";

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
  ui?: any;
};

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
  return "Run failed.";
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

  // Live sub-agent activity captured from subgraph stream events, keyed by the
  // subgraph namespace (e.g. "tools:<id>"). Ephemeral: it resets when the chat
  // session remounts on thread switch, and is not persisted (lost on reload).
  const [subAgentActivity, setSubAgentActivity] = useState<
    Record<string, SubAgentStep[]>
  >({});

  const stream = useStream<StateType>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    reconnectOnMount: true,
    threadId: threadId ?? null,
    onThreadId: setThreadId,
    defaultHeaders: { "x-auth-scheme": "langsmith" },
    // Enable fetching state history when switching to existing threads
    fetchStateHistory: true,
    // Revalidate thread list when stream finishes, errors, or creates new
    // thread. Errors additionally surface a toast with the SDK's payload -
    // without this the user only sees React's generic "An internal error
    // occurred" and has to dig into the server log to learn that, e.g., a
    // model provider returned a quota error.
    onFinish: onHistoryRevalidate,
    onError: (error) => {
      onHistoryRevalidate?.();
      toast.error(formatStreamError(error));
    },
    onCreated: onHistoryRevalidate,
    // Capture sub-agent (subgraph) node outputs as they stream. `namespace` is
    // non-empty (e.g. ["tools:<id>"]) for subgraphs and empty for the main graph,
    // which we skip.
    onUpdateEvent: (data, options) => {
      const ns = options?.namespace;
      if (!ns || ns.length === 0) return;
      const steps = extractSubAgentSteps(data);
      if (steps.length === 0) return;
      const key = ns.join("|");
      setSubAgentActivity((prev) => ({
        ...prev,
        [key]: [...(prev[key] ?? []), ...steps],
      }));
    },
    experimental_thread: thread,
  });

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
  const [fetchedThreadId, setFetchedThreadId] = useState<string | null>(null);
  const recoveryRunRef = useRef(0);

  // Per-thread model override. When set, gets folded into
  // `configurable.model` on every `stream.submit` — the backend's
  // `configurable_model` middleware
  // (EvoScientist/middleware/configurable_model.py) is what actually swaps
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
    if (!threadId) {
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
    let cancelled = false;
    void (async () => {
      try {
        const t = (await client.threads.get(threadId)) as {
          metadata?: Record<string, unknown>;
        };
        if (cancelled) return;
        const raw = (t.metadata ?? {})[MODEL_OVERRIDE_METADATA_KEY];
        if (
          raw &&
          typeof raw === "object" &&
          typeof (raw as { model?: unknown }).model === "string"
        ) {
          const r = raw as { model: string; model_provider?: unknown };
          setModelOverrideState({
            model: r.model,
            model_provider:
              typeof r.model_provider === "string"
                ? r.model_provider
                : undefined,
          });
        } else {
          setModelOverrideState(null);
        }
      } catch {
        if (!cancelled) setModelOverrideState(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, threadId]);

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
  useEffect(() => {
    if (!threadId) {
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedThreadId(null);
      setResolvedInterruptKey(null);
      return;
    }
    if (stream.isLoading) {
      recoveryRunRef.current += 1;
      setFetchedInterrupt(undefined);
      setResolvedInterruptKey(null);
      return;
    }
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
        const msgs = threadRecord.values?.messages;
        const pending = latestTaskInterrupt(state.tasks);
        const stillPending = Array.isArray(state.next) && state.next.length > 0;
        const safePending = normalizePendingInterrupt(pending);
        if (safePending && hasActionableInterrupt(safePending)) {
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
          }
          return;
        }
        // Backfill only after the live stream is idle. During active streaming the
        // live message list owns rendering; this recovery loop is for dropped tail
        // state after the stream has settled.
        if (Array.isArray(msgs) && msgs.length > baseline) {
          setFetchedThreadId(threadId);
          setFetchedMessages(msgs);
        }
        if (!stillPending) {
          // The server has no pending task/interrupt anymore. Record the stale
          // live interrupt's identity so the getter suppresses ONLY that one
          // (composer unlocks after approving) — a new interrupt still shows.
          setFetchedInterrupt(undefined);
          setResolvedInterruptKey(interruptValueKey(stream.interrupt));
          if (Array.isArray(msgs)) {
            setFetchedThreadId(threadId);
            setFetchedMessages(msgs);
          }
          return;
        }
        // Keep polling only while the run is still working server-side; a
        // finished run (next empty) won't produce anything more.
        if (stillPending && tries < MAX_TRIES && !cancelled) {
          timer = setTimeout(attempt, 1000);
        }
      } catch {
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
  }, [threadId, stream.interrupt, stream.isLoading, client]);

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
    if (modelOverride) {
      configurable.model = modelOverride.model;
      if (modelOverride.model_provider) {
        configurable.model_provider = modelOverride.model_provider;
      }
    }
    return { ...base, configurable };
  }, [activeAssistant?.config, modelOverride]);

  const sendMessage = useCallback(
    (content: string) => {
      // Drop any settled-run snapshot up front. Otherwise, until `isLoading`
      // flips true (and the effect above clears it), a previous run's
      // `fetchedMessages` can still out-count `stream.messages` and shadow the
      // just-added optimistic user message — making it flicker/vanish.
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedThreadId(null);
      setResolvedInterruptKey(null);
      recoveryRunRef.current += 1;
      const newMessage: Message = { id: uuidv4(), type: "human", content };
      stream.submit(
        { messages: [newMessage] },
        {
          optimisticValues: (prev) => ({
            messages: [...(prev.messages ?? []), newMessage],
          }),
          config: buildRunConfig(),
          streamSubgraphs: true,
          streamMode: ["updates"],
          streamResumable: true,
          onDisconnect: "continue",
        }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, buildRunConfig, onHistoryRevalidate]
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

  const resumeInterrupt = useCallback(
    (value: any) => {
      // Same as sendMessage: clear the prior snapshot before resuming so a stale
      // fetchedInterrupt/fetchedMessages can't briefly re-surface a resolved
      // approval card or shadow the resumed run's messages.
      setFetchedInterrupt(undefined);
      setFetchedMessages(null);
      setFetchedThreadId(null);
      setResolvedInterruptKey(null);
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
    [stream, buildRunConfig, onHistoryRevalidate]
  );

  const stopStream = useCallback(() => {
    stream.stop();
  }, [stream]);

  return {
    stream,
    todos: stream.values.todos ?? [],
    files: stream.values.files ?? {},
    email: stream.values.email,
    asyncTasks: stream.values.async_tasks ?? {},
    summarizationEvent: parseSummarizationEvent(
      stream.values._summarization_event
    ),
    ui: stream.values.ui,
    setFiles,
    messages,
    isLoading: stream.isLoading,
    isThreadLoading: stream.isThreadLoading,
    interrupt,
    sendMessage,
    stopStream,
    resumeInterrupt,
    subAgentActivity,
    modelOverride,
    setModelOverride,
  };
}
