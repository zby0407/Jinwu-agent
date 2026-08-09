"use client";

import React, {
  useState,
  useRef,
  useCallback,
  useEffect,
  useMemo,
  FormEvent,
  Fragment,
} from "react";
import { Button } from "@/components/ui/button";
import {
  Square,
  ArrowUp,
  CheckCircle,
  Clock,
  Circle,
  FileIcon,
  FolderOpen,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Paperclip,
  X,
  Pencil,
  CornerDownRight,
  Trash2,
  GripVertical,
  Ellipsis,
  ListX,
} from "lucide-react";
import { ChatMessage } from "@/app/components/ChatMessage";
import {
  ActionGroup,
  type GroupedActionItem,
} from "@/app/components/ActionGroup";
import { CompactionSummary } from "@/app/components/CompactionSummary";
import { ResearchDashboard } from "@/app/components/ResearchDashboard";
import { ResearchReviewPanel } from "@/app/components/ResearchReviewPanel";
import { isSummarizationMessage } from "@/lib/summarization";
import { useCollapseAgentActions } from "@/lib/uiSettings";
import {
  AskUserInterrupt,
  type AskUserQuestion,
} from "@/app/components/AskUserInterrupt";
import type {
  TodoItem,
  ToolCall,
  ActionRequest,
  ReviewConfig,
} from "@/app/types/types";
import { Assistant, Message } from "@langchain/langgraph-sdk";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { useChatContext } from "@/providers/ChatProvider";
import { cn } from "@/lib/utils";
import { formatModel } from "@/lib/model";
import {
  getThreadAutoApprove,
  setThreadAutoApprove,
  migrateNewThreadAutoApprove,
} from "@/lib/autoApprove";
import {
  agentLabel,
  asyncTaskReportKey,
  asyncUpdateMatchesTask,
  asyncUpdateMessageKey,
  countRunning,
  formatAsyncUpdateMessage,
  isTerminalStatus,
  type MainChatReporter,
} from "@/lib/asyncAgents";
import { useAsyncAgents } from "@/app/hooks/useAsyncAgents";
import { useAutoNotify } from "@/app/hooks/useAutoNotify";
import {
  getThreadAutoNotifyReportedKeys,
  initializeThreadAutoNotifyReports,
  isThreadAutoNotifyInitialized,
  markThreadAutoNotifyReported,
} from "@/lib/autoNotify";
import { lastTextOf, type SubAgentStep } from "@/lib/subAgentActivity";
import { useStickToBottom } from "use-stick-to-bottom";
import { FilesPopover } from "@/app/components/TasksFilesSidebar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useQueryState } from "nuqs";
import { toast } from "sonner";
import { WorkspaceFileDialog } from "@/app/components/WorkspaceFileDialog";
import { MemoryFileDialog } from "@/app/components/MemoryFileDialog";
import { FILE_LINK_EVENT, type FileLinkEventDetail } from "@/lib/fileLink";
import {
  RESEARCH_MESSAGE_NAVIGATE_EVENT,
  type ResearchMessageNavigateDetail,
} from "@/lib/researchNavigation";
import {
  COMMON_MODELS,
  parseModelCommand,
  type ModelOverride,
} from "@/lib/modelCommand";
import { useAvailableModels } from "@/app/hooks/useAvailableModels";
import { useClient } from "@/providers/ClientProvider";

type DashboardNavTarget =
  | {
      view: "memory";
      tab: "identity" | "knowledge" | "history";
      obsId?: string;
      execId?: string;
    }
  | { view: "schedule" }
  | { view: "workspace" };

interface ChatInterfaceProps {
  assistant: Assistant | null;
  // Open the right inspector on its Agents tab (composer "agents running" pulse).
  onShowAgents?: () => void;
  // Navigate to a memory tab / the schedule view from the empty-state dashboard.
  onNavigate?: (target: DashboardNavTarget) => void;
  // Open a pinned thread from the empty-state dashboard.
  onOpenThread?: (id: string) => void;
  // Whether the workspace inspector is currently visible.
  workspaceOpen?: boolean;
  // Register a "submit a message on THIS (main) thread" function up to page so
  // the Agents board can loop an async result back to the main agent. Returns
  // false if the main chat is mid-run (can't take a turn). Cleared on unmount.
  onNotifyReady?: (notify: MainChatReporter | null) => void;
}

const SUGGESTED_PROMPTS = [
  "调研某个主题的最新论文",
  "设计一份实验方案",
  "分析工作区文件",
];

interface UploadedWorkspaceFile {
  name: string;
  path: string;
  size: number;
}

// A message typed while the agent is busy. It waits in the queue and is sent
// verbatim (append-only — never replaces what's already in the thread) once the
// current turn finishes or is stopped. Each carries its own attached files.
interface QueuedMessage {
  id: number;
  text: string;
  files: UploadedWorkspaceFile[];
  threadId: string | null;
}

// Build the message body sent to the backend, appending the same workspace-file
// annotation handleSubmit uses so queued messages keep their attachments.
function formatMessageWithFiles(
  text: string,
  files: UploadedWorkspaceFile[]
): string {
  const workspaceFiles =
    files.length > 0
      ? `\n\nWorkspace files uploaded for this request:\n${files
          .map((file) => `- ${file.path}`)
          .join("\n")}`
      : "";
  return `${text}${workspaceFiles}`;
}

