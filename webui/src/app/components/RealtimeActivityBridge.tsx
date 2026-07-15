"use client";

import { useEffect, useMemo } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { useRealtimeActivity } from "@/providers/RealtimeActivityProvider";
import type { Message } from "@langchain/langgraph-sdk";
import type {
  ActiveToolCall,
  ActiveSubAgent,
} from "@/providers/RealtimeActivityProvider";

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

  useEffect(() => {
    setState((prev) => ({
      ...prev,
      isLoading,
      hasInterrupt: !!interrupt,
      interruptType: (interrupt?.value as { type?: string } | undefined)?.type,
      activeToolCalls,
      subAgents,
      todos,
    }));
  }, [
    isLoading,
    interrupt,
    activeToolCalls,
    subAgents,
    todos,
    setState,
  ]);

  return null;
}
