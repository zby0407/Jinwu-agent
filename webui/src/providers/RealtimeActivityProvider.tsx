"use client";

import { createContext, useContext, useState, ReactNode } from "react";
import type { SubAgentStep } from "@/lib/subAgentActivity";
import type { TodoItem } from "@/app/types/types";

export interface ActiveToolCall {
  id?: string;
  name: string;
}

export interface ActiveSubAgent {
  key: string;
  name: string;
  steps: SubAgentStep[];
  latestStep?: SubAgentStep;
}

export interface RealtimeActivityState {
  isLoading: boolean;
  hasInterrupt: boolean;
  interruptType?: string;
  activeToolCalls: ActiveToolCall[];
  subAgents: ActiveSubAgent[];
  todos: TodoItem[];
}

const RealtimeActivityContext = createContext<{
  state: RealtimeActivityState;
  setState: React.Dispatch<React.SetStateAction<RealtimeActivityState>>;
} | null>(null);

export function RealtimeActivityProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RealtimeActivityState>({
    isLoading: false,
    hasInterrupt: false,
    activeToolCalls: [],
    subAgents: [],
    todos: [],
  });
  return (
    <RealtimeActivityContext.Provider value={{ state, setState }}>
      {children}
    </RealtimeActivityContext.Provider>
  );
}

export function useRealtimeActivity() {
  const ctx = useContext(RealtimeActivityContext);
  if (!ctx) {
    throw new Error(
      "useRealtimeActivity must be used within RealtimeActivityProvider"
    );
  }
  return ctx;
}
