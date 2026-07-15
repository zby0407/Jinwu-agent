"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Network,
  Activity,
  FolderOpen,
  Clock,
  ArrowRight,
  Pin,
  MessageSquare,
  History,
} from "lucide-react";
import { nodeColor, type ObsGraphData } from "@/lib/observationGraph";
import { listScheduledTasks } from "@/app/hooks/useScheduledTasks";
import { useThreads } from "@/app/hooks/useThreads";
import { formatTime } from "@/lib/time";

type MemoryTab = "identity" | "knowledge" | "history";
type NavTarget =
  | { view: "memory"; tab: MemoryTab; obsId?: string; execId?: string }
  | { view: "schedule" }
  | { view: "workspace" };

interface ExecEntry {
  id: string;
  created_at: string;
  agent: string;
  project_id: string;
  summary: string;
}

interface ActivityItem {
  kind: "execution" | "observation";
  id: string;
  created_at: string;
  createdAtMs: number;
  summary: string;
  label: string;
  color: string;
}

interface ResearchDashboardProps {
  onNavigate: (target: NavTarget) => void;
  onOpenThread: (id: string) => void;
}

function formatSize(bytes: number): { num: string; unit: string } {
  if (bytes <= 0) return { num: "0", unit: "" };
  if (bytes < 1024) return { num: String(bytes), unit: "B" };
  if (bytes < 1024 * 1024)
    return { num: String(Math.round(bytes / 1024)), unit: "KB" };
  return { num: (bytes / (1024 * 1024)).toFixed(1), unit: "MB" };
}

async function readJson<T>(response: Response): Promise<T | null> {
  const data = (await response.json().catch(() => null)) as T | null;
  return response.ok ? data : null;
}

function timeValue(iso: string): number {
  const value = new Date(iso).getTime();
  return Number.isFinite(value) ? value : 0;
}

function activityAriaLabel(item: ActivityItem): string {
  const kind =
    item.kind === "observation" ? "recent observation" : "recent execution";
  // formatTime renders an em dash for an unusable timestamp; don't read it out.
  const when = formatTime(new Date(item.createdAtMs || Number.NaN));
  const hasWhen = when !== "" && when !== "—";
  return `Open ${kind} from ${item.label}${hasWhen ? `, ${when}` : ""}`;
}

