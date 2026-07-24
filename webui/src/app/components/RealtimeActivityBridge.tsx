"use client";

import { useEffect, useMemo } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { useRealtimeActivity } from "@/providers/RealtimeActivityProvider";
import type { Message } from "@langchain/langgraph-sdk";
import type {
  ActiveToolCall,
  ActiveSubAgent,
  RealtimeActivityState,
} from "@/providers/RealtimeActivityProvider";
import type { TodoItem } from "@/app/types/types";

function getLatestToolCalls(
  messages: Message[],
  isLoading: boolean
): ActiveToolCall[] {
  if (!isLoading) return [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.type !== "ai") continue;

    const additional = m.additional_kwargs?.tool_calls;
    if (Array.isArray(additional) && additional.length > 0) {
      const calls = additional
        .filter((tc: any) => tc.function?.name || tc.name)
        .map((tc: any) => ({
          id: tc.id,
          name: (tc.function?.name || tc.name || "tool") as string,
        }));
      if (calls.length > 0) return calls;
    }

    const toolCalls = (m as { tool_calls?: { id?: string; name?: string }[] })
      .tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const calls = toolCalls
        .filter((tc) => tc.name)
        .map((tc) => ({ id: tc.id, name: tc.name as string }));
      if (calls.length > 0) return calls;
    }
  }
  return [];
}

function sameToolCalls(a: ActiveToolCall[], b: ActiveToolCall[]): boolean {
  return (
    a === b ||
    (a.length === b.length &&
      a.every(
        (item, index) =>
          item.id === b[index]?.id && item.name === b[index]?.name
      ))
  );
}

function sameSubAgents(a: ActiveSubAgent[], b: ActiveSubAgent[]): boolean {
  return (
    a === b ||
    (a.length === b.length &&
      a.every((item, index) => {
        const other = b[index];
        return (
          item.key === other?.key &&
          item.name === other.name &&
          item.steps === other.steps &&
          item.latestStep === other.latestStep
        );
      }))
  );
}

function sameTodos(a: TodoItem[], b: TodoItem[]): boolean {
  return (
    a === b ||
    (a.length === b.length &&
      a.every((item, index) => {
        const other = b[index];
        return (
          item.id === other?.id &&
          item.content === other.content &&
          item.status === other.status &&
          item.updatedAt === other.updatedAt
        );
      }))
  );
}

function sameActivityState(
  prev: RealtimeActivityState,
  next: RealtimeActivityState
): boolean {
  return (
    prev.isLoading === next.isLoading &&
    prev.hasInterrupt === next.hasInterrupt &&
    prev.interruptType === next.interruptType &&
    sameToolCalls(prev.activeToolCalls, next.activeToolCalls) &&
    sameSubAgents(prev.subAgents, next.subAgents) &&
    sameTodos(prev.todos, next.todos)
  );
}

export function RealtimeActivityBridge() {
  const { isLoading, interrupt, messages, subAgentActivity, todos } =
    useChatContext();
  const { setState } = useRealtimeActivity();

  const activeToolCalls = useMemo(
    () => getLatestToolCalls(messages, isLoading),
    [messages, isLoading]
  );

  const subAgents = useMemo<ActiveSubAgent[]>(() => {
    return Object.entries(subAgentActivity).map(([key, steps]) => ({
      key,
      name: key.split("|").pop() || key,
      steps,
      latestStep: steps[steps.length - 1],
    }));
  }, [subAgentActivity]);

  const hasInterrupt = !!interrupt;
  const interruptType = (interrupt?.value as { type?: string } | undefined)
    ?.type;

  useEffect(() => {
    const next: RealtimeActivityState = {
      isLoading,
      hasInterrupt,
      interruptType,
      activeToolCalls,
      subAgents,
      todos,
    };
    setState((prev) => (sameActivityState(prev, next) ? prev : next));
  }, [
    isLoading,
    hasInterrupt,
    interruptType,
    activeToolCalls,
    subAgents,
    todos,
    setState,
  ]);

  return null;
}
