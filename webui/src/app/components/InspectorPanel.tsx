"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { FolderOpen, PackageOpen, Zap } from "lucide-react";
import { ArtifactsPanel } from "@/app/components/ArtifactsPanel";
import { PanelEdgeToggle } from "@/app/components/PanelEdgeToggle";
import { RealtimeActivityPanel } from "@/app/components/RealtimeActivityPanel";
import { SidebarResizeHandle, type SidebarSize } from "@/app/components/SidebarResizeHandle";
import { WorkspacePanel } from "@/app/components/WorkspacePanel";

const tabs = [
  { id: "workspace", label: "工作区", Icon: FolderOpen },
  { id: "artifacts", label: "产物", Icon: PackageOpen },
  { id: "activity", label: "实时活动", Icon: Zap },
] as const;

const panelId = "chat-workspace-drawer";
const slideDuration = 240;

export type InspectorTab = (typeof tabs)[number]["id"];

interface InspectorPanelProps {
  open: boolean;
  overlay: boolean;
  tab: InspectorTab;
  size?: SidebarSize;
  onTabChange: (tab: InspectorTab) => void;
  onToggle: () => void;
}

export function InspectorPanel({ open, overlay, tab, size, onTabChange, onToggle }: InspectorPanelProps) {
  const [keepContent, setKeepContent] = useState(open);
  const [dragging, setDragging] = useState(false);
  const canResize = open && !!size;

  useEffect(() => {
    if (open) {
      setKeepContent(true);
      return;
    }
    // Keep the selected tab visible while the drawer slides out.
    const timer = window.setTimeout(() => setKeepContent(false), slideDuration);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!canResize) setDragging(false);
  }, [canResize]);

  const dockStyle = {
    "--workspace-drawer-width": size ? `${size.width}px` : undefined,
    transitionDuration: canResize && dragging ? "0ms" : `${slideDuration}ms`,
  } as CSSProperties;

  return (
    <aside
      className="chat-workspace-dock"
      data-open={open}
      data-overlay={overlay}
      style={dockStyle}
      aria-label="研究工具"
      onKeyDown={event => {
        if (
          event.key !== "Escape" || event.defaultPrevented || !open ||
          !event.currentTarget.contains(event.target as Node)
        ) return;
        event.preventDefault();
        event.currentTarget.querySelector("button")?.focus();
        onToggle();
      }}
    >
      <PanelEdgeToggle
        side="right"
        open={open}
        controls={panelId}
        onClick={onToggle}
        label={open ? "收起右侧工作区" : "展开右侧工作区"}
        className="chat-workspace-toggle"
      />
      {open && size && (
        <SidebarResizeHandle
          side="right"
          label="调整右侧工作区宽度"
          controls={panelId}
          size={size}
          onDraggingChange={setDragging}
        />
      )}
      <div id={panelId} className="chat-workspace-drawer" inert={!open} aria-hidden={!open}>
        {(open || keepContent) && (
          <>
            <div role="tablist" aria-label="研究工作区" className="grid shrink-0 grid-cols-3 gap-1 border-b border-border bg-sky-50/60 p-2">
              {tabs.map(({ id, label, Icon }, index) => (
                <button
                  key={id}
                  id={`${panelId}-${id}`}
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  aria-controls={`${panelId}-content`}
                  tabIndex={tab === id ? 0 : -1}
                  onClick={() => onTabChange(id)}
                  onKeyDown={event => {
                    const next = ({
                      ArrowLeft: (index + tabs.length - 1) % tabs.length,
                      ArrowRight: (index + 1) % tabs.length,
                      Home: 0,
                      End: tabs.length - 1,
                    } as Record<string, number>)[event.key];
                    if (typeof next !== "number") return;
                    event.preventDefault();
                    onTabChange(tabs[next].id);
                    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
                  }}
                  className="platform-blue-hover flex min-h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-1 py-1.5 text-xs font-medium text-slate-600 aria-selected:bg-sky-100 aria-selected:text-sky-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <Icon className="size-3.5 shrink-0 text-sky-700" aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>
            <div id={`${panelId}-content`} role="tabpanel" aria-labelledby={`${panelId}-${tab}`} className="min-h-0 flex-1 overflow-hidden">
              {tab === "workspace" && <div className="h-full overflow-y-auto p-3"><WorkspacePanel /></div>}
              {tab === "artifacts" && <ArtifactsPanel />}
              {tab === "activity" && <RealtimeActivityPanel />}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
