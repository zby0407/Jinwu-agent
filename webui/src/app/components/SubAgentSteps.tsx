"use client";

import { ToolCallBox } from "@/app/components/ToolCallBox";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import type { ToolCall } from "@/app/types/types";
import type { SubAgentStep } from "@/lib/subAgentActivity";
import { cn } from "@/lib/utils";

/** Render a sub-agent's steps as a vertical timeline of tool-call boxes (args +
 *  result + status) interleaved with the sub-agent's own text, reusing the SAME
 *  ToolCallBox + MarkdownContent the main agent uses. Shared by the inline
 *  sub-agent block (ChatMessage) and the background Agents board (AgentsPanel). */
export function SubAgentSteps({
  steps,
  hideFinalText,
  compact = false,
}: {
  steps: SubAgentStep[];
  hideFinalText?: boolean;
  compact?: boolean;
}) {
  const resultByCallId = new Map<string, string>();
  for (const s of steps) {
    if (s.kind === "tool_result" && s.toolCallId) {
      resultByCallId.set(s.toolCallId, s.text);
    }
  }
  // The sub-agent's final text is sometimes shown separately as the task Output;
  // when asked, drop that trailing text step here so it isn't duplicated.
  let lastTextIdx = -1;
  if (hideFinalText) {
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].kind === "text") {
        lastTextIdx = i;
        break;
      }
    }
  }
  return (
    <div className={cn("flex flex-col gap-1", compact && "gap-0.5")}>
      {steps.map((s, i) => {
        if (i === lastTextIdx) return null;
        if (s.kind === "tool_call") {
          const toolCall: ToolCall = {
            id: s.id,
            name: s.name,
            args: s.args,
            result: resultByCallId.get(s.id),
            status: resultByCallId.has(s.id) ? "completed" : "pending",
          };
          return (
            <ToolCallBox
              key={s.id || `tc-${i}`}
              toolCall={toolCall}
              compact={compact}
            />
          );
        }
        if (s.kind === "text") {
          return (
            <div
              key={`txt-${i}`}
              className={cn("px-2 text-sm", compact && "px-1 text-xs")}
            >
              <MarkdownContent
                content={s.text}
                className={
                  compact
                    ? "text-xs leading-5 [&_blockquote]:my-2 [&_blockquote]:pl-2 [&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-base [&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:text-sm [&_h4]:mb-1.5 [&_h4]:mt-3 [&_h4]:text-sm [&_ol]:my-2 [&_ol]:pl-5 [&_p]:mb-2 [&_table]:text-[11px] [&_td]:p-1.5 [&_th]:p-1.5 [&_ul]:my-2 [&_ul]:pl-5"
                    : undefined
                }
              />
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}