export function ResearchDashboard({
  onNavigate,
  onOpenThread,
}: ResearchDashboardProps) {
  const { data: threadPages } = useThreads({ limit: 100 });
  const pinned = useMemo(
    () =>
      (threadPages ?? [])
        .flat()
        .filter((t) => t.pinned)
        .sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime())
        .slice(0, 5),
    [threadPages]
  );

  const [obsCount, setObsCount] = useState(0);
  const [runCount, setRunCount] = useState(0);
  const [wsBytes, setWsBytes] = useState(0);
  const [scheduledCount, setScheduledCount] = useState(0);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [ready, setReady] = useState(false);
  const reqRef = useRef(0);

  useEffect(() => {
    const reqId = ++reqRef.current;
    (async () => {
      try {
        const [obsRes, execRes, wsRes, scheduled] = await Promise.all([
          fetch("/api/memory/observations", { cache: "no-store" }).catch(
            () => null
          ),
          fetch("/api/memory/executions", { cache: "no-store" }).catch(
            () => null
          ),
          fetch("/api/workspace?recursive=1", { cache: "no-store" }).catch(
            () => null
          ),
          listScheduledTasks().catch(() => []),
        ]);
        const obs = obsRes ? await readJson<ObsGraphData>(obsRes) : null;
        const exec = execRes
          ? await readJson<{ entries?: ExecEntry[] }>(execRes)
          : null;
        const ws = wsRes
          ? await readJson<{ entries?: Array<{ size?: number }> }>(wsRes)
          : null;
        if (reqRef.current !== reqId) return;
        setScheduledCount(scheduled.length);
        setWsBytes(
          (Array.isArray(ws?.entries) ? ws.entries : []).reduce(
            (sum, f) => sum + (typeof f.size === "number" ? f.size : 0),
            0
          )
        );

        const obsNodes = Array.isArray(obs?.nodes) ? obs.nodes : [];
        const execEntries = Array.isArray(exec?.entries) ? exec.entries : [];

        setObsCount(obsNodes.length);
        setRunCount(execEntries.length);

        const items: ActivityItem[] = [
          ...execEntries.map((e) => ({
            kind: "execution" as const,
            id: e.id,
            created_at: e.created_at,
            createdAtMs: timeValue(e.created_at),
            summary: e.summary || "Execution completed",
            label: e.agent || "agent",
            color: "var(--brand)",
          })),
          ...obsNodes.map((n) => ({
            kind: "observation" as const,
            id: n.id,
            created_at: n.created_at,
            createdAtMs: timeValue(n.created_at),
            summary: n.summary || n.id,
            label: n.memory_type || "memory",
            color: nodeColor(n.memory_type),
          })),
        ];
        items.sort((a, b) => b.createdAtMs - a.createdAtMs);
        setActivity(items.slice(0, 5));
        setReady(true);
      } catch {
        if (reqRef.current === reqId) setReady(true);
      }
    })();
  }, []);

  const hasStats =
    ready &&
    (obsCount > 0 || runCount > 0 || scheduledCount > 0 || wsBytes > 0);

  if (!hasStats && pinned.length === 0) return null;

  const wsSize = formatSize(wsBytes);

  const stats: Array<{
    value: string | number;
    unit?: string;
    label: string;
    hint: string;
    Icon: typeof Network;
    target: NavTarget;
  }> = [
    {
      value: obsCount,
      label: "Knowledge",
      hint: "Observations 金乌 has learned.",
      Icon: Network,
      target: { view: "memory", tab: "knowledge" },
    },
    {
      value: runCount + obsCount,
      label: "Timeline",
      hint: "Runs and observations on the activity timeline.",
      Icon: Activity,
      target: { view: "memory", tab: "history" },
    },
    {
      value: wsSize.num,
      unit: wsSize.unit,
      label: "Workspace",
      hint: "Total size of files in the current workspace.",
      Icon: FolderOpen,
      target: { view: "workspace" },
    },
    {
      value: scheduledCount,
      label: "Scheduled",
      hint: "Recurring scheduled tasks.",
      Icon: Clock,
      target: { view: "schedule" },
    },
  ];

  return (
    <div className="mt-7 w-full max-w-xl text-left sm:mt-8">
      {hasStats && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {stats.map(({ value, unit, label, hint, Icon, target }) => (
            <button
              key={label}
              type="button"
              onClick={() => onNavigate(target)}
              title={hint}
              aria-label={`${label}: ${value}${
                unit ? ` ${unit}` : ""
              }. ${hint}`}
              className="hover:border-[var(--brand)]/40 flex min-h-[82px] flex-col items-center justify-center gap-1 rounded-md border border-border bg-[var(--color-surface)] px-2 py-2.5 transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Icon
                className="size-4 text-[var(--brand)]"
                aria-hidden="true"
              />
              <span className="relative inline-block text-base font-bold leading-none text-foreground sm:text-lg">
                {value}
                {unit && (
                  <span className="absolute left-full top-0 ml-0.5 text-[9px] font-medium text-muted-foreground/70">
                    {unit}
                  </span>
                )}
              </span>
              <span className="text-[11px] leading-none text-muted-foreground">
                {label}
              </span>
            </button>
          ))}
        </div>
      )}

      {pinned.length > 0 && (
        <div className="mt-4 sm:mt-5">
          <div className="mb-2 flex items-center gap-1.5">
            <Pin
              className="size-3 text-muted-foreground"
              aria-hidden="true"
            />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Your research
            </span>
          </div>
          <div className="space-y-1">
            {pinned.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => onOpenThread(t.id)}
                title={`Open ${t.title}`}
                aria-label={`Open pinned research thread: ${t.title}`}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <MessageSquare
                  className="size-3.5 flex-shrink-0 text-[var(--brand)]"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                  {t.title}
                </span>
                <span className="flex-shrink-0 text-[11px] tabular-nums text-muted-foreground">
                  {formatTime(t.updatedAt)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {hasStats && activity.length > 0 && (
        <div className="mt-4 sm:mt-5">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <History
                className="size-3 text-muted-foreground"
                aria-hidden="true"
              />
              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Recent activity
              </span>
            </div>
            <button
              type="button"
              onClick={() => onNavigate({ view: "memory", tab: "history" })}
              className="flex items-center gap-1 text-[11px] text-[var(--brand)] transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              View all
              <ArrowRight
                className="size-3"
                aria-hidden="true"
              />
            </button>
          </div>
          <div className="space-y-1">
            {activity.map((item) => (
              <button
                key={`${item.kind}-${item.id}`}
                type="button"
                onClick={() =>
                  onNavigate(
                    item.kind === "observation"
                      ? { view: "memory", tab: "knowledge", obsId: item.id }
                      : { view: "memory", tab: "history", execId: item.id }
                  )
                }
                title={item.summary}
                aria-label={activityAriaLabel(item)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span
                  className="size-2 flex-shrink-0 rounded-full"
                  style={{ background: item.color }}
                  aria-hidden="true"
                />
                <span className="flex-shrink-0 text-xs font-medium text-foreground">
                  {item.label}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {item.summary}
                </span>
                <span className="flex-shrink-0 text-[11px] tabular-nums text-muted-foreground">
                  {formatTime(new Date(item.createdAtMs || Number.NaN))}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
