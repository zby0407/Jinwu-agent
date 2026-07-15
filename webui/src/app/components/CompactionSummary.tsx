"use client";

import React, { useState } from "react";
import { ChevronRight, Layers } from "lucide-react";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { extractSummaryBody } from "@/lib/summarization";
import { cn } from "@/lib/utils";

interface CompactionSummaryProps {
  /** Raw summary message content (wrapper + body). */
  content: string;
  /** Number of earlier messages folded into this summary, if known. */
  summarizedCount?: number;
}

/**
 * Renders a conversation-compaction summary as a collapsible block, mirroring
 * the "Thinking" disclosure in ChatMessage. Collapsed by default so the (often
 * large) summary doesn't dominate the transcript — the backend keeps the full
 * history intact, this is just the context the agent was handed after compaction.
 */
export const CompactionSummary = React.memo<CompactionSummaryProps>(
  ({ content, summarizedCount }) => {
    const [open, setOpen] = useState(false);
    const body = extractSummaryBody(content);

    return (
      <div className="my-3 w-full">
        <div className="flex items-center gap-2">
          <div className="h-px flex-1 bg-border" />
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <Layers
              className="size-3.5 text-[var(--brand)]"
              aria-hidden="true"
            />
            Conversation compacted
            {typeof summarizedCount === "number" && summarizedCount > 0 && (
              <span className="text-muted-foreground/70">
                · {summarizedCount} earlier{" "}
                {summarizedCount === 1 ? "message" : "messages"} summarized
              </span>
            )}
            <ChevronRight
              className={cn(
                "size-3.5 transition-transform",
                open && "rotate-90"
              )}
              aria-hidden="true"
            />
          </button>
          <div className="h-px flex-1 bg-border" />
        </div>
        {open && (
          <div className="mt-2 overflow-hidden rounded-md border border-border bg-muted/20 px-4 py-3">
            <MarkdownContent content={body} />
          </div>
        )}
      </div>
    );
  }
);

CompactionSummary.displayName = "CompactionSummary";
