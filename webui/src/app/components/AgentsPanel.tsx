"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  BellRing,
  Bot,
  ChevronDown,
  ChevronRight,
  CornerUpLeft,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useQueryState } from "nuqs";
import { toast } from "sonner";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import {
  type EnrichedAsyncTask,
  ASYNC_STATUS_META,
  agentLabel,
  asyncTaskReportKey,
  countRunning,
  formatElapsed,
  isTerminalStatus,
  type MainChatReporter,
  normalizeAsyncStatus,
} from "@/lib/asyncAgents";
import { useAsyncAgents } from "@/app/hooks/useAsyncAgents";
import { useAutoNotify } from "@/app/hooks/useAutoNotify";
import { initializeThreadAutoNotifyReports } from "@/lib/autoNotify";
import {
  messagesToSubAgentSteps,
  type SubAgentStep,
} from "@/lib/subAgentActivity";
import { SubAgentSteps } from "@/app/components/SubAgentSteps";

interface TaskDetail {
  loading: boolean;
  error: string | null;
  prompt: string;
  steps: SubAgentStep[];
}

/** Turn a task thread's persisted messages into a prompt + renderable steps
 *  (tool calls with args + paired results + markdown text, same as the main
 *  agent — reused via {@link SubAgentSteps}). */
function buildDetail(messages: unknown[]): {
  prompt: string;
  steps: SubAgentStep[];
} {
  let prompt = "";
  for (const raw of messages) {
    const m = raw as { type?: string; content?: unknown };
    if (m.type === "human") {
      const text = extractStringFromMessageContent(
        m as Parameters<typeof extractStringFromMessageContent>[0]
      ).trim();
      if (text) {
        prompt = text;
        break;
      }
    }
  }
  return { prompt, steps: messagesToSubAgentSteps(messages) };
}

function StatusDot({ status }: { status: string }) {
  const meta = ASYNC_STATUS_META[normalizeAsyncStatus(status)];
  return (
    <span
      className={cn(
        "size-2 shrink-0 rounded-full",
        meta.dot,
        meta.pulse && "animate-pulse"
      )}
      aria-hidden="true"
    />
  );
}

function elapsedOf(task: EnrichedAsyncTask, now: number): string {
  const status = normalizeAsyncStatus(task.liveStatus);
  // Expired tasks have no reliable end time (their run is gone) — show none
  // rather than a ticking or made-up duration.
  if (status === "expired") return "";
  const running = status === "running";
  const end = running ? now : task.endedAt ? Date.parse(task.endedAt) : now;
  return formatElapsed(task.startedAt ?? task.created_at, end);
}

/**
 * Right-inspector "Agents" board. Shows the background async sub-agents (writing
 * / data-analysis) the active conversation launched, with their REAL run status
 * and elapsed/duration; expand a task to see its live steps (from its own
 * thread). Polling is in {@link useAsyncAgents} and stops when this is unmounted
 * (i.e. the Agents tab is closed).
 */
interface AgentsPanelProps {
  // Submit a message on the main thread (loops an async result back to the main
  // agent). Returns false if the main chat is mid-run. Null when the chat view
  // isn't mounted (Skills/Memory) — the "Notify main chat" button then disables.
  onReportToMainChat?: MainChatReporter | null;
}

