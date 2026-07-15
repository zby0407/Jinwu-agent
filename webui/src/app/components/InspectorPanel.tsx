"use client";

import { X, FolderOpen, Bot, Zap, PackageOpen } from "lucide-react";
import { useQueryState } from "nuqs";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { WorkspacePanel } from "@/app/components/WorkspacePanel";
import { AgentsPanel } from "@/app/components/AgentsPanel";
import { RealtimeActivityPanel } from "@/app/components/RealtimeActivityPanel";
import { ArtifactsPanel } from "@/app/components/ArtifactsPanel";
import type { MainChatReporter } from "@/lib/asyncAgents";

interface InspectorPanelProps {
  onClose: () => void;
  // Loop a finished async agent's result back to the main chat (Agents tab).
  // Null when the chat view isn't mounted (e.g. viewing Skills/Memory).
  onReportToMainChat?: MainChatReporter | null;
}

type InspectorTab = "workspace" | "agents" | "activity" | "artifacts";

/**
 * Dockable right-hand inspector with tabs:
 *  - Activity: real-time view of what the AI is currently processing.
 *  - Workspace: the on-disk workspace browser.
 *  - Artifacts: numbered research outputs generated in the workspace.
 *  - Agents: background async sub-agents (writing / data-analysis) this
 *    conversation launched, with live status + steps.
 * The active tab is mirrored to the `inspectorTab` URL param so the composer's
 * "agents running" indicator can deep-link straight to the Agents tab.
 */
export function InspectorPanel({
  onClose,
  onReportToMainChat,
}: InspectorPanelProps) {
  const [tabParam, setTab] = useQueryState("inspectorTab");
  const tab: InspectorTab =
    tabParam === "workspace"
      ? "workspace"
      : tabParam === "agents"
      ? "agents"
      : tabParam === "artifacts"
      ? "artifacts"
      : "activity";

  return (
    <div className="flex h-full flex-col border-l border-border bg-sidebar">
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border px-2">
        <div
          role="tablist"
          aria-label="Inspector"
          className="flex items-center gap-1"
        >
          <button
            type="button"
            role="tab"
            aria-selected={tab === "activity"}
            onClick={() => setTab(null)}
            className={cn(
              "gold-tab flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold-shadow)]",
              tab === "activity" && "is-active"
            )}
          >
            <Zap
              className="size-4 text-[var(--brand)]"
              aria-hidden="true"
            />
            {"\u6d3b\u52a8"}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "workspace"}
            onClick={() => setTab("workspace")}
            className={cn(
              "gold-tab flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold-shadow)]",
              tab === "workspace" && "is-active"
            )}
          >
            <FolderOpen
              className="size-4 text-[var(--brand)]"
              aria-hidden="true"
            />
            {"\u5de5\u4f5c\u533a"}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "agents"}
            onClick={() => setTab("agents")}
            className={cn(
              "gold-tab flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold-shadow)]",
              tab === "agents" && "is-active"
            )}
          >
            <Bot
              className="size-4 text-[var(--brand)]"
              aria-hidden="true"
            />
            {"Agents"}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "artifacts"}
            onClick={() => setTab("artifacts")}
            className={cn(
              "gold-tab flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold-shadow)]",
              tab === "artifacts" && "is-active"
            )}
          >
            <PackageOpen
              className="size-4 text-[var(--brand)]"
              aria-hidden="true"
            />
            {"\u4ea7\u7269"}
          </button>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={onClose}
          aria-label="\u5173\u95ed\u53f3\u4fa7\u680f"
          title="\u5173\u95ed"
        >
          <X
            className="size-4"
            aria-hidden="true"
          />
        </Button>
      </div>
      {tab === "agents" ? (
        <div className="min-h-0 flex-1 overflow-hidden p-3">
          <AgentsPanel onReportToMainChat={onReportToMainChat} />
        </div>
      ) : tab === "workspace" ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <WorkspacePanel />
        </div>
      ) : tab === "artifacts" ? (
        <div className="min-h-0 flex-1 overflow-hidden p-3">
          <ArtifactsPanel />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-hidden">
          <RealtimeActivityPanel />
        </div>
      )}
    </div>
  );
}
