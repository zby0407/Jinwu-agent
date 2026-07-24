"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, ChevronUp, Loader2 } from "lucide-react";
import type { Message } from "@langchain/langgraph-sdk";
import type { ActionRequest, ReviewConfig, ToolCall } from "@/app/types/types";
import type { SubAgentStep } from "@/lib/subAgentActivity";
import { ChatMessage } from "./ChatMessage";
import { CompactionSummary } from "./CompactionSummary";
import { cn } from "@/lib/utils";

export interface GroupedActionItem {
  message: Message;
  toolCalls: ToolCall[];
  showAvatar?: boolean;
}

interface ActionGroupProps {
  items: GroupedActionItem[];
  /** True if the very last message in the whole transcript is in this group
   *  and the run is still active — i.e. the group is currently being extended. */
  isStreaming: boolean;
  /** From `useCollapseAgentActions` — user preference. */
  defaultCollapsed: boolean;
  /** From `useStickToBottom().isAtBottom` — auto-collapse only fires when
   *  the user is at the bottom (so a scrolled-up reader isn't jumped). */
  isAtBottom: boolean;
  /** Id of the LAST message in the entire processedMessages list. Used to
   *  decide which ChatMessage should receive actionRequests / reviewConfigsMap. */
  lastMessageId: string | undefined;
  // Pass-through ChatMessage props
  isLoading: boolean;
  actionRequests: ActionRequest[];
  submittedActionRequestKeys: Set<string>;
  onActionRequestSubmitted: (key: string) => void;
  reviewConfigsMap: Map<string, ReviewConfig> | null;
  stream: unknown;
  onResumeInterrupt: (value: unknown) => void;
  graphId?: string;
  onEditMessage: (content: string) => void;
  autoApprove: boolean;
  subAgentSteps: Record<string, SubAgentStep[]>;
  ui: any[] | undefined;
  // CompactionSummary anchoring: rendered before the matching item inside the group.
  compactionAnchorId: string | null;
  summarizationEvent: { content: string; cutoffIndex: number } | null;
}

// Last tool call name — what we surface in the header summary line.
function lastToolName(items: GroupedActionItem[]): string {
  for (let i = items.length - 1; i >= 0; i--) {
    const tcs = items[i].toolCalls;
    if (tcs.length > 0) return tcs[tcs.length - 1].name || "tool";
  }
  return "action";
}