function parseToolArgs(rawArgs: unknown): Record<string, unknown> {
  if (rawArgs && typeof rawArgs === "object") {
    return rawArgs as Record<string, unknown>;
  }
  if (typeof rawArgs !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(rawArgs);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function getMessageToolCalls(message: Message): Array<{
  id?: string;
  name: string;
  args: Record<string, unknown>;
}> {
  const messageWithTools = message as Message & {
    tool_calls?: Array<{ name?: string }>;
  };
  const toolCalls: Array<{
    id?: string;
    function?: { name?: string; arguments?: unknown };
    name?: string;
    type?: string;
    args?: unknown;
    input?: unknown;
  }> = [];

  if (
    message.additional_kwargs?.tool_calls &&
    Array.isArray(message.additional_kwargs.tool_calls)
  ) {
    toolCalls.push(...message.additional_kwargs.tool_calls);
  } else if (
    messageWithTools.tool_calls &&
    Array.isArray(messageWithTools.tool_calls)
  ) {
    toolCalls.push(
      ...messageWithTools.tool_calls.filter(
        (toolCall: { name?: string }) => toolCall.name !== ""
      )
    );
  } else if (Array.isArray(message.content)) {
    toolCalls.push(
      ...message.content.filter(
        (block: { type?: string }) => block.type === "tool_use"
      )
    );
  }

  return toolCalls.map((toolCall) => {
    const rawArgs =
      toolCall.function?.arguments || toolCall.args || toolCall.input || {};
    return {
      id: toolCall.id,
      name: toolCall.function?.name || toolCall.name || toolCall.type || "",
      args: parseToolArgs(rawArgs),
    };
  });
}

const getStatusIcon = (status: TodoItem["status"], className?: string) => {
  switch (status) {
    case "completed":
      return (
        <CheckCircle
          size={16}
          className={cn("text-[var(--color-success)]", className)}
        />
      );
    case "in_progress":
      return (
        <Clock
          size={16}
          className={cn("text-[var(--color-warning)]", className)}
        />
      );
    default:
      return (
        <Circle
          size={16}
          className={cn("text-[var(--color-text-tertiary)]", className)}
        />
      );
  }
};

export const ChatInterface = React.memo<ChatInterfaceProps>(
  ({
    assistant,
    onShowAgents,
    onNotifyReady,
    onNavigate,
    onOpenThread,
    workspaceOpen,
  }) => {
    const [metaOpen, setMetaOpen] = useState<"tasks" | "files" | null>(null);
    const tasksContainerRef = useRef<HTMLDivElement | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const composerResizeRef = useRef<{
      pointerId: number;
      startY: number;
      startHeight: number;
    } | null>(null);
    const uploadInputRef = useRef<HTMLInputElement | null>(null);
    const client = useClient();
    const [threadId, setThreadId] = useQueryState("threadId");

    const [input, setInput] = useState("");
    const [composerHeight, setComposerHeight] = useState(64);
    const [pendingFiles, setPendingFiles] = useState<UploadedWorkspaceFile[]>(
      []
    );
    const [isUploadingFiles, setIsUploadingFiles] = useState(false);
    const [workspaceDir, setWorkspaceDir] = useState<string | null>(null);

    useEffect(() => {
      setWorkspaceDir(null);
      if (!threadId) return;
      let cancelled = false;
      let retryTimer: ReturnType<typeof setTimeout> | null = null;
      const loadWorkspace = async (attempt: number) => {
        try {
          const response = await fetch(
            `/api/workspace?${new URLSearchParams({ threadId, path: "" })}`
          );
          const data = response.ok
            ? ((await response.json()) as { dir?: string })
            : null;
          if (!cancelled && data?.dir) {
            setWorkspaceDir(data.dir);
            return;
          }
        } catch {
          // The first checkpoint may not have created the binding yet.
        }
        if (!cancelled && attempt < 10) {
          retryTimer = setTimeout(() => void loadWorkspace(attempt + 1), 500);
        }
      };
      void loadWorkspace(0);
      return () => {
        cancelled = true;
        if (retryTimer) clearTimeout(retryTimer);
      };
    }, [threadId]);
    // Messages typed while the agent is busy are queued. They
    // drain one-per-idle-window into the thread once it's free. A ref mirrors the
    // latest queue so event handlers (key ↑, edit) read current state without
    // being recreated; queueIdRef hands out stable keys.
    const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([]);
    const queuedMessagesRef = useRef<QueuedMessage[]>(queuedMessages);
    queuedMessagesRef.current = queuedMessages;
    const queueIdRef = useRef(0);
    const draggedQueuedMessageIdRef = useRef<number | null>(null);
    // Inline file paths in agent messages are rendered as click-to-open links
    // by MarkdownContent. They dispatch a window event with the resolved
    // workspace / memory path; we open the matching modal over the chat so
    // workspace and memory feel uniform to the user (no view switch).
    const [workspaceFilePath, setWorkspaceFilePath] = useState<string | null>(
      null
    );
    const [memoryFilePath, setMemoryFilePath] = useState<string | null>(null);
    useEffect(() => {
      const onOpenFile = (e: Event) => {
        const detail = (e as CustomEvent<FileLinkEventDetail>).detail;
        if (!detail) return;
        if (detail.kind === "memory") {
          setMemoryFilePath(detail.path);
        } else {
          setWorkspaceFilePath(detail.path);
        }
      };
      window.addEventListener(FILE_LINK_EVENT, onOpenFile);
      return () => window.removeEventListener(FILE_LINK_EVENT, onOpenFile);
    }, []);
    // Auto-approve is per-thread and persisted (see lib/autoApprove): it follows
    // the conversation across view switches (Skills/Memory unmount this), thread
    // switches, and reloads. Seed from storage for whatever thread is active on
    // mount so returning from another view restores the right setting.
    const [autoApprove, setAutoApproveState] = useState(() =>
      getThreadAutoApprove(threadId)
    );
    const [autoApproveDialogOpen, setAutoApproveDialogOpen] = useState(false);
    const [regenerateMessageId, setRegenerateMessageId] = useState<
      string | null
    >(null);
    const [modelPickerOpen, setModelPickerOpen] = useState(false);
    const [modelSearch, setModelSearch] = useState("");
    // Reset the search box every time the picker opens — stale filter state
    // surviving across opens would surprise the user.
    useEffect(() => {
      if (modelPickerOpen) setModelSearch("");
    }, [modelPickerOpen]);
    const {
      registry: modelRegistry,
      loading: modelRegistryLoading,
      error: modelRegistryError,
    } = useAvailableModels();
    // We're on the curated fallback list when the registry fetch settled
    // (not loading) but produced no entries — either an explicit error from
    // the backend's `/api/models` route, or a successful response that came
    // back empty. We log once so dev tools surface the cause.
    const isFallbackModelList =
      !modelRegistryLoading && modelRegistry.entries.length === 0;
    useEffect(() => {
      if (isFallbackModelList) {
        console.warn(
          "[model picker] using curated fallback list — registry fetch failed or empty",
          modelRegistryError ?? "(no error message)"
        );
      }
    }, [isFallbackModelList, modelRegistryError]);
    // Picker source: prefer the backend's authoritative registry; fall back
    // to the curated short list when the endpoint isn't available (older
    // deployment, network blip). Registry order is the rank the backend
    // recommends — we don't re-sort.
    const pickerModels = useMemo(() => {
      if (modelRegistry.entries.length > 0) {
        return modelRegistry.entries.map((e) => ({
          model: e.name,
          model_provider: e.provider,
        }));
      }
      return COMMON_MODELS.map((m) => ({
        model: m.model,
        model_provider: m.model_provider,
      }));
    }, [modelRegistry.entries]);
    // Case-insensitive substring filter on name + provider. Fuzzy match was
    // discussed but punted — substring catches the common "I know roughly
    // what I want" case and keeps the picker behavior predictable.
    const filteredPickerModels = useMemo(() => {
      const q = modelSearch.trim().toLowerCase();
      if (!q) return pickerModels;
      return pickerModels.filter((m) => {
        const provider = m.model_provider ?? "";
        return (
          m.model.toLowerCase().includes(q) ||
          provider.toLowerCase().includes(q)
        );
      });
    }, [pickerModels, modelSearch]);
    const autoApprovedRef = useRef<unknown>(null);
    const previousThreadIdRef = useRef(threadId);
    const migrateAutoApproveForCreatedThreadRef = useRef(false);
    const { scrollRef, contentRef, scrollToBottom, isAtBottom } =
      useStickToBottom();

    const {
      stream,
      messages,
      todos,
      files,
      ui,
      setFiles,
      isLoading,
      isStopping,
      isThreadLoading,
      interrupt,
      sendMessage,
      editMessage,
      regenerateMessage,
      selectBranch,
      stopStream,
      resumeInterrupt,
      subAgentActivity,
      asyncTasks,
      summarizationEvent,
      modelOverride,
      setModelOverride,
    } = useChatContext();
    const [pendingResearchFocus, setPendingResearchFocus] = useState<
      string | null
    >(null);
    const [focusedResearchMessage, setFocusedResearchMessage] = useState<
      string | null
    >(null);

    useEffect(() => {
      const onNavigate = (event: Event) => {
        const detail = (event as CustomEvent<ResearchMessageNavigateDetail>)
          .detail;
        if (!detail?.messageId) return;
        if (detail.branch !== undefined) selectBranch(detail.branch);
        setPendingResearchFocus(detail.messageId);
      };
      window.addEventListener(RESEARCH_MESSAGE_NAVIGATE_EVENT, onNavigate);
      return () =>
        window.removeEventListener(RESEARCH_MESSAGE_NAVIGATE_EVENT, onNavigate);
    }, [selectBranch]);

    useEffect(() => {
      if (!pendingResearchFocus) return;
      let cancelled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let attempts = 0;
      const locate = () => {
        if (cancelled) return;
        const escaped = CSS.escape(pendingResearchFocus);
        const target = document.querySelector<HTMLElement>(
          `[data-chat-message-id="${escaped}"]`
        );
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          setFocusedResearchMessage(pendingResearchFocus);
          setPendingResearchFocus(null);
          timer = setTimeout(() => setFocusedResearchMessage(null), 2200);
          return;
        }
        attempts += 1;
        if (attempts < 20) timer = setTimeout(locate, 100);
        else setPendingResearchFocus(null);
      };
      timer = setTimeout(locate, 0);
      return () => {
        cancelled = true;
        if (timer) clearTimeout(timer);
      };
    }, [messages, pendingResearchFocus]);

    const confirmRegenerate = useCallback(() => {
      if (!regenerateMessageId) return;
      const messageId = regenerateMessageId;
      setRegenerateMessageId(null);
      regenerateMessage(messageId);
    }, [regenerateMessage, regenerateMessageId]);

    // Count of background async sub-agents (writing / data-analysis) still
    // running — drives the composer's "agents running" pulse. We poll each task's
    // REAL run status (via useAsyncAgents) rather than trusting the conversation
    // state's cached `status`, which only updates when the agent checks and would
    // otherwise keep the pulse on forever. Only polls when tasks actually exist.
    const hasAsyncTasks = Object.keys(asyncTasks ?? {}).length > 0;
    const { tasks: liveAgentTasks } = useAsyncAgents(threadId, {
      enabled: hasAsyncTasks,
    });
    const runningAgents = useMemo(
      () => countRunning(liveAgentTasks),
      [liveAgentTasks]
    );

    // Auto-report: when on for this thread, a sub-agent that FINISHES while we're
    // watching is looped back to the main agent automatically (same signal as the
    // manual "Notify main chat" button — rendered as a system pill). We baseline
    // tasks already terminal at mount / when the toggle is switched on so we never
    // replay old completions, and only inject while the main chat is idle (one at
    // a time; isLoading gates the rest until the agent finishes the turn).
    const [autoNotify] = useAutoNotify(threadId);
    // Latch covering the gap between submitting an auto-report and `isLoading`
    // flipping true — without it a poll in that window could fire a SECOND report
    // and collide on the main thread. Cleared once the run is confirmed running.
    const autoFireInFlightRef = useRef(false);

    useEffect(() => {
      autoFireInFlightRef.current = false;
    }, [threadId]);

    // Once a run is actually in flight (isLoading true — from a user message, the
    // agent's own turn, or our auto-report), release the latch: the isLoading gate
    // now governs, and the next queued report fires when the thread next goes idle.
    useEffect(() => {
      if (isLoading) autoFireInFlightRef.current = false;
    }, [isLoading]);

    useEffect(() => {
      if (!liveAgentTasks || liveAgentTasks.length === 0) return;
      if (!threadId || !autoNotify) return;
      // One-time migration/baseline: existing terminal tasks predate the setting
      // and must not replay when this feature first appears or is restored.
      if (!isThreadAutoNotifyInitialized(threadId)) {
        initializeThreadAutoNotifyReports(
          threadId,
          liveAgentTasks
            .filter((task) => isTerminalStatus(task.liveStatus))
            .map(asyncTaskReportKey)
        );
        return;
      }
      // Don't fire when: off; the thread is busy (the agent's own turn takes the
      // slot); a report we just sent hasn't started yet; or the USER is composing
      // a query (draft text) — their message has priority, so we hold the queue
      // until the composer is clear. Pending completions stay unreported (= the
      // queue) and drain one per idle window.
      if (isLoading || autoFireInFlightRef.current || input.trim()) return;
      // User-queued messages take the idle slot first — hold auto-reports until
      // the user's own queue has drained.
      if (queuedMessages.length > 0) return;
      const reportedKeys = getThreadAutoNotifyReportedKeys(threadId);
      for (const t of liveAgentTasks) {
        if (!isTerminalStatus(t.liveStatus)) continue;
        const key = asyncTaskReportKey(t);
        if (reportedKeys.has(key)) continue;
        if (
          messages.some(
            (message) =>
              message.type === "human" &&
              asyncUpdateMatchesTask(
                extractStringFromMessageContent(message),
                t
              )
          )
        ) {
          markThreadAutoNotifyReported(threadId, key);
          continue;
        }
        autoFireInFlightRef.current = true;
        markThreadAutoNotifyReported(threadId, key);
        sendMessage(formatAsyncUpdateMessage(t));
        toast.success(
          `Auto-reported ${agentLabel(t.agent_name)} to the main chat.`
        );
        break; // one per idle window; the rest fire once this turn settles
      }
    }, [
      liveAgentTasks,
      autoNotify,
      isLoading,
      input,
      messages,
      sendMessage,
      threadId,
      queuedMessages,
    ]);

    // Re-engage stick-to-bottom whenever a new run starts (sending a message or
    // resuming an interrupt → isLoading flips true). Without this, if the user had
    // drifted even slightly off the bottom after the previous answer, a short new
    // reply would render below the fold and look like nothing happened.
    useEffect(() => {
      if (isLoading) void scrollToBottom();
    }, [isLoading, scrollToBottom]);

    // Register a "notify the main agent" hook up to page (Agents board → "Notify
    // main chat" loops an async result back here). A ref keeps the latest
    // sendMessage/isLoading so the once-registered closure always reads current
    // values. Returns false if a run is in flight (the agent can't take a turn).
    const notifyStateRef = useRef({
      sendMessage,
      isLoading,
      messages,
      threadId,
    });
    notifyStateRef.current = { sendMessage, isLoading, messages, threadId };
    const onNotifyReadyRef = useRef(onNotifyReady);
    onNotifyReadyRef.current = onNotifyReady;
    useEffect(() => {
      const notify: MainChatReporter = (task, expectedThreadId) => {
        const current = notifyStateRef.current;
        if (current.threadId !== expectedThreadId) return "wrong-thread";
        if (current.isLoading) return "busy";
        if (
          current.messages.some(
            (message) =>
              message.type === "human" &&
              asyncUpdateMatchesTask(
                extractStringFromMessageContent(message),
                task
              )
          )
        ) {
          return "duplicate";
        }
        markThreadAutoNotifyReported(
          expectedThreadId,
          asyncTaskReportKey(task)
        );
        current.sendMessage(formatAsyncUpdateMessage(task));
        return "sent";
      };
      onNotifyReadyRef.current?.(notify);
      return () => onNotifyReadyRef.current?.(null);
    }, []);

    // What model will the next turn use? The pill is forward-looking — it
    // updates the moment the user changes (or clears) the override, instead
    // of lingering on what just ran. Priority:
    //   1. Per-thread model override (`/model` command, picker) — the next
    //      run will use this verbatim.
    //   2. The model registry endpoint's reported deployment default —
    //      what the next run will use when no override is set.
    //   3. The assistant's configured default from `assistant.config.configurable`.
    //   4. Last AI message's `response_metadata.model_name` — final fallback
    //      when none of the above are available (older deployment without
    //      `/api/models`).
    // The endpoint's `default` can disagree with what the runtime actually
    // boots with; that's a backend honesty problem and tracked separately.
    // Token/context usage is intentionally NOT shown — the backend doesn't
    // persist usage_metadata, so it isn't reliably available here.
    const currentModel = useMemo(() => {
      if (modelOverride) {
        return formatModel(modelOverride.model, modelOverride.model_provider);
      }
      if (modelRegistry.defaultEntry) {
        return formatModel(
          modelRegistry.defaultEntry.name,
          modelRegistry.defaultEntry.provider ?? undefined
        );
      }
      const cfg = assistant?.config as
        | { configurable?: Record<string, unknown> }
        | undefined;
      const configurable = cfg?.configurable;
      if (configurable) {
        const info = formatModel(
          configurable.model ?? configurable.model_name,
          configurable.model_provider
        );
        if (info) return info;
      }
      for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i];
        if (m.type !== "ai") continue;
        const rm = m.response_metadata as Record<string, unknown> | undefined;
        const info = formatModel(
          rm?.model_name ?? rm?.model,
          rm?.model_provider
        );
        if (info) return info;
      }
      return null;
    }, [
      messages,
      modelOverride,
      assistant?.config,
      modelRegistry.defaultEntry,
    ]);

    // Bind captured sub-agent activity (keyed by subgraph namespace) to each task
    // tool call → its live steps. B': match a finished sub-agent to a task by its
    // final text == the task's result; assign still-running sub-agents to the
    // remaining task calls in order. Returns { taskToolCallId: SubAgentStep[] }.
    const subAgentSteps = useMemo(() => {
      const out: Record<string, SubAgentStep[]> = {};
      const nsKeys = Object.keys(subAgentActivity);
      if (nsKeys.length === 0) return out;

      const taskIds: string[] = [];
      const results: Record<string, string> = {};
      for (const m of messages) {
        if (m.type === "ai") {
          const tcs = (m as { tool_calls?: { id?: string; name?: string }[] })
            .tool_calls;
          for (const tc of tcs ?? []) {
            if (tc.name === "task" && tc.id) taskIds.push(tc.id);
          }
        } else if (m.type === "tool") {
          const id = (m as { tool_call_id?: string }).tool_call_id;
          if (id) results[id] = extractStringFromMessageContent(m);
        }
      }

      const norm = (s: string) => s.replace(/\s+/g, " ").trim();
      const claimed = new Set<string>();
      // 1) Finished tasks: match by output text.
      for (const id of taskIds) {
        const r = norm(results[id] ?? "");
        if (!r) continue;
        const key = nsKeys.find((k) => {
          if (claimed.has(k)) return false;
          const last = norm(lastTextOf(subAgentActivity[k]));
          return last !== "" && (r.includes(last) || last.includes(r));
        });
        if (key) {
          out[id] = subAgentActivity[key];
          claimed.add(key);
        }
      }
      // 2) Running tasks (no result yet): take remaining namespaces in order.
      const remaining = nsKeys.filter((k) => !claimed.has(k));
      let ri = 0;
      for (const id of taskIds) {
        if (out[id] || results[id]) continue;
        if (ri < remaining.length) out[id] = subAgentActivity[remaining[ri++]];
      }
      return out;
    }, [messages, subAgentActivity]);

    // While the agent waits on an *actionable* interrupt (approval or ask_user),
    // lock the composer so the user answers via the in-message controls — a free
    // message would cancel the pending tool call and corrupt the thread.
    // A bare/leftover interrupt value (e.g. after Stop) must NOT lock the input.
    const interruptValue = interrupt?.value as
      | { type?: string; action_requests?: unknown[] }
      | undefined;
    const hasPendingInterrupt =
      interruptValue?.type === "ask_user" ||
      (Array.isArray(interruptValue?.action_requests) &&
        interruptValue.action_requests.length > 0);
    const submitDisabled = isLoading || !assistant || hasPendingInterrupt;

    // Drain the user-message queue: when the thread goes idle (and no interrupt is
    // pending), send the head as the next turn. One per idle window — the
    // autoFireInFlightRef latch (shared with auto-report) covers the gap before
    // isLoading flips true so two messages can't race onto the thread. After Stop,
    // isLoading drops and the queue still drains (queued = intent to send).
    useEffect(() => {
      if (isLoading || hasPendingInterrupt || autoFireInFlightRef.current)
        return;
      if (queuedMessages.length === 0) return;
      const [head, ...rest] = queuedMessages;
      // Effects from the previous render still run once after a route/thread
      // change. Never let that stale queue drain into the newly selected thread.
      if (head.threadId !== threadId) return;
      autoFireInFlightRef.current = true;
      setQueuedMessages(rest);
      sendMessage(formatMessageWithFiles(head.text, head.files));
    }, [isLoading, hasPendingInterrupt, queuedMessages, sendMessage, threadId]);

    // Clear the queue when switching to a *different* conversation, but NOT on the
    // null→real-id transition that happens when the first message of a brand-new
    // chat creates its thread (the queue belongs to this same conversation).
    const queueThreadRef = useRef(threadId);
    useEffect(() => {
      const prev = queueThreadRef.current;
      queueThreadRef.current = threadId;
      if (prev === null && threadId !== null) {
        // The first submitted message creates the thread asynchronously. Carry
        // any follow-ups queued during that transition into the new thread.
        setQueuedMessages((messages) =>
          messages.map((message) =>
            message.threadId === null ? { ...message, threadId } : message
          )
        );
        return;
      }
      if (prev !== threadId) setQueuedMessages([]);
    }, [threadId]);

    const enableAutoApprove = useCallback(() => {
      setAutoApproveState(true);
      setThreadAutoApprove(threadId, true);
      setAutoApproveDialogOpen(false);
    }, [threadId]);

    const turnOffAutoApprove = useCallback(() => {
      setAutoApproveState(false);
      setThreadAutoApprove(threadId, false);
      setAutoApproveDialogOpen(false);
      autoApprovedRef.current = null;
    }, [threadId]);

    // Follow the thread: when the active thread changes, load THAT thread's saved
    // auto-approve instead of resetting. The null→real-id transition is the new
    // chat getting created on its first message — carry its sentinel setting over.
    useEffect(() => {
      const previousThreadId = previousThreadIdRef.current;
      if (previousThreadId === threadId) return;

      if (
        previousThreadId === null &&
        threadId !== null &&
        migrateAutoApproveForCreatedThreadRef.current
      ) {
        migrateNewThreadAutoApprove(threadId);
      } else if (previousThreadId === null && threadId !== null) {
        // The user selected an existing research from New Chat before sending.
        // Do not leak the pending-new-chat auto-approve sentinel onto that thread.
        setThreadAutoApprove(null, false);
      }

      setAutoApproveState(getThreadAutoApprove(threadId));
      autoApprovedRef.current = null;
      setAutoApproveDialogOpen(false);
      setPendingFiles([]);
      migrateAutoApproveForCreatedThreadRef.current = false;
      previousThreadIdRef.current = threadId;
    }, [threadId]);

    const handleFilesSelected = useCallback(
      async (event: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFiles = Array.from(event.target.files ?? []);
        event.target.value = "";
        if (selectedFiles.length === 0) return;

        setIsUploadingFiles(true);
        try {
          let taskThreadId = threadId;
          if (!taskThreadId) {
            const created = await client.threads.create({
              graphId: assistant?.graph_id,
              metadata: { project_id: "default" },
            });
            taskThreadId = created.thread_id;
            await setThreadId(taskThreadId);
          }
          const bindResponse = await fetch("/api/workspace/bind", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              threadId: taskThreadId,
              projectId: "default",
            }),
          });
          const bindData = (await bindResponse.json().catch(() => null)) as {
            binding?: { workspace?: string };
            error?: string;
          } | null;
          if (!bindResponse.ok) {
            throw new Error(bindData?.error || "无法绑定任务工作区。");
          }
          if (bindData?.binding?.workspace) {
            setWorkspaceDir(bindData.binding.workspace);
          }
          const formData = new FormData();
          selectedFiles.forEach((file) => formData.append("files", file));
          const response = await fetch(
            `/api/workspace/upload?${new URLSearchParams({
              threadId: taskThreadId,
            })}`,
            {
              method: "POST",
              body: formData,
            }
          );
          const data = (await response.json()) as {
            files?: UploadedWorkspaceFile[];
            error?: string;
          };
          if (!response.ok || !data.files) {
            throw new Error(data.error || "文件上传失败。");
          }
          setPendingFiles((currentFiles) => [...currentFiles, ...data.files!]);
          toast.success(`已将 ${data.files.length} 个文件上传到工作区。`);
        } catch (error) {
          toast.error(
            error instanceof Error ? error.message : "文件上传失败。"
          );
        } finally {
          setIsUploadingFiles(false);
        }
      },
      [assistant?.graph_id, client, setThreadId, threadId]
    );

    const removePendingFile = useCallback((filePath: string) => {
      setPendingFiles((currentFiles) =>
        currentFiles.filter((file) => file.path !== filePath)
      );
    }, []);

    // Apply a parsed `/model` command. Three forms:
    //   - show  → open the model picker dialog
    //   - reset → clear the per-thread override (revert to assistant default)
    //   - set   → persist the new override and confirm via toast. Names that
    //             aren't in our curated list are still accepted — the backend
    //             accepts anything `init_chat_model` knows, and we'd rather
    //             not block a power user on our curation lagging the registry.
    const applyModelCommand = useCallback(
      async (cmd: ReturnType<typeof parseModelCommand>) => {
        if (!cmd) return;
        if (cmd.kind === "show") {
          setModelPickerOpen(true);
          return;
        }
        // Pre-thread picks are staged in useChat and applied to the first
        // run; no threadId guard needed.
        try {
          if (cmd.kind === "reset") {
            await setModelOverride(null);
            toast.success("已恢复默认模型。");
          } else {
            const next: ModelOverride = {
              model: cmd.model,
              ...(cmd.provider ? { model_provider: cmd.provider } : {}),
            };
            await setModelOverride(next);
            toast.success(
              `模型已切换为 ${cmd.model}${
                cmd.provider ? ` (${cmd.provider})` : ""
              }。`
            );
          }
        } catch (err) {
          toast.error(
            err instanceof Error
              ? `无法更新模型：${err.message}`
              : "无法更新模型，请重试。"
          );
        }
      },
      [setModelOverride]
    );

    const handleSubmit = useCallback(
      (e?: FormEvent) => {
        if (e) {
          e.preventDefault();
        }
        const messageText = input.trim();
        if (!messageText) return;
        // Intercept `/model` before the agent sees it — purely client-side
        // state on the thread metadata. The textarea clears regardless of
        // outcome so the command echo doesn't linger.
        const modelCmd = parseModelCommand(messageText);
        if (modelCmd) {
          void applyModelCommand(modelCmd);
          setInput("");
          return;
        }
        // Can't compose with no assistant, a pending interrupt, or files still
        // uploading. (Unlike before, isLoading is NOT a blocker — see below.)
        if (!assistant || hasPendingInterrupt || isUploadingFiles) return;
        // Agent busy → queue it. The queue drains and sends
        // automatically once this turn finishes (or is stopped); the message is
        // appended as the next turn — it never replaces what's already running.
        if (isLoading) {
          setQueuedMessages((prev) => [
            ...prev,
            {
              id: (queueIdRef.current += 1),
              text: messageText,
              files: pendingFiles,
              threadId,
            },
          ]);
          setInput("");
          setPendingFiles([]);
          return;
        }
        migrateAutoApproveForCreatedThreadRef.current =
          threadId === null && autoApprove;
        sendMessage(formatMessageWithFiles(messageText, pendingFiles));
        setInput("");
        setPendingFiles([]);
      },
      [
        input,
        applyModelCommand,
        assistant,
        hasPendingInterrupt,
        autoApprove,
        isLoading,
        isUploadingFiles,
        pendingFiles,
        sendMessage,
        threadId,
      ]
    );

    // Pull a queued message back into the composer to edit it (also restores its
    // attached files), removing it from the queue. Only ever touches the unsent
    // draft — the running/sent messages in the thread are untouched.
    const editQueuedMessage = useCallback((id: number) => {
      const target = queuedMessagesRef.current.find((m) => m.id === id);
      if (!target) return;
      setQueuedMessages((prev) => prev.filter((m) => m.id !== id));
      setInput(target.text);
      setPendingFiles(target.files);
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (el) {
          el.focus();
          el.setSelectionRange(target.text.length, target.text.length);
        }
      });
    }, []);

    // "Steer": prioritize this instruction without interrupting the
    // active run. It remains in our visible queue and drains in the next idle
    // window, which preserves the same non-interrupting contract.
    const steerQueuedMessage = useCallback((id: number) => {
      setQueuedMessages((messages) => {
        const target = messages.find((message) => message.id === id);
        if (!target || messages[0]?.id === id) return messages;
        return [target, ...messages.filter((message) => message.id !== id)];
      });
      toast.info("此消息将在当前轮次结束后优先发送。");
    }, []);

    const moveQueuedMessage = useCallback((id: number, direction: -1 | 1) => {
      setQueuedMessages((messages) => {
        const from = messages.findIndex((message) => message.id === id);
        const to = from + direction;
        if (from < 0 || to < 0 || to >= messages.length) return messages;
        const next = [...messages];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      });
    }, []);

    const dropQueuedMessage = useCallback((targetId: number) => {
      const sourceId = draggedQueuedMessageIdRef.current;
      draggedQueuedMessageIdRef.current = null;
      if (sourceId === null || sourceId === targetId) return;
      setQueuedMessages((messages) => {
        const from = messages.findIndex((message) => message.id === sourceId);
        const to = messages.findIndex((message) => message.id === targetId);
        if (from < 0 || to < 0) return messages;
        const next = [...messages];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      });
    }, []);

    const removeQueuedMessage = useCallback((id: number) => {
      setQueuedMessages((prev) => prev.filter((m) => m.id !== id));
    }, []);

    const clearQueuedMessages = useCallback(() => {
      setQueuedMessages([]);
    }, []);

    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // The composer is locked only when an interrupt is pending (the textarea
        // is disabled then) — NOT while the agent is busy: typing + Enter queues a
        // follow-up rather than doing nothing.
        if (hasPendingInterrupt) return;
        // Don't submit while an IME is composing (e.g. pressing Enter to pick a
        // Chinese/Japanese/Korean candidate must confirm text, not send).
        if (e.nativeEvent.isComposing || e.keyCode === 229) return;
        // ↑ on an empty composer pulls the most recent queued message back to edit.
        if (
          e.key === "ArrowUp" &&
          input.length === 0 &&
          queuedMessagesRef.current.length > 0
        ) {
          e.preventDefault();
          const queue = queuedMessagesRef.current;
          editQueuedMessage(queue[queue.length - 1].id);
          return;
        }
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          handleSubmit();
        }
      },
      [handleSubmit, hasPendingInterrupt, input, editQueuedMessage]
    );

    const handleComposerResizeStart = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return;
        const startHeight =
          textareaRef.current?.getBoundingClientRect().height ?? composerHeight;
        composerResizeRef.current = {
          pointerId: event.pointerId,
          startY: event.clientY,
          startHeight,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
      },
      [composerHeight]
    );

    const handleComposerResizeMove = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        const resize = composerResizeRef.current;
        if (!resize || resize.pointerId !== event.pointerId) return;
        const maxHeight = Math.max(64, window.innerHeight * 0.45);
        const nextHeight = resize.startHeight + resize.startY - event.clientY;
        setComposerHeight(Math.min(maxHeight, Math.max(64, nextHeight)));
      },
      []
    );

    const handleComposerResizeEnd = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (composerResizeRef.current?.pointerId !== event.pointerId) return;
        composerResizeRef.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
      },
      []
    );

    const handleSuggestedPrompt = useCallback((prompt: string) => {
      setInput(prompt);
      requestAnimationFrame(() => textareaRef.current?.focus());
    }, []);

    // Auto-approve: when enabled, approve any pending tool-execution interrupt
    // for the rest of this conversation (each interrupt is handled once).
    useEffect(() => {
      if (!autoApprove) return;
      const ir = interrupt;
      const actionRequests =
        ir?.value && ((ir.value as any)["action_requests"] as unknown[]);
      if (
        !ir ||
        !Array.isArray(actionRequests) ||
        actionRequests.length === 0
      ) {
        autoApprovedRef.current = null;
        return;
      }
      if (autoApprovedRef.current === ir) return;
      autoApprovedRef.current = ir;
      resumeInterrupt({
        decisions: actionRequests.map(() => ({ type: "approve" })),
      });
    }, [autoApprove, interrupt, resumeInterrupt]);

    // ask_user: the agent is asking the user structured questions.
    const askUserQuestions = useMemo<AskUserQuestion[] | null>(() => {
      const value = interrupt?.value as
        | { type?: string; questions?: AskUserQuestion[] }
        | undefined;
      if (value?.type === "ask_user" && Array.isArray(value.questions)) {
        return value.questions;
      }
      return null;
    }, [interrupt]);

    const handleAskUserSubmit = useCallback(
      (answers: string[]) => {
        resumeInterrupt({ status: "answered", answers });
      },
      [resumeInterrupt]
    );

    const handleAskUserCancel = useCallback(() => {
      resumeInterrupt({ status: "cancelled" });
    }, [resumeInterrupt]);

    // Ordered list of pending tool-approval requests from the interrupt. We hand
    // ChatMessage the ORDER (not a name-keyed map) so two calls to the same tool
    // in one turn (e.g. two `execute`) each bind to their OWN request/args instead
    // of both collapsing onto the last one. `Array.isArray` guards a malformed
    // payload — a non-array `action_requests` here would otherwise throw and blank
    // the whole page.
    const actionRequests: ActionRequest[] = useMemo(() => {
      const raw =
        interrupt?.value && (interrupt.value as any)["action_requests"];
      return Array.isArray(raw) ? (raw as ActionRequest[]) : [];
    }, [interrupt]);

    // TODO: can we make this part of the hook?
    const processedMessages = useMemo(() => {
      /*
     1. Loop through all messages
     2. For each AI message, add the AI message, and any tool calls to the messageMap
     3. For each tool message, find the corresponding tool call in the messageMap and update the status and output
    */
      const messageMap = new Map<
        string,
        { message: Message; toolCalls: ToolCall[] }
      >();
      // Sub-agent (subgraph) messages stream in alongside the main conversation
      // when streamSubgraphs is on. They carry a NESTED langgraph_checkpoint_ns
      // ("tools:<id>|…") while the main agent's own messages are single-segment.
      // Keep them OUT of the main flow — they render under each sub-agent block's
      // "Steps" instead. (streamMetadata is live-only; once complete these messages
      // aren't in thread state anyway.)
      const seenAsyncUpdates = new Set<string>();
      // A malformed legacy regeneration could inherit the answer from its
      // sibling branch and then append another AI answer. For every fork, keep
      // only the branch represented by the latest message in the active view.
      const activeBranchByFork = new Map<string, string>();
      const staleSiblingIndexes = new Set<number>();
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const metadata = stream.getMessagesMetadata(messages[index], index);
        const options = metadata?.branchOptions;
        if (!metadata?.branch || !options || options.length < 2) continue;
        const forkKey = options.join("\u0000");
        if (!activeBranchByFork.has(forkKey)) {
          activeBranchByFork.set(forkKey, metadata.branch);
        }
        // Compatibility for branches created by the old regeneration bug: its
        // new child inherited the previous AI answer, so the SDK sees that old
        // message as unbranched. A real replacement branch starts at this
        // marker; non-human messages earlier in the same turn are stale copies.
        // Legacy regeneration branches placed their marker on an assistant
        // message after inheriting a stale sibling answer. A user-edit branch
        // legitimately places its marker on the NEW human message; applying
        // the compatibility cleanup there would incorrectly remove the prior
        // turn's assistant response.
        if (messages[index].type !== "human") {
          let turnHumanIndex = -1;
          for (let prior = index - 1; prior >= 0; prior -= 1) {
            if (messages[prior].type === "human") {
              turnHumanIndex = prior;
              break;
            }
          }
          for (let stale = turnHumanIndex + 1; stale < index; stale += 1) {
            if (messages[stale].type !== "human") {
              staleSiblingIndexes.add(stale);
            }
          }
        }
      }
      const visibleMessages = messages.filter((message: Message, index) => {
        if (staleSiblingIndexes.has(index)) return false;
        const messageMetadata = stream.getMessagesMetadata(message, index);
        const branchOptions = messageMetadata?.branchOptions;
        if (
          messageMetadata?.branch &&
          branchOptions &&
          branchOptions.length > 1 &&
          activeBranchByFork.get(branchOptions.join("\u0000")) !==
            messageMetadata.branch
        ) {
          return false;
        }
        // Humans are always user-typed (or our injected async-update pills) —
        // never sub-agent noise. Run their checks first so a stale subgraph
        // namespace on a previous-run human can't silently drop the prompt.
        // They CAN now be sibling branch roots after inline editing, so the
        // active-branch check above must run before this early return.
        if (message.type === "human") {
          const key = asyncUpdateMessageKey(
            extractStringFromMessageContent(message)
          );
          if (!key) return true;
          if (seenAsyncUpdates.has(key)) return false;
          seenAsyncUpdates.add(key);
          return true;
        }
        const meta = messageMetadata?.streamMetadata;
        const ns = meta?.["langgraph_checkpoint_ns"];
        if (typeof ns === "string" && ns.includes("|")) return false;
        // The conversation-compaction summary is generated by a SEPARATE LLM
        // call (its own "Context Extraction Assistant" system prompt, like the
        // tool-selector). Its output transiently leaks into the raw stream as an
        // AI message (## SESSION INTENT / ## SUMMARY / …) then vanishes — it is
        // never persisted in `messages`. Drop it here; the stable summary is
        // surfaced from `_summarization_event` as a collapsible block instead.
        if (isSummarizationMessage(message)) return false;
        return true;
      });
      const completedToolCallIds = new Set<string>();
      for (const message of visibleMessages) {
        if (message.type !== "tool") continue;
        const toolCallId = message.tool_call_id;
        if (toolCallId) completedToolCallIds.add(toolCallId);
      }
      const pendingActionCounts = new Map<string, number>();
      for (const ar of actionRequests) {
        pendingActionCounts.set(
          ar.name,
          (pendingActionCounts.get(ar.name) ?? 0) + 1
        );
      }
      visibleMessages.forEach((message: Message) => {
        if (message.type === "ai") {
          const toolCallsWithStatus = getMessageToolCalls(message)
            // The auxiliary tool-selector's internal `ToolSelectionResponse` call
            // has no result and isn't HITL-gated. Surface it only as a transient
            // spinner WHILE the run is actively selecting; hide it once the run
            // pauses on an interrupt or settles. Otherwise the execute approval's
            // "interrupted" icon leaks onto it (it never gets a result to clear)
            // and it lingers instead of disappearing.
            .filter(
              (toolCall) =>
                toolCall.name !== "ToolSelectionResponse" ||
                (isLoading && !interrupt)
            )
            .map((toolCall, toolCallIndex) => {
              const name = toolCall.name || "unknown";
              const id =
                toolCall.id ||
                `${message.id ?? "ai-message"}-tool-${toolCallIndex}-${name}`;
              const pendingCount = pendingActionCounts.get(name) ?? 0;
              const hasPendingAction =
                pendingCount > 0 && !completedToolCallIds.has(id);
              if (hasPendingAction) {
                pendingActionCounts.set(name, pendingCount - 1);
              }
              return {
                id,
                name,
                args: toolCall.args,
                // The selector call only survives the filter above while the run is
                // actively selecting (!interrupt), so this resolves to a spinner for
                // it without a special case.
                status: hasPendingAction ? "interrupted" : ("pending" as const),
              } as ToolCall;
            });
          messageMap.set(message.id!, {
            message,
            toolCalls: toolCallsWithStatus,
          });
        } else if (message.type === "tool") {
          const toolCallId = message.tool_call_id;
          if (!toolCallId) {
            return;
          }
          for (const [, data] of messageMap.entries()) {
            const toolCallIndex = data.toolCalls.findIndex(
              (tc: ToolCall) => tc.id === toolCallId
            );
            if (toolCallIndex === -1) {
              continue;
            }
            data.toolCalls[toolCallIndex] = {
              ...data.toolCalls[toolCallIndex],
              status: "completed" as const,
              result: extractStringFromMessageContent(message),
            };
            break;
          }
        } else if (message.type === "human") {
          messageMap.set(message.id!, {
            message,
            toolCalls: [],
          });
        }
      });
      const processedArray = Array.from(messageMap.values());
      return processedArray.map((data, index) => {
        const prevMessage =
          index > 0 ? processedArray[index - 1].message : null;
        return {
          ...data,
          showAvatar: data.message.type !== prevMessage?.type,
        };
      });
    }, [messages, actionRequests, interrupt, isLoading, stream]);

    const getResponseVersions = useCallback(
      (message: Message) => {
        const messageIndex = messages.findIndex(
          (candidate) => candidate.id === message.id
        );
        if (messageIndex < 0) return;
        let turnStart = 0;
        for (let index = messageIndex - 1; index >= 0; index -= 1) {
          if (messages[index].type === "human") {
            turnStart = index + 1;
            break;
          }
        }
        let turnEnd = messages.length;
        for (
          let index = messageIndex + 1;
          index < messages.length;
          index += 1
        ) {
          if (messages[index].type === "human") {
            turnEnd = index;
            break;
          }
        }
        let lastTextAssistantIndex = -1;
        for (let index = turnStart; index < turnEnd; index += 1) {
          if (
            messages[index].type === "ai" &&
            extractStringFromMessageContent(messages[index]).trim()
          ) {
            lastTextAssistantIndex = index;
          }
        }
        if (messageIndex !== lastTextAssistantIndex) return;

        let metadata = stream.getMessagesMetadata(message, messageIndex);
        if (!metadata?.branchOptions || metadata.branchOptions.length < 2) {
          for (let index = turnStart; index < turnEnd; index += 1) {
            const candidate = stream.getMessagesMetadata(
              messages[index],
              index
            );
            if (
              candidate?.branchOptions &&
              candidate.branchOptions.length > 1
            ) {
              metadata = candidate;
              break;
            }
          }
        }
        const options = metadata?.branchOptions;
        if (!metadata?.branch || !options || options.length < 2) return;
        const currentIndex = options.indexOf(metadata.branch);
        if (currentIndex < 0) return;
        return {
          current: currentIndex + 1,
          total: options.length,
          onPrevious:
            !isLoading && currentIndex > 0
              ? () => selectBranch(options[currentIndex - 1])
              : undefined,
          onNext:
            !isLoading && currentIndex < options.length - 1
              ? () => selectBranch(options[currentIndex + 1])
              : undefined,
        };
      },
      [isLoading, messages, selectBranch, stream]
    );

    const getMessageVersions = useCallback(
      (message: Message) => {
        if (message.type !== "human") return;
        const messageIndex = messages.findIndex(
          (candidate) => candidate.id === message.id
        );
        if (messageIndex < 0) return;
        const metadata = stream.getMessagesMetadata(message, messageIndex);
        const options = metadata?.branchOptions;
        if (!metadata?.branch || !options || options.length < 2) return;
        const currentIndex = options.indexOf(metadata.branch);
        if (currentIndex < 0) return;
        return {
          current: currentIndex + 1,
          total: options.length,
          onPrevious:
            !isLoading && currentIndex > 0
              ? () => selectBranch(options[currentIndex - 1])
              : undefined,
          onNext:
            !isLoading && currentIndex < options.length - 1
              ? () => selectBranch(options[currentIndex + 1])
              : undefined,
        };
      },
      [isLoading, messages, selectBranch, stream]
    );

    // UI preference: auto-collapse completed agent-action groups. The user can
    // turn this off in ConfigDialog; default is on.
    const { value: collapseAgentActions } = useCollapseAgentActions();

    // Detect whether an AI message has any actual rendered text content (as
    // opposed to being pure tool-call carriage). Tool-only AI messages are what
    // the ActionGroup wraps; AI messages with text (the assistant's "answer")
    // stay outside the fold so the user always sees it without expanding.
    const aiHasTextContent = (message: Message): boolean => {
      const content = (message as { content?: unknown }).content;
      if (typeof content === "string") return content.trim().length > 0;
      if (Array.isArray(content)) {
        for (const part of content) {
          const t = (part as { text?: unknown })?.text;
          if (typeof t === "string" && t.trim().length > 0) return true;
        }
      }
      return false;
    };

    // Group consecutive tool-only AI entries into a single foldable action
    // block. Anything else (human messages, AI messages with text) is rendered
    // as before. Reuses the same ProcessedMessage shape — the ActionGroup is
    // pure presentation, no data transformation beyond grouping.
    type RenderedItem =
      | { kind: "message"; data: (typeof processedMessages)[number] }
      | { kind: "action-group"; items: GroupedActionItem[] };
    const renderedItems = useMemo<RenderedItem[]>(() => {
      const out: RenderedItem[] = [];
      for (const entry of processedMessages) {
        const isToolOnly =
          entry.message.type === "ai" &&
          entry.toolCalls.length > 0 &&
          !aiHasTextContent(entry.message);
        if (isToolOnly) {
          const last = out[out.length - 1];
          if (last?.kind === "action-group") {
            last.items.push(entry);
          } else {
            out.push({ kind: "action-group", items: [entry] });
          }
        } else {
          out.push({ kind: "message", data: entry });
        }
      }
      return out;
    }, [processedMessages]);

    const lastMessageId =
      processedMessages.length > 0
        ? processedMessages[processedMessages.length - 1].message.id
        : undefined;

    // Where to anchor the "Conversation compacted" block. The event's
    // cutoffIndex points into the raw `messages` array (messages[0:cutoff] were
    // summarized); we render the block right before the first message AFTER the
    // cutoff so it reads as "everything above was folded into this summary". If
    // that boundary message isn't in the rendered list (e.g. cutoff past the
    // end), fall back to appending the block after the transcript.
    const compactionAnchorId = useMemo(() => {
      if (!summarizationEvent) return null;
      const processedIds = new Set(processedMessages.map((d) => d.message.id));
      // Anchor before the first STILL-VISIBLE message at or after the cutoff, so
      // a filtered boundary message (a tool result, or the transient summary
      // leak itself) doesn't bump the block to the very end of the transcript.
      for (let i = summarizationEvent.cutoffIndex; i < messages.length; i++) {
        const id = messages[i]?.id;
        if (id != null && processedIds.has(id)) return id;
      }
      return null;
    }, [summarizationEvent, messages, processedMessages]);

    const groupedTodos = {
      in_progress: todos.filter((t) => t.status === "in_progress"),
      pending: todos.filter((t) => t.status === "pending"),
      completed: todos.filter((t) => t.status === "completed"),
    };

    const hasTasks = todos.length > 0;
    const hasFiles = Object.keys(files).length > 0;

    const [submittedActionRequestKeys, setSubmittedActionRequestKeys] =
      useState<Set<string>>(() => new Set());
    useEffect(() => {
      if (actionRequests.length === 0) {
        setSubmittedActionRequestKeys(new Set());
      }
    }, [actionRequests.length]);
    const markActionRequestSubmitted = useCallback((key: string) => {
      setSubmittedActionRequestKeys((current) => {
        const next = new Set(current);
        next.add(key);
        return next;
      });
    }, []);

    const reviewConfigsMap: Map<string, ReviewConfig> | null = useMemo(() => {
      const reviewConfigs =
        interrupt?.value && (interrupt.value as any)["review_configs"];
      if (!Array.isArray(reviewConfigs)) return new Map<string, ReviewConfig>();
      const entries: Array<readonly [string, ReviewConfig]> = [];
      for (const rc of reviewConfigs as ReviewConfig[]) {
        const actionName = rc.actionName ?? rc.action_name;
        if (!actionName) continue;
        entries.push([
          actionName,
          {
            actionName,
            allowedDecisions: rc.allowedDecisions ?? rc.allowed_decisions,
          },
        ]);
      }
      return new Map<string, ReviewConfig>(entries);
    }, [interrupt]);

    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <Dialog
          open={autoApproveDialogOpen}
          onOpenChange={setAutoApproveDialogOpen}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>启用自动批准？</DialogTitle>
              <DialogDescription>
                金乌将在本次研究中自动执行工具操作，不再逐项请求确认。仅在你信任当前任务和部署环境时启用。
              </DialogDescription>
            </DialogHeader>
            <div className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
              <TriangleAlert
                className="mt-0.5 size-4 shrink-0"
                aria-hidden="true"
              />
              <p>
                自动批准仅对当前研究会话生效，并会在页面切换和重新加载后保留；
                其他研究会话拥有独立设置。你可以随时在此关闭。
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setAutoApproveDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                onClick={enableAutoApprove}
                className="bg-amber-600 text-white hover:bg-amber-700"
              >
                启用自动批准
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={regenerateMessageId !== null}
          onOpenChange={(open) => {
            if (!open) setRegenerateMessageId(null);
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>确认重新生成？</DialogTitle>
              <DialogDescription>
                重新生成会用新回答替换当前显示，并从这条回复之前重新开始思考。原回答会保留为历史版本，可通过回答下方的版本按钮查看。
              </DialogDescription>
            </DialogHeader>
            <div className="flex items-start gap-3 rounded-lg border border-red-900/70 bg-red-950/40 p-3 text-sm text-red-100">
              <TriangleAlert
                className="mt-0.5 size-4 shrink-0 text-red-400"
                aria-hidden="true"
              />
              <p>
                工作区中的生成文件会被永久删除且无法恢复。已上传的输入文件会保留，供本次重新生成继续使用。
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setRegenerateMessageId(null)}
              >
                取消
              </Button>
              <Button
                variant="destructive"
                onClick={confirmRegenerate}
              >
                删除并重新生成
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <WorkspaceFileDialog
          path={workspaceFilePath}
          onClose={() => setWorkspaceFilePath(null)}
        />
        <MemoryFileDialog
          path={memoryFilePath}
          onClose={() => setMemoryFilePath(null)}
        />
        <Dialog
          open={modelPickerOpen}
          onOpenChange={setModelPickerOpen}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>选择当前会话使用的模型</DialogTitle>
              <DialogDescription>
                该选择仅对当前研究会话生效。如需使用列表外的模型名称，可在输入框中输入{" "}
                <span className="font-mono text-xs">/model &lt;name&gt;</span>{" "}
                。
              </DialogDescription>
            </DialogHeader>
            <div className="mt-2 space-y-2">
              <div className="text-xs text-muted-foreground">
                当前模型：{" "}
                <span className="font-mono">
                  {currentModel
                    ? `${currentModel.name}${
                        currentModel.provider
                          ? ` (${currentModel.provider})`
                          : ""
                      }`
                    : "部署默认值"}
                </span>
                {modelRegistryLoading && (
                  <span className="ml-2 italic">正在加载模型列表…</span>
                )}
              </div>
              {isFallbackModelList && (
                <div className="border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 flex items-start gap-2 rounded-md border px-3 py-2 text-xs text-foreground">
                  <span
                    aria-hidden="true"
                    className="text-base leading-none text-[var(--color-warning)]"
                  >
                    {"\u26A0"}
                  </span>
                  <span>
                    当前显示精选模型列表——部署的
                    <span className="mx-1 font-mono">/api/models</span>
                    模型注册表加载失败
                    {modelRegistryError ? ` (${modelRegistryError})` : ""}.
                    仍可通过{" "}
                    <span className="font-mono">/model &lt;name&gt;</span>
                    使用其他短名称。
                  </span>
                </div>
              )}
              <input
                type="text"
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                placeholder="按名称或提供方筛选…"
                autoFocus
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-sm placeholder:font-sans placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <ul className="max-h-72 space-y-1 overflow-y-auto pl-0">
                {filteredPickerModels.length === 0 && (
                  <li className="px-3 py-2 text-xs text-muted-foreground">
                    没有匹配“{modelSearch}”的模型。
                  </li>
                )}
                {filteredPickerModels.map((m) => {
                  const isActive =
                    modelOverride?.model === m.model &&
                    modelOverride?.model_provider === m.model_provider;
                  const isDefault =
                    !modelOverride &&
                    modelRegistry.defaultEntry?.name === m.model &&
                    (modelRegistry.defaultEntry?.provider ?? null) ===
                      (m.model_provider ?? null);
                  return (
                    <li key={`${m.model}|${m.model_provider ?? ""}`}>
                      <button
                        type="button"
                        aria-label={`选择模型 ${m.model}${
                          m.model_provider ? `，提供方 ${m.model_provider}` : ""
                        }${isDefault ? "（默认）" : ""}`}
                        onClick={async () => {
                          try {
                            await setModelOverride({
                              model: m.model,
                              ...(m.model_provider
                                ? { model_provider: m.model_provider }
                                : {}),
                            });
                            toast.success(
                              `模型已设置为 ${m.model}${
                                m.model_provider ? ` (${m.model_provider})` : ""
                              }。`
                            );
                            setModelPickerOpen(false);
                          } catch (err) {
                            toast.error(
                              err instanceof Error
                                ? `更新模型失败：${err.message}`
                                : "更新模型失败，请重试。"
                            );
                          }
                        }}
                        className={cn(
                          "flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                          isActive && "bg-accent"
                        )}
                      >
                        <span className="min-w-0 truncate font-mono">
                          {m.model}
                          {isDefault && (
                            <span className="ml-2 text-xs font-normal text-muted-foreground">
                              · 默认
                            </span>
                          )}
                        </span>
                        {m.model_provider && (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {m.model_provider}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={async () => {
                  try {
                    await setModelOverride(null);
                    toast.success("已恢复使用默认模型。");
                    setModelPickerOpen(false);
                  } catch (err) {
                    toast.error(
                      err instanceof Error
                        ? `恢复默认模型失败：${err.message}`
                        : "恢复默认模型失败，请重试。"
                    );
                  }
                }}
                disabled={!modelOverride}
              >
                恢复默认
              </Button>
              <Button onClick={() => setModelPickerOpen(false)}>关闭</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <div
          className="flex-1 overflow-y-auto overflow-x-hidden overscroll-contain"
          ref={scrollRef}
        >
          <div
            className="mx-auto w-full max-w-[960px] px-4 pb-4 pt-3 sm:px-5"
            ref={contentRef}
          >
            {isThreadLoading ? (
              <div className="flex items-center justify-center p-8">
                <p className="text-muted-foreground">加载中…</p>
              </div>
            ) : (
              <>
                {processedMessages.length === 0 && !isLoading && (
                  <div className="flex min-h-[42vh] flex-col items-center justify-center px-3 pt-12 text-center sm:pt-16">
                    <h2 className="text-pretty text-lg font-semibold sm:text-xl">
                      开启你的科研探索
                    </h2>
                    <p className="mt-2 max-w-lg text-sm text-muted-foreground">
                      金乌是你的 AI 科研伙伴 —— 阅读文献、运行实验、沉淀知识。
                    </p>
                    <div className="mt-4 flex max-w-2xl flex-wrap justify-center gap-2">
                      {SUGGESTED_PROMPTS.map((prompt) => (
                        <button
                          key={prompt}
                          type="button"
                          onClick={() => handleSuggestedPrompt(prompt)}
                          className="max-w-full rounded-full border border-border bg-card px-2.5 py-1.5 text-xs text-foreground shadow-sm transition-colors hover:border-[var(--color-border)] hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring sm:text-sm"
                        >
                          {prompt}
                        </button>
                      ))}
                    </div>
                    {onNavigate && onOpenThread && (
                      <ResearchDashboard
                        onNavigate={onNavigate}
                        onOpenThread={onOpenThread}
                      />
                    )}
                  </div>
                )}
                {threadId && (
                  <ResearchReviewPanel
                    threadId={threadId}
                    isLoading={isLoading}
                  />
                )}
                {renderedItems.map((item, index) => {
                  if (item.kind === "action-group") {
                    // A group is "streaming" when its last item is the last
                    // overall AND the run is in flight — drives the spinner +
                    // auto-collapse-on-settle behavior inside ActionGroup.
                    const groupLastId =
                      item.items[item.items.length - 1].message.id;
                    const isLastGroup = index === renderedItems.length - 1;
                    const groupIsStreaming =
                      isLoading && isLastGroup && groupLastId === lastMessageId;
                    const groupMessageIds = item.items
                      .map((entry) => entry.message.id)
                      .filter((id): id is string => Boolean(id));
                    const groupFocused = groupMessageIds.some(
                      (id) => id === focusedResearchMessage
                    );
                    return (
                      <div
                        key={`action-group-${item.items[0].message.id}`}
                        data-chat-message-id={groupMessageIds[0]}
                        className={cn(
                          "relative rounded-lg transition-[background-color,box-shadow] duration-300",
                          groupFocused &&
                            "bg-[var(--brand)]/10 ring-[var(--brand)]/60 ring-1"
                        )}
                      >
                        {groupMessageIds.slice(1).map((id) => (
                          <span
                            key={id}
                            data-chat-message-id={id}
                            className="sr-only"
                          />
                        ))}
                        <ActionGroup
                          items={item.items}
                          isStreaming={groupIsStreaming}
                          defaultCollapsed={collapseAgentActions}
                          isAtBottom={isAtBottom}
                          lastMessageId={lastMessageId}
                          isLoading={isLoading}
                          actionRequests={actionRequests}
                          submittedActionRequestKeys={
                            submittedActionRequestKeys
                          }
                          onActionRequestSubmitted={markActionRequestSubmitted}
                          reviewConfigsMap={reviewConfigsMap}
                          stream={stream}
                          onResumeInterrupt={resumeInterrupt}
                          graphId={assistant?.graph_id}
                          autoApprove={autoApprove}
                          subAgentSteps={subAgentSteps}
                          ui={ui}
                          compactionAnchorId={compactionAnchorId}
                          summarizationEvent={summarizationEvent ?? null}
                        />
                      </div>
                    );
                  }
                  const data = item.data;
                  const messageUi = ui?.filter(
                    (u: any) => u.metadata?.message_id === data.message.id
                  );
                  const isLastMessage = index === renderedItems.length - 1;
                  const isAssistant = data.message.type !== "human";
                  const showCompactionBefore =
                    compactionAnchorId === data.message.id;
                  return (
                    <div
                      key={data.message.id}
                      data-chat-message-id={data.message.id}
                      className={cn(
                        "rounded-lg transition-[background-color,box-shadow] duration-300",
                        focusedResearchMessage === data.message.id &&
                          "bg-[var(--brand)]/10 ring-[var(--brand)]/60 ring-1"
                      )}
                    >
                      {showCompactionBefore && summarizationEvent && (
                        <CompactionSummary
                          content={summarizationEvent.content}
                          summarizedCount={summarizationEvent.cutoffIndex}
                        />
                      )}
                      <ChatMessage
                        message={data.message}
                        toolCalls={data.toolCalls}
                        isLoading={isLoading}
                        isStreaming={isLoading && isLastMessage && isAssistant}
                        actionRequests={
                          isLastMessage ? actionRequests : undefined
                        }
                        submittedActionRequestKeys={submittedActionRequestKeys}
                        onActionRequestSubmitted={markActionRequestSubmitted}
                        reviewConfigsMap={
                          isLastMessage ? reviewConfigsMap : undefined
                        }
                        ui={messageUi}
                        stream={stream}
                        onResumeInterrupt={resumeInterrupt}
                        graphId={assistant?.graph_id}
                        onEditMessage={
                          data.message.type === "human" && data.message.id
                            ? (content) =>
                                editMessage(data.message.id!, content)
                            : undefined
                        }
                        canEditMessage={
                          !isLoading &&
                          !hasPendingInterrupt &&
                          data.message.type === "human" &&
                          Boolean(threadId && data.message.id)
                        }
                        onRegenerate={() =>
                          setRegenerateMessageId(data.message.id!)
                        }
                        canRegenerate={
                          !isLoading &&
                          !hasPendingInterrupt &&
                          Boolean(threadId && data.message.id)
                        }
                        responseVersions={
                          isAssistant
                            ? getResponseVersions(data.message)
                            : undefined
                        }
                        messageVersions={
                          data.message.type === "human"
                            ? getMessageVersions(data.message)
                            : undefined
                        }
                        autoApprove={autoApprove}
                        subAgentSteps={subAgentSteps}
                      />
                    </div>
                  );
                })}
                {summarizationEvent && !compactionAnchorId && (
                  <CompactionSummary
                    content={summarizationEvent.content}
                    summarizedCount={summarizationEvent.cutoffIndex}
                  />
                )}
                {askUserQuestions && (
                  <div className="mt-4">
                    <AskUserInterrupt
                      questions={askUserQuestions}
                      onSubmit={handleAskUserSubmit}
                      onCancel={handleAskUserCancel}
                      isLoading={isLoading}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="flex-shrink-0 bg-transparent">
          {queuedMessages.length > 0 && (
            <div
              aria-label="排队消息"
              className="relative mx-auto -mb-4 w-[calc(100%-16px)] max-w-[960px] px-2 sm:w-[calc(100%-24px)]"
            >
              <div className="rounded-b-lg rounded-t-2xl border border-border bg-background pb-5 pt-1 shadow-sm">
                {queuedMessages.map((q, index) => {
                  const editBlocked =
                    input.trim().length > 0 || pendingFiles.length > 0;
                  return (
                    <div
                      key={q.id}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={() => dropQueuedMessage(q.id)}
                      className="relative flex min-h-11 items-start gap-1.5 px-2 py-2 text-sm sm:gap-2 sm:px-3"
                    >
                      <button
                        type="button"
                        draggable
                        onDragStart={(event) => {
                          draggedQueuedMessageIdRef.current = q.id;
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData(
                            "text/plain",
                            String(q.id)
                          );
                        }}
                        onDragEnd={() => {
                          draggedQueuedMessageIdRef.current = null;
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "ArrowUp" && index > 0) {
                            event.preventDefault();
                            moveQueuedMessage(q.id, -1);
                          }
                          if (
                            event.key === "ArrowDown" &&
                            index < queuedMessages.length - 1
                          ) {
                            event.preventDefault();
                            moveQueuedMessage(q.id, 1);
                          }
                        }}
                        aria-label={`调整第 ${index + 1} 条排队消息的顺序，共 ${
                          queuedMessages.length
                        } 条`}
                        title="拖动调整顺序，或使用方向键"
                        className="mt-0.5 inline-flex size-5 shrink-0 cursor-grab items-center justify-center rounded text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
                      >
                        <GripVertical
                          className="size-3.5"
                          aria-hidden="true"
                        />
                      </button>
                      <CornerDownRight
                        className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="line-clamp-2 whitespace-pre-wrap break-words pt-0.5 text-foreground">
                          {q.text}
                        </span>
                        {q.files.length > 0 && (
                          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                            <Paperclip
                              className="size-3 shrink-0"
                              aria-hidden="true"
                            />
                            {q.files.length} 个文件
                          </span>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-0.5">
                        <button
                          type="button"
                          onClick={() => steerQueuedMessage(q.id)}
                          aria-label="将此消息设为下一条优先发送"
                          title="不中断当前轮次，并在下一轮优先发送"
                          className="inline-flex h-7 items-center gap-1 rounded-full px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <CornerDownRight
                            className="size-3.5"
                            aria-hidden="true"
                          />
                          <span className="hidden sm:inline">优先发送</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => removeQueuedMessage(q.id)}
                          aria-label="移除排队消息"
                          title="从队列中移除"
                          className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <Trash2
                            className="size-3.5"
                            aria-hidden="true"
                          />
                        </button>
                        <details
                          className="group/menu relative"
                          onBlur={(event) => {
                            if (
                              !event.currentTarget.contains(event.relatedTarget)
                            ) {
                              event.currentTarget.removeAttribute("open");
                            }
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") {
                              event.currentTarget.removeAttribute("open");
                              event.currentTarget
                                .querySelector("summary")
                                ?.focus();
                            }
                          }}
                        >
                          <summary
                            aria-label="更多排队消息操作"
                            title="更多操作"
                            className="inline-flex size-7 cursor-pointer list-none items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden"
                          >
                            <Ellipsis
                              className="size-4"
                              aria-hidden="true"
                            />
                          </summary>
                          <div className="absolute right-0 top-8 z-30 min-w-40 rounded-xl border border-border bg-background p-1.5 shadow-lg">
                            <button
                              type="button"
                              onClick={() => editQueuedMessage(q.id)}
                              disabled={editBlocked}
                              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              <Pencil
                                className="size-4"
                                aria-hidden="true"
                              />
                              编辑消息
                            </button>
                            <button
                              type="button"
                              onClick={clearQueuedMessages}
                              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              <ListX
                                className="size-4"
                                aria-hidden="true"
                              />
                              清空队列
                            </button>
                          </div>
                        </details>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <div
            className={cn(
              "relative z-10 mx-auto mb-2 flex w-[calc(100%-16px)] max-w-[960px] flex-shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-background transition-colors duration-200 ease-in-out sm:mb-4 sm:w-[calc(100%-24px)]",
              "focus-within:ring-2 focus-within:ring-ring"
            )}
          >
            {
              <div className="flex max-h-60 flex-col overflow-y-auto border-b border-border bg-sidebar empty:hidden sm:max-h-72">
                {!metaOpen && (
                  <>
                    {(() => {
                      const activeTask = todos.find(
                        (t) => t.status === "in_progress"
                      );

                      const totalTasks = todos.length;
                      const remainingTasks =
                        totalTasks - groupedTodos.pending.length;
                      const isCompleted = totalTasks === remainingTasks;

                      const tasksTrigger = (() => {
                        if (!hasTasks) return null;
                        return (
                          <button
                            type="button"
                            onClick={() =>
                              setMetaOpen((prev) =>
                                prev === "tasks" ? null : "tasks"
                              )
                            }
                            className="grid w-full cursor-pointer grid-cols-[auto_auto_1fr] items-center gap-2.5 px-3 py-2.5 text-left sm:px-4"
                            aria-expanded={metaOpen === "tasks"}
                          >
                            {(() => {
                              if (isCompleted) {
                                return [
                                  <CheckCircle
                                    key="icon"
                                    size={16}
                                    className="text-[var(--color-success)]"
                                  />,
                                  <span
                                    key="label"
                                    className="ml-[1px] min-w-0 truncate text-sm"
                                  >
                                    所有任务已完成
                                  </span>,
                                ];
                              }

                              if (activeTask != null) {
                                return [
                                  <div key="icon">
                                    {getStatusIcon(activeTask.status)}
                                  </div>,
                                  <span
                                    key="label"
                                    className="ml-[1px] min-w-0 truncate text-sm"
                                  >
                                    任务{" "}
                                    {totalTasks - groupedTodos.pending.length} /{" "}
                                    {totalTasks}
                                  </span>,
                                  <span
                                    key="content"
                                    className="min-w-0 gap-2 truncate text-sm text-muted-foreground"
                                  >
                                    {activeTask.content}
                                  </span>,
                                ];
                              }

                              return [
                                <Circle
                                  key="icon"
                                  size={16}
                                  className="text-[var(--color-text-tertiary)]"
                                />,
                                <span
                                  key="label"
                                  className="ml-[1px] min-w-0 truncate text-sm"
                                >
                                  任务{" "}
                                  {totalTasks - groupedTodos.pending.length} /{" "}
                                  {totalTasks}
                                </span>,
                              ];
                            })()}
                          </button>
                        );
                      })();

                      const filesTrigger = (() => {
                        if (!hasFiles) return null;
                        return (
                          <button
                            type="button"
                            onClick={() =>
                              setMetaOpen((prev) =>
                                prev === "files" ? null : "files"
                              )
                            }
                            className="flex flex-shrink-0 cursor-pointer items-center gap-2 px-3 py-2.5 text-left text-sm sm:px-4"
                            aria-expanded={metaOpen === "files"}
                          >
                            <FileIcon size={16} />
                            文件（状态）
                            <span className="h-4 min-w-4 rounded-full bg-[var(--brand-solid)] px-0.5 text-center text-[10px] leading-[16px] text-[var(--brand-foreground)]">
                              {Object.keys(files).length}
                            </span>
                          </button>
                        );
                      })();

                      if (!hasTasks && !hasFiles) return null;

                      return (
                        <div className="flex items-center">
                          <div className="min-w-0 flex-1">{tasksTrigger}</div>
                          {filesTrigger}
                        </div>
                      );
                    })()}
                  </>
                )}

                {metaOpen && (
                  <>
                    <div className="sticky top-0 flex items-stretch bg-sidebar text-sm">
                      {hasTasks && (
                        <button
                          type="button"
                          className="py-2.5 pr-4 first:pl-3 aria-expanded:font-semibold sm:first:pl-4"
                          onClick={() =>
                            setMetaOpen((prev) =>
                              prev === "tasks" ? null : "tasks"
                            )
                          }
                          aria-expanded={metaOpen === "tasks"}
                        >
                          任务
                        </button>
                      )}
                      {hasFiles && (
                        <button
                          type="button"
                          className="inline-flex items-center gap-2 py-2.5 pr-4 first:pl-3 aria-expanded:font-semibold sm:first:pl-4"
                          onClick={() =>
                            setMetaOpen((prev) =>
                              prev === "files" ? null : "files"
                            )
                          }
                          aria-expanded={metaOpen === "files"}
                        >
                          文件（状态）
                          <span className="h-4 min-w-4 rounded-full bg-[var(--brand-solid)] px-0.5 text-center text-[10px] leading-[16px] text-[var(--brand-foreground)]">
                            {Object.keys(files).length}
                          </span>
                        </button>
                      )}
                      <button
                        aria-label="关闭"
                        className="flex-1"
                        onClick={() => setMetaOpen(null)}
                      />
                    </div>
                    <div
                      ref={tasksContainerRef}
                      className="px-3 sm:px-4"
                    >
                      {metaOpen === "tasks" &&
                        Object.entries(groupedTodos)
                          .filter(([_, todos]) => todos.length > 0)
                          .map(([status, todos]) => (
                            <div
                              key={status}
                              className="mb-4"
                            >
                              <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-tertiary">
                                {
                                  {
                                    pending: "待处理",
                                    in_progress: "进行中",
                                    completed: "已完成",
                                  }[status]
                                }
                              </h3>
                              <div className="grid grid-cols-[auto_1fr] gap-3 rounded-sm p-1 pl-0 text-sm">
                                {todos.map((todo, index) => (
                                  <Fragment
                                    key={`${status}_${todo.id}_${index}`}
                                  >
                                    {getStatusIcon(todo.status, "mt-0.5")}
                                    <span className="break-words text-inherit">
                                      {todo.content}
                                    </span>
                                  </Fragment>
                                ))}
                              </div>
                            </div>
                          ))}

                      {metaOpen === "files" && (
                        <div className="mb-6">
                          <FilesPopover
                            files={files}
                            setFiles={setFiles}
                            editDisabled={
                              isLoading === true || interrupt !== undefined
                            }
                          />
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            }
            {autoApprove && (
              <div
                aria-live="polite"
                className="flex items-center justify-between gap-3 border-b border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
              >
                <span className="flex items-center gap-1.5">
                  <TriangleAlert
                    className="size-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  工具操作将不经审批直接运行。
                </span>
                <button
                  type="button"
                  onClick={turnOffAutoApprove}
                  className="shrink-0 rounded px-2 py-1 font-semibold transition-colors hover:bg-amber-200 focus-visible:ring-2 focus-visible:ring-amber-700 dark:hover:bg-amber-900"
                >
                  关闭
                </button>
              </div>
            )}
            {runningAgents > 0 && (
              <div className="flex items-center gap-1.5 border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
                <button
                  type="button"
                  onClick={onShowAgents}
                  title={`${runningAgents} 个后台 Agent 正在运行，点击查看`}
                  aria-label={`查看 ${runningAgents} 个正在运行的后台 Agent`}
                  className="ml-auto flex shrink-0 items-center gap-1.5 rounded px-1.5 py-0.5 font-medium text-[var(--brand)] transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span
                    className="size-2 animate-pulse rounded-full bg-[var(--color-warning)]"
                    aria-hidden="true"
                  />
                  {runningAgents} 个 Agent
                </button>
              </div>
            )}
            <form
              onSubmit={handleSubmit}
              className="flex flex-col"
            >
              {pendingFiles.length > 0 && (
                <div
                  aria-label="已附加文件"
                  className="flex flex-wrap gap-2 border-b border-border px-3 py-2"
                >
                  {pendingFiles.map((file) => (
                    <span
                      key={file.path}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-muted px-2 py-1 text-xs text-foreground"
                    >
                      <FileIcon
                        className="size-3.5 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span
                        className="max-w-48 truncate"
                        title={file.path}
                      >
                        {file.name}
                      </span>
                      <button
                        type="button"
                        onClick={() => removePendingFile(file.path)}
                        aria-label={`从此消息中移除 ${file.name}`}
                        title={`从此消息中移除 ${file.name}`}
                        className="rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <X
                          className="size-3.5"
                          aria-hidden="true"
                        />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div
                role="separator"
                aria-orientation="horizontal"
                aria-label="调整输入框高度"
                title="上下拖动调整输入框高度"
                onPointerDown={handleComposerResizeStart}
                onPointerMove={handleComposerResizeMove}
                onPointerUp={handleComposerResizeEnd}
                onPointerCancel={handleComposerResizeEnd}
                className="group flex h-3 shrink-0 cursor-row-resize touch-none select-none items-center justify-center"
              >
                <span
                  aria-hidden="true"
                  className="h-0.5 w-12 rounded-full bg-border transition-colors group-focus-within:bg-[var(--brand)] group-hover:bg-[var(--brand)]"
                />
              </div>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                aria-label="消息"
                disabled={hasPendingInterrupt}
                placeholder={
                  hasPendingInterrupt
                    ? "请先响应上方请求以继续…"
                    : isLoading
                    ? "输入后续消息，将在当前轮次完成后发送…"
                    : "向金乌提问……"
                }
                className="font-inherit max-h-[45vh] min-h-16 w-full flex-none resize-none overflow-y-auto border-0 bg-transparent px-3.5 pb-2.5 pt-3 text-sm leading-6 text-primary outline-none placeholder:text-tertiary disabled:cursor-not-allowed sm:px-4"
                style={{ height: composerHeight }}
                rows={2}
              />
              <div className="flex items-center justify-between gap-2 p-2 sm:p-2.5">
                <div className="flex items-center gap-1">
                  <input
                    ref={uploadInputRef}
                    type="file"
                    multiple
                    onChange={handleFilesSelected}
                    disabled={
                      !assistant || hasPendingInterrupt || isUploadingFiles
                    }
                    className="hidden"
                  />
                  <button
                    type="button"
                    onClick={() => uploadInputRef.current?.click()}
                    disabled={
                      !assistant || hasPendingInterrupt || isUploadingFiles
                    }
                    aria-label="上传文件到工作区"
                    title="上传文件到工作区（单个文件最大 50 MB）"
                    className="inline-flex size-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Paperclip
                      className="size-4"
                      aria-hidden="true"
                    />
                  </button>
                  {currentModel && (
                    <button
                      type="button"
                      onClick={() => setModelPickerOpen(true)}
                      title="点击更改当前会话的模型"
                      aria-label="更改当前会话的模型"
                      className="inline-flex min-w-0 max-w-[min(14rem,38vw)] items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Sparkles
                        className="size-3.5 shrink-0 text-[var(--brand)]"
                        aria-hidden="true"
                      />
                      <span className="truncate font-medium text-foreground">
                        {currentModel.name}
                      </span>
                      {currentModel.provider && (
                        <span className="hidden shrink-0 sm:inline">
                          · {currentModel.provider}
                        </span>
                      )}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      autoApprove
                        ? turnOffAutoApprove()
                        : setAutoApproveDialogOpen(true)
                    }
                    aria-pressed={autoApprove}
                    title="自动批准当前会话中的所有工具操作"
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                      autoApprove
                        ? "bg-amber-600 text-white hover:bg-amber-700"
                        : "text-muted-foreground hover:bg-accent hover:text-foreground"
                    )}
                  >
                    <ShieldCheck
                      className="size-3.5"
                      aria-hidden="true"
                    />
                    <span className="hidden min-[360px]:inline">
                      {autoApprove ? "自动批准：开" : "自动批准"}
                    </span>
                  </button>
                  {onNavigate && workspaceDir && (
                    <button
                      type="button"
                      onClick={() => onNavigate({ view: "workspace" })}
                      title={`${
                        workspaceOpen ? "关闭" : "打开"
                      }工作区：${workspaceDir}`}
                      aria-label={`${
                        workspaceOpen ? "关闭" : "打开"
                      }工作区：${workspaceDir}`}
                      aria-pressed={Boolean(workspaceOpen)}
                      className="inline-flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <FolderOpen
                        className="size-3.5 flex-shrink-0"
                        aria-hidden="true"
                      />
                      <span className="hidden max-w-[140px] truncate font-mono sm:inline lg:max-w-[220px]">
                        {workspaceDir.split("/").filter(Boolean).pop() ||
                          workspaceDir}
                      </span>
                    </button>
                  )}
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    type={isLoading ? "button" : "submit"}
                    variant={isLoading ? "destructive" : "default"}
                    onClick={isLoading ? stopStream : handleSubmit}
                    disabled={
                      isStopping ||
                      (!isLoading &&
                        (submitDisabled || isUploadingFiles || !input.trim()))
                    }
                    aria-label={
                      isStopping
                        ? "正在停止生成"
                        : isLoading
                        ? "停止生成"
                        : "发送消息"
                    }
                  >
                    {isStopping ? (
                      <>
                        <Square size={14} />
                        <span className="hidden sm:inline">正在停止…</span>
                      </>
                    ) : isLoading ? (
                      <>
                        <Square size={14} />
                        <span className="hidden sm:inline">停止</span>
                      </>
                    ) : (
                      <>
                        <ArrowUp size={18} />
                        <span className="hidden sm:inline">发送</span>
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </form>
          </div>
        </div>
      </div>
    );
  }
);

ChatInterface.displayName = "ChatInterface";
