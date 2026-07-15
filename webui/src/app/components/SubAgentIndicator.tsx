"use client";

import React from "react";
import { Button } from "@/components/ui/button";
import { Bot, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import type { SubAgent } from "@/app/types/types";

interface SubAgentIndicatorProps {
  subAgent: SubAgent;
  onClick: () => void;
  isExpanded?: boolean;
}

export const SubAgentIndicator = React.memo<SubAgentIndicatorProps>(
  ({ subAgent, onClick, isExpanded = true }) => {
    const running = subAgent.status === "pending";
    return (
      <div className="w-fit max-w-[70vw] overflow-hidden rounded-lg bg-card">
        <Button
          variant="ghost"
          size="sm"
          onClick={onClick}
          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
        >
          <span className="bg-[var(--brand)]/10 flex size-5 shrink-0 items-center justify-center rounded-md text-[var(--brand)]">
            {running ? (
              <Loader2
                className="size-3.5 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <Bot
                className="size-3.5"
                aria-hidden="true"
              />
            )}
          </span>
          <span className="truncate text-sm font-semibold text-foreground">
            {subAgent.subAgentName}
          </span>
          <span className="text-xs font-normal text-muted-foreground">
            subagent
          </span>
          {isExpanded ? (
            <ChevronUp
              size={14}
              className="ml-auto shrink-0 text-muted-foreground"
            />
          ) : (
            <ChevronDown
              size={14}
              className="ml-auto shrink-0 text-muted-foreground"
            />
          )}
        </Button>
      </div>
    );
  }
);

SubAgentIndicator.displayName = "SubAgentIndicator";