export const ActionGroup = React.memo<ActionGroupProps>(function ActionGroup({
  items,
  isStreaming,
  defaultCollapsed,
  isAtBottom,
  lastMessageId,
  isLoading,
  actionRequests,
  submittedActionRequestKeys,
  onActionRequestSubmitted,
  reviewConfigsMap,
  stream,
  onResumeInterrupt,
  graphId,
  onEditMessage,
  autoApprove,
  subAgentSteps,
  ui,
  compactionAnchorId,
  summarizationEvent,
}) {
  // Whether this group contains the message that an interrupt is currently
  // asking the user to approve. Tool-approval interrupts attach to the latest
  // assistant message — so if `lastMessageId` belongs to this group AND there
  // are pending action requests, the user needs to see them.
  //
  // Skip entirely when auto-approve is on: in that mode each interrupt is
  // observed for one render tick before the auto-approval effect fires, so
  // `actionRequests` is briefly non-empty per tool call. A force-open per flash
  // would yank the section open dozens of times in a single turn — defeating
  // the whole "don't bother me" intent of auto-approve.
  const hasPendingApproval = useMemo(() => {
    if (autoApprove) return false;
    if (actionRequests.length === 0) return false;
    if (lastMessageId === undefined) return false;
    return items.some((item) => item.message.id === lastMessageId);
  }, [autoApprove, actionRequests.length, lastMessageId, items]);

  const [open, setOpen] = useState<boolean>(() => !defaultCollapsed);
  const wasStreamingRef = useRef(isStreaming);
  // Once the user manually clicks the header (open or close), they own the
  // state — no more auto-collapse. Without this, expanding a group to
  // inspect earlier tool calls gets undone the moment the next assistant
  // message lands (that flips `groupIsStreaming` from true to false, which
  // is exactly the transition the auto-collapse effect watches for).
  const userTouchedRef = useRef(false);
  const handleToggle = () => {
    userTouchedRef.current = true;
    setOpen((v) => !v);
  };
  const handleCollapse = () => {
    userTouchedRef.current = true;
    setOpen(false);
  };

  // Auto-collapse when streaming ends, but only if the user is at the bottom
  // AND hasn't touched this group. Approvals never force-open; their controls
  // render in the preview below.
  useEffect(() => {
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = isStreaming;
    if (userTouchedRef.current) return;
    if (
      wasStreaming &&
      !isStreaming &&
      !hasPendingApproval &&
      defaultCollapsed &&
      isAtBottom
    ) {
      setOpen(false);
    }
  }, [isStreaming, hasPendingApproval, defaultCollapsed, isAtBottom]);

  // One AI message can carry several tool calls, so count the actual actions
  // rather than the number of message containers in this group.
  const count = items.reduce((total, item) => total + item.toolCalls.length, 0);
  const toolName = lastToolName(items);
  const headerText = isStreaming
    ? `${count} action${count === 1 ? "" : "s"} running — ${toolName}`
    : `${count} action${count === 1 ? "" : "s"} — last: ${toolName}`;

  return (
    <div className="my-2">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} ${headerText}`}
        title={headerText}
        onClick={handleToggle}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md border border-border bg-[var(--color-surface)] px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        )}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            open && "rotate-90"
          )}
        />
        {isStreaming && (
          <Loader2
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin text-[var(--brand)]"
          />
        )}
        <span className="truncate">{headerText}</span>
      </button>
      {/* Collapsed approval preview — renders the single approval-bearing
          message so the user can act without expanding the full timeline.
          When open, this is empty and the same item renders inside the body. */}
      {(() => {
        if (open || !hasPendingApproval || lastMessageId === undefined)
          return null;
        const previewItem = items.find((i) => i.message.id === lastMessageId);
        if (!previewItem) return null;
        const messageUi = ui?.filter(
          (u) => u.metadata?.message_id === previewItem.message.id
        );
        return (
          <div className="mt-2 space-y-2 border-l-2 border-border pl-3">
            <ChatMessage
              message={previewItem.message}
              toolCalls={previewItem.toolCalls}
              isLoading={isLoading}
              isStreaming={isStreaming}
              actionRequests={actionRequests}
              submittedActionRequestKeys={submittedActionRequestKeys}
              onActionRequestSubmitted={onActionRequestSubmitted}
              reviewConfigsMap={reviewConfigsMap ?? undefined}
              ui={messageUi}
              stream={stream}
              onResumeInterrupt={onResumeInterrupt}
              graphId={graphId}
              onEditMessage={onEditMessage}
              autoApprove={autoApprove}
              subAgentSteps={subAgentSteps}
            />
          </div>
        );
      })()}
      {open && (
        <div className="mt-2 space-y-2 border-l-2 border-border pl-3">
          {items.map((item) => {
            const isLastOverall = item.message.id === lastMessageId;
            const messageUi = ui?.filter(
              (u) => u.metadata?.message_id === item.message.id
            );
            const showCompactionBefore = compactionAnchorId === item.message.id;
            return (
              <React.Fragment key={item.message.id}>
                {showCompactionBefore && summarizationEvent && (
                  <CompactionSummary
                    content={summarizationEvent.content}
                    summarizedCount={summarizationEvent.cutoffIndex}
                  />
                )}
                <ChatMessage
                  message={item.message}
                  toolCalls={item.toolCalls}
                  isLoading={isLoading}
                  isStreaming={isStreaming && isLastOverall}
                  actionRequests={isLastOverall ? actionRequests : undefined}
                  submittedActionRequestKeys={submittedActionRequestKeys}
                  onActionRequestSubmitted={onActionRequestSubmitted}
                  reviewConfigsMap={
                    isLastOverall ? reviewConfigsMap ?? undefined : undefined
                  }
                  ui={messageUi}
                  stream={stream}
                  onResumeInterrupt={onResumeInterrupt}
                  graphId={graphId}
                  onEditMessage={onEditMessage}
                  autoApprove={autoApprove}
                  subAgentSteps={subAgentSteps}
                />
              </React.Fragment>
            );
          })}
          {/* Bottom collapse button — easy reach after scrolling through a long group. */}
          <button
            type="button"
            onClick={handleCollapse}
            className="flex w-full items-center justify-center gap-1.5 rounded-md py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Collapse ${count} action${count === 1 ? "" : "s"}`}
          >
            <ChevronUp
              aria-hidden="true"
              className="size-3.5"
            />
            Collapse
          </button>
        </div>
      )}
    </div>
  );
});