export function AgentsPanel({ onReportToMainChat }: AgentsPanelProps) {
  const client = useClient();
  const [threadId] = useQueryState("threadId");
  const { tasks, loaded, error, refresh } = useAsyncAgents(threadId);
  const [now, setNow] = useState(() => Date.now());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, TaskDetail>>({});
  const terminalDetailSignaturesRef = useRef(new Map<string, string>());
  // Which tasks have been reported to the main chat this session (so the button
  // flips to "Reported" and we don't double-poke the main agent).
  const [reported, setReported] = useState<Record<string, boolean>>({});
  // Per-thread "auto-report finished agents to the main chat" toggle. The actual
  // auto-injection runs in ChatInterface (always mounted on the chat view); here
  // we just own the switch. Shared reactively via useAutoNotify.
  const [autoNotify, setAutoNotify] = useAutoNotify(threadId);
  // Hidden power-user feature: a tiny composer per expanded task that sends a
  // message straight to that sub-agent's own thread. Note this is a SIDE channel
  // — the reply lands in the sub-agent's thread (shown here), the main agent
  // doesn't see it — there is no auto loop-back to the parent agent.
  const [chatInput, setChatInput] = useState<Record<string, string>>({});
  const [chatBusy, setChatBusy] = useState<Record<string, boolean>>({});
  const [chatError, setChatError] = useState<Record<string, string | null>>({});
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Smooth elapsed-time ticker for running tasks.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  // Send one message to a sub-agent's own thread and fold its reply back into the
  // steps. Uses runs.wait (blocks until the run finishes) — no attachments, no
  // approvals: a minimal "poke the worker" box. Disabled while a run is active.
  const sendToAgent = async (task: EnrichedAsyncTask) => {
    const text = (chatInput[task.task_id] ?? "").trim();
    if (!text || chatBusy[task.task_id]) return;
    setChatBusy((b) => ({ ...b, [task.task_id]: true }));
    setChatError((e) => ({ ...e, [task.task_id]: null }));
    try {
      const values = (await client.runs.wait(task.thread_id, task.agent_name, {
        input: { messages: [{ type: "human", content: text }] },
      })) as { messages?: unknown[] } | null;
      if (!mountedRef.current) return;
      const messages = Array.isArray(values?.messages) ? values.messages : [];
      if (messages.length === 0) {
        // The run came back without a transcript (server-side error path) —
        // keep the existing steps and the draft instead of overwriting the
        // detail with an empty "Steps (0)" (which would stick: terminal tasks
        // are signature-deduped and never re-fetched).
        setChatError((e) => ({
          ...e,
          [task.task_id]:
            "Agent 未返回回答，可能遇到了错误。",
        }));
        return;
      }
      setChatInput((i) => ({ ...i, [task.task_id]: "" }));
      const { prompt, steps } = buildDetail(messages);
      setDetails((prev) => ({
        ...prev,
        [task.task_id]: { loading: false, error: null, prompt, steps },
      }));
    } catch (err) {
      if (!mountedRef.current) return;
      // The SDK raises run errors as plain Errors ("<error>: <message>") and
      // transport problems as HTTPError (numeric status) / TypeError (fetch) —
      // tell the user which side failed.
      const transport =
        err instanceof TypeError ||
        (typeof err === "object" &&
          err !== null &&
          typeof (err as { status?: unknown }).status === "number");
      setChatError((e) => ({
        ...e,
        [task.task_id]: transport
          ? "无法连接此 Agent，请重试。"
          : "Agent 处理此追问时发生错误。",
      }));
    } finally {
      if (mountedRef.current) {
        setChatBusy((b) => ({ ...b, [task.task_id]: false }));
      }
    }
  };

  // Loop a finished task's result back to the MAIN agent: inject the exact
  // "[Async tasks update]" signal block as a user turn on the main thread (via
  // the registered notify hook). The main agent recognizes it and calls
  // check_async_task(task_id) to fetch the real result — zero backend change.
  const reportToMain = (task: EnrichedAsyncTask) => {
    if (!onReportToMainChat || !threadId) {
      toast.error("请打开对应会话后再通知主 Agent。" );
      return;
    }
    const result = onReportToMainChat(task, threadId);
    if (result === "sent") {
      setReported((r) => ({ ...r, [asyncTaskReportKey(task)]: true }));
      toast.success("已通知主会话。" );
      return;
    }
    if (result === "duplicate") {
      setReported((r) => ({ ...r, [asyncTaskReportKey(task)]: true }));
      toast.info("此结果已发送到主会话。" );
      return;
    }
    toast.error(
      result === "wrong-thread"
        ? "当前会话已更改，请重新打开此任务后重试。"
        : "主会话正忙，请等待空闲后重试。"
    );
  };

  const toggleAutoNotify = () => {
    if (!threadId) return;
    if (!autoNotify) {
      initializeThreadAutoNotifyReports(
        threadId,
        tasks
          .filter((task) => isTerminalStatus(task.liveStatus))
          .map(asyncTaskReportKey)
      );
    }
    setAutoNotify(!autoNotify);
  };

  // Fetch the expanded task's own thread for its steps; re-fetch whenever the
  // task list refreshes (so a running task's steps stay live).
  useEffect(() => {
    if (!expandedId) return;
    const task = tasks.find((t) => t.task_id === expandedId);
    if (!task) return;
    // Expired = the task's thread is gone; getState would just 404 on every
    // poll. The expanded card renders an explanatory note instead.
    if (normalizeAsyncStatus(task.liveStatus) === "expired") return;
    const running = normalizeAsyncStatus(task.liveStatus) === "running";
    const terminalSignature = [
      task.run_id,
      task.last_updated_at,
      task.endedAt,
    ].join(":");
    if (
      !running &&
      terminalDetailSignaturesRef.current.get(expandedId) === terminalSignature
    ) {
      return;
    }
    let cancelled = false;
    setDetails((prev) => ({
      ...prev,
      [expandedId]: {
        loading: !prev[expandedId],
        error: null,
        prompt: prev[expandedId]?.prompt ?? "",
        steps: prev[expandedId]?.steps ?? [],
      },
    }));
    (async () => {
      try {
        const state = (await client.threads.getState(task.thread_id)) as {
          values?: { messages?: unknown[] };
        };
        if (cancelled) return;
        const { prompt, steps } = buildDetail(state.values?.messages ?? []);
        if (!running) {
          terminalDetailSignaturesRef.current.set(
            expandedId,
            terminalSignature
          );
        }
        setDetails((prev) => ({
          ...prev,
          [expandedId]: { loading: false, error: null, prompt, steps },
        }));
      } catch {
        if (cancelled) return;
        terminalDetailSignaturesRef.current.delete(expandedId);
        setDetails((prev) => ({
          ...prev,
          [expandedId]: {
            loading: false,
            error: "无法加载此 Agent 的步骤。",
            prompt: prev[expandedId]?.prompt ?? "",
            steps: prev[expandedId]?.steps ?? [],
          },
        }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expandedId, tasks, client]);

  const runningCount = useMemo(() => countRunning(tasks), [tasks]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 px-1 pb-2">
        <p className="text-xs text-muted-foreground">
          {runningCount > 0
            ? `${runningCount} 个正在运行`
            : tasks.length > 0
            ? `${tasks.length} total`
            : "后台 Agent"}
        </p>
        <div className="flex items-center gap-1">
          {/* Auto-report toggle: when on, finished agents loop back to the main
              chat automatically (no need to click "Notify main chat" each time). */}
          <button
            type="button"
            onClick={toggleAutoNotify}
            aria-pressed={autoNotify}
            aria-label={`自动通知${autoNotify ? "已开启" : "已关闭"}`}
            title={
              autoNotify
                ? "自动通知已开启：Agent 完成后会自动发送到主会话"
                : "自动通知已关闭：请使用各 Agent 的“通知主会话”按钮"
            }
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              autoNotify
                ? "bg-accent text-[var(--brand)]"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            )}
          >
            <BellRing
              className="size-3.5"
              aria-hidden="true"
            />
            <span className="hidden min-[340px]:inline">
              自动通知{autoNotify ? "已开启" : "已关闭"}
            </span>
          </button>
          <button
            type="button"
            onClick={refresh}
            aria-label="刷新 Agent"
            title="刷新"
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw
              className="size-3.5"
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      {error && (
        <p className="px-1 text-xs text-[var(--color-error)]">{error}</p>
      )}

      {!loaded && (
        <div className="flex items-center gap-2 px-1 py-4 text-xs text-muted-foreground">
          <Loader2
            className="size-4 animate-spin"
            aria-hidden="true"
          />
          加载中…
        </div>
      )}

      {loaded && tasks.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
          <Bot
            className="size-9 text-muted-foreground/60"
            aria-hidden="true"
          />
          <p className="text-sm text-muted-foreground">
            暂无后台 Agent。
          </p>
          <p className="text-xs text-muted-foreground/80">
            金乌委派的长任务（如写作、数据分析）会在后台运行，并显示在这里。
          </p>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
        {tasks.map((task) => {
          const status = normalizeAsyncStatus(task.liveStatus);
          const meta = ASYNC_STATUS_META[status];
          const expanded = expandedId === task.task_id;
          const expired = status === "expired";
          const detail = details[task.task_id];
          return (
            <div
              key={task.task_id}
              className="rounded-md border border-border bg-background"
            >
              <button
                type="button"
                onClick={() => setExpandedId(expanded ? null : task.task_id)}
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                aria-expanded={expanded}
              >
                {expanded ? (
                  <ChevronDown
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                ) : (
                  <ChevronRight
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                )}
                <StatusDot status={task.liveStatus} />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {agentLabel(task.agent_name)}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {meta.label}
                  {(() => {
                    const e = elapsedOf(task, now);
                    return e ? ` · ${e}` : "";
                  })()}
                </span>
              </button>

              {expanded && expired && (
                <div className="border-t border-border px-2.5 py-2 text-xs">
                  <p className="text-muted-foreground">
                    此任务运行于后端重启之前，相关记录已丢失，因此无法继续加载步骤和结果；其生成的文件仍保留在工作区中。
                  </p>
                </div>
              )}

              {expanded && !expired && (
                <div className="border-t border-border px-2.5 py-2 text-xs">
                  {detail?.loading && (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2
                        className="size-3.5 animate-spin"
                        aria-hidden="true"
                      />
                      正在加载步骤…
                    </div>
                  )}
                  {detail?.error && (
                    <p className="text-[var(--color-error)]">{detail.error}</p>
                  )}
                  {detail && !detail.loading && !detail.error && (
                    <div className="flex flex-col gap-2">
                      {detail.prompt && (
                        <div>
                          <p className="font-semibold text-foreground">任务</p>
                          <p className="whitespace-pre-wrap break-words text-muted-foreground">
                            {detail.prompt.length > 600
                              ? detail.prompt.slice(0, 600) + "…"
                              : detail.prompt}
                          </p>
                        </div>
                      )}
                      <div>
                        <p className="mb-1 font-semibold text-foreground">
                          步骤{" "}
                          <span className="font-normal text-muted-foreground">
                            ({detail.steps.length})
                          </span>
                        </p>
                        {detail.steps.length === 0 ? (
                          <p className="text-muted-foreground">暂无步骤。</p>
                        ) : (
                          <SubAgentSteps
                            steps={detail.steps}
                            compact
                          />
                        )}
                      </div>

                      {/* Loop this finished task's result back to the MAIN agent.
                          When auto-report is on it's sent automatically, so the
                          manual button is replaced by a note (avoids a double
                          inject — auto and manual track dedup separately). */}
                      {isTerminalStatus(task.liveStatus) && (
                        <div className="mt-1 border-t border-border pt-2">
                          {autoNotify ? (
                            <p className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                              <BellRing
                                className="size-3.5 text-[var(--brand)]"
                                aria-hidden="true"
                              />
                              自动通知已开启 · 后续完成结果将通知主会话
                            </p>
                          ) : (
                            <button
                              type="button"
                              onClick={() => reportToMain(task)}
                              disabled={
                                !onReportToMainChat ||
                                !!reported[asyncTaskReportKey(task)]
                              }
                              title={
                                onReportToMainChat
                                  ? "将此结果发送给主 Agent"
                                  : "打开会话后通知主 Agent"
                              }
                              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <CornerUpLeft
                                className="size-3.5 text-[var(--brand)]"
                                aria-hidden="true"
                              />
                              {reported[asyncTaskReportKey(task)]
                                ? "已通知主会话"
                                : "通知主会话"}
                            </button>
                          )}
                        </div>
                      )}

                      {(() => {
                        const running =
                          normalizeAsyncStatus(task.liveStatus) === "running";
                        const busy = !!chatBusy[task.task_id];
                        return (
                          <div className="mt-1 border-t border-border pt-2">
                            <p className="mb-1 text-[11px] text-muted-foreground">
                              直接追问 · 不通知主会话
                            </p>
                            <form
                              onSubmit={(e) => {
                                e.preventDefault();
                                sendToAgent(task);
                              }}
                              className="flex items-center gap-1.5"
                            >
                              <input
                                type="text"
                                name={`agent-message-${task.task_id}`}
                                autoComplete="off"
                                value={chatInput[task.task_id] ?? ""}
                                onChange={(e) =>
                                  setChatInput((i) => ({
                                    ...i,
                                    [task.task_id]: e.target.value,
                                  }))
                                }
                                onKeyDown={(e) => {
                                  // Don't submit while an IME is composing (e.g.
                                  // Enter to pick a Chinese candidate).
                                  if (
                                    e.key === "Enter" &&
                                    (e.nativeEvent.isComposing ||
                                      e.keyCode === 229)
                                  ) {
                                    e.preventDefault();
                                  }
                                }}
                                disabled={running || busy}
                                placeholder={
                                  running
                                    ? "Agent 正在工作…"
                                    : "向此 Agent 直接追问…"
                                }
                                aria-label="向此 Agent 发送消息"
                                className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                              />
                              <button
                                type="submit"
                                disabled={
                                  running ||
                                  busy ||
                                  !(chatInput[task.task_id] ?? "").trim()
                                }
                                aria-label="发送"
                                title="发送给此 Agent"
                                className="inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--brand-solid)] text-[var(--brand-foreground)] transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
                              >
                                {busy ? (
                                  <Loader2
                                    className="size-3.5 animate-spin"
                                    aria-hidden="true"
                                  />
                                ) : (
                                  <ArrowUp
                                    className="size-3.5"
                                    aria-hidden="true"
                                  />
                                )}
                              </button>
                            </form>
                            {chatError[task.task_id] && (
                              <p
                                className="mt-1 text-[var(--color-error)]"
                                role="status"
                                aria-live="polite"
                              >
                                {chatError[task.task_id]}
                              </p>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
