"use client";

import { AlertTriangle, ChevronDown } from "lucide-react";
import {
  describeResearchTerminal,
  parseResearchTerminalMessage,
} from "@/lib/researchReviewTerminal";

export function ResearchTerminalNotice({ content }: { content: string }) {
  const terminal = parseResearchTerminalMessage(content);
  if (!terminal) return null;
  const copy = describeResearchTerminal(terminal);
  const Icon = AlertTriangle;

  return (
    <section
      aria-label={copy.title}
      className="mt-4 rounded-lg border border-destructive/35 bg-destructive/10 p-4"
    >
      <div className="flex items-start gap-3">
        <Icon
          className="mt-0.5 size-5 shrink-0 text-destructive"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">
            {copy.title}
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-foreground/85">
            {copy.description}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {copy.action}
          </p>
        </div>
      </div>
      <details className="mt-3 border-t border-border/70 pt-2 text-xs text-muted-foreground">
        <summary className="flex cursor-pointer list-none items-center gap-1 font-medium hover:text-foreground">
          <ChevronDown
            className="size-3.5"
            aria-hidden="true"
          />
          技术详情
        </summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-background/70 p-2 font-mono text-[11px] leading-relaxed">
          {content}
        </pre>
      </details>
    </section>
  );
}
