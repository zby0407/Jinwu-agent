"use client";

import {
  Loader2,
  Bot,
  CheckCircle2,
  AlertCircle,
  Wrench,
  ListTodo,
  Zap,
} from "lucide-react";
import { useRealtimeActivity } from "@/providers/RealtimeActivityProvider";
import { cn } from "@/lib/utils";
import { lastTextOf } from "@/lib/subAgentActivity";

export function RealtimeActivityPanel() {
  const { state } = useRealtimeActivity();
  const { isLoading, hasInterrupt, interruptType, activeToolCalls, subAgents, todos } =
    state;

  const status = (() => {
    if (hasInterrupt) {
      if (interruptType === "ask_user") {
        return {
          label: "等待用户输入",
          icon: AlertCircle,
          color: "text-amber-500",
          spin: false,
        };
      }
      return {
        label: "等待工具审批",
        icon: AlertCircle,
        color: "text-amber-500",
        spin: false,
      };
    }
    if (isLoading) {
      return {
        label: "思考中…",
        icon: Loader2,
        color: "text-[var(--brand)]",
        spin: true,
      };
    }
    return {
      label: "空闲",
      icon: CheckCircle2,
      color: "text-green-500",
      spin: false,
    };
  })();

  const activeTodos = todos.filter((t) => t.status === "in_progress");
  const isIdle =
    status.label === "空闲" &&
    activeToolCalls.length === 0 &&
    subAgents.length === 0 &&
    activeTodos.length === 0;

  return (
    <div className="flex h-full flex-col border-l border-border bg-sidebar">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <Zap
          className="size-4 text-[var(--brand)]"
          aria-hidden="true"
        />
        <h3 className="text-sm font-semibold">实时活动</h3>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Main agent status */}
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <status.icon
              className={cn("size-4", status.color, status.spin && "animate-spin")}
              aria-hidden="true"
            />
            <span>{status.label}</span>
          </div>
        </div>

        {/* Active tool calls */}
        {activeToolCalls.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Wrench
                className="size-3.5"
                aria-hidden="true"
              />
              正在调用工具
            </h4>
            <div className="space-y-1.5">
              {activeToolCalls.map((tc, i) => (
                <div
                  key={tc.id || i}
                  className="rounded-md border border-border bg-card p-2"
                >
                  <div className="truncate text-xs font-medium">{tc.name}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Active sub-agents */}
        {subAgents.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Bot
                className="size-3.5"
                aria-hidden="true"
              />
              子智能体
            </h4>
            <div className="space-y-2">
              {subAgents.map((agent) => {
                const latestText = lastTextOf(agent.steps);
                return (
                  <div
                    key={agent.key}
                    className="rounded-lg border border-border bg-card p-2"
                  >
                    <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold">
                      <Loader2
                        className="size-3 animate-spin text-[var(--brand)]"
                        aria-hidden="true"
                      />
                      <span className="truncate">{agent.name}</span>
                    </div>
                    {agent.latestStep && (
                      <div className="text-xs text-muted-foreground">
                        {agent.latestStep.kind === "tool_call"
                          ? `调用: ${agent.latestStep.name}`
                          : latestText.slice(0, 120)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Active todos */}
        {activeTodos.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <ListTodo
                className="size-3.5"
                aria-hidden="true"
              />
              进行中任务
            </h4>
            <div className="space-y-1.5">
              {activeTodos.map((todo) => (
                <div
                  key={todo.id}
                  className="flex items-start gap-2 rounded-md border border-border bg-card p-2 text-xs"
                >
                  <Loader2
                    className="mt-0.5 size-3 animate-spin text-[var(--brand)]"
                    aria-hidden="true"
                  />
                  <span>{todo.content}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {isIdle && (
          <p className="text-center text-xs text-muted-foreground">
            AI 当前没有处理中的任务
          </p>
        )}
      </div>
    </div>
  );
}
