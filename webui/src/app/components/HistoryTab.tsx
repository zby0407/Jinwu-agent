"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, ChevronDown, Network } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { nodeColor } from "@/lib/observationGraph";

export interface ExecEntryClient {
  id: string;
  created_at: string;
  agent: string;
  session_id: string;
  project_id: string;
  summary: string;
  obs_ids: string[];
  path: string;
}

export interface ObsItemClient {
  id: string;
  created_at: string;
  summary: string;
  memory_type: string;
  scope: string;
}

export type TimelineItem =
  | ({ kind: "execution" } & ExecEntryClient)
  | ({ kind: "observation" } & ObsItemClient);

interface HistoryTabProps {
  items: TimelineItem[] | null;
  truncated: boolean;
  loading: boolean;
  error: string | null;
  highlightExecId?: string | null;
  onRefresh: () => void;
  onNavigateToObs: (obsId: string) => void;
}

function formatTimelineStamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (isToday(iso)) {
      return new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(d);
    }
    const now = new Date();
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
    }).format(d);
  } catch {
    return iso;
  }
}

function isToday(iso: string): boolean {
  try {
    const d = new Date(iso);
    const now = new Date();
    return (
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    );
  } catch {
    return false;
  }
}

function StatsBar({ items }: { items: TimelineItem[] }) {
  const projectCount = new Set(
    items
      .filter((i) => i.kind === "execution")
      .map((i) => (i as ExecEntryClient).project_id)
      .filter(Boolean)
  ).size;
  const today = items.filter((i) => i.created_at && isToday(i.created_at));
  const runsToday = today.filter((i) => i.kind === "execution").length;
  const obsToday = today.filter((i) => i.kind === "observation").length;

  const stats = [
    { n: projectCount, label: "Projects" },
    { n: items.length, label: "Total" },
    { n: runsToday, label: "Runs Today" },
    { n: obsToday, label: "Obs Today" },
  ];

  return (
    <div className="flex flex-shrink-0 border-b border-border">
      {stats.map((s) => (
        <div
          key={s.label}
          className="flex flex-1 flex-col items-center gap-1 border-r border-border py-2 last:border-r-0"
        >
          <span className="text-lg font-bold leading-none text-[var(--brand)]">
            {s.n}
          </span>
          <span className="text-[11px] leading-none text-muted-foreground">
            {s.label}
          </span>
        </div>
      ))}
    </div>
  );
}

function TimelineRow({
  created_at,
  dotColor,
  children,
}: {
  created_at: string;
  dotColor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-2">
      <time
        dateTime={created_at}
        className="w-14 flex-shrink-0 pt-3 text-right text-xs tabular-nums leading-none text-muted-foreground"
      >
        {formatTimelineStamp(created_at)}
      </time>

      <div className="relative flex w-3 flex-shrink-0 justify-center">
        <span
          className="absolute bottom-0 left-1/2 top-0 w-px -translate-x-1/2 bg-border"
          aria-hidden="true"
        />
        <span
          className="z-10 mt-3 size-2.5 rounded-full border-2 border-[var(--color-surface)]"
          style={{ background: dotColor }}
        />
      </div>

      {children}
    </div>
  );
}

function EntryCard({
  entry,
  onNavigateToObs,
  defaultOpen = false,
}: {
  entry: ExecEntryClient;
  onNavigateToObs: (id: string) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const toggle = () => setOpen((o) => !o);
  const cardRef = useRef<HTMLDivElement>(null);
  const label = `${open ? "Collapse" : "Expand"} ${entry.agent} execution: ${
    entry.summary
  }`;

  useEffect(() => {
    if (!defaultOpen) return;
    setOpen(true);
    cardRef.current?.scrollIntoView({ block: "center" });
  }, [defaultOpen]);

  return (
    <TimelineRow
      created_at={entry.created_at}
      dotColor="var(--brand)"
    >
      <div
        ref={cardRef}
        className={`hover:border-[var(--brand)]/40 mb-2 min-w-0 flex-1 overflow-hidden rounded-lg border bg-[var(--color-surface)] transition-colors ${
          defaultOpen ? "border-[var(--brand)]/40" : "border-border"
        }`}
      >
        <button
          type="button"
          aria-expanded={open}
          aria-label={label}
          title={label}
          onClick={toggle}
          className="flex w-full items-center gap-2 px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span className="flex-shrink-0 text-sm font-medium text-foreground">
            {entry.agent}
          </span>
          {!open && (
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {entry.summary}
            </span>
          )}
          <ChevronDown
            className={`ml-auto size-3.5 flex-shrink-0 text-muted-foreground transition-transform duration-200 ${
              open ? "rotate-180" : ""
            }`}
            aria-hidden="true"
          />
        </button>

        <div
          className="grid transition-[grid-template-rows] duration-200"
          style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
        >
          {open && (
            <div className="overflow-hidden">
              {entry.project_id && (
                <p className="px-3 pb-2 font-mono text-[11px] text-muted-foreground">
                  {entry.project_id}
                </p>
              )}
              <p className="border-t border-border px-3 py-3 text-sm leading-relaxed text-foreground">
                {entry.summary}
              </p>
              {entry.obs_ids.length > 0 && (
                <div className="border-t border-border px-3 py-2.5">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Linked Observations
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {entry.obs_ids.map((id) => (
                      <button
                        key={id}
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onNavigateToObs(id);
                        }}
                        className="border-[var(--brand)]/40 rounded border bg-background px-2 py-0.5 font-mono text-[11px] text-[var(--brand)] transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={`Jump to ${id} in Knowledge graph`}
                        aria-label={`Jump to observation ${id} in Knowledge graph`}
                      >
                        {id.slice(0, 14)}…
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </TimelineRow>
  );
}

function ObsCard({
  obs,
  onNavigateToObs,
}: {
  obs: ObsItemClient;
  onNavigateToObs: (id: string) => void;
}) {
  const color = nodeColor(obs.memory_type);

  return (
    <TimelineRow
      created_at={obs.created_at}
      dotColor={color}
    >
      <button
        type="button"
        onClick={() => onNavigateToObs(obs.id)}
        title="View in Knowledge graph"
        aria-label={`View observation in Knowledge graph: ${obs.summary}`}
        className="hover:border-[var(--brand)]/40 mb-2 min-w-0 flex-1 cursor-pointer overflow-hidden rounded-lg border border-dashed border-border bg-[var(--color-surface)] px-3 py-2.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-center gap-2">
          <span
            className="flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white"
            style={{ background: color }}
          >
            {obs.memory_type}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-foreground">
            {obs.summary}
          </span>
          <Network
            className="ml-auto size-3.5 flex-shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
        </div>
      </button>
    </TimelineRow>
  );
}

export function HistoryTab({
  items,
  truncated,
  loading,
  error,
  highlightExecId,
  onRefresh,
  onNavigateToObs,
}: HistoryTabProps) {
  if (loading && !items) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2
          className="size-4 animate-spin"
          aria-hidden="true"
        />
        Loading history…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8">
        <p className="text-sm text-destructive">{error}</p>
        <button
          type="button"
          onClick={onRefresh}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <RefreshCw
            className="size-3.5"
            aria-hidden="true"
          />
          Retry
        </button>
      </div>
    );
  }
  if (!items || items.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        No activity yet.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <StatsBar items={items} />
      <ScrollArea
        type="always"
        className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block [&_[data-slot=scroll-area-viewport]>div]:!w-full"
      >
        <div className="p-4">
          <div>
            {items.map((item) =>
              item.kind === "execution" ? (
                <EntryCard
                  key={`e-${item.id}`}
                  entry={item}
                  onNavigateToObs={onNavigateToObs}
                  defaultOpen={highlightExecId === item.id}
                />
              ) : (
                <ObsCard
                  key={`o-${item.id}`}
                  obs={item}
                  onNavigateToObs={onNavigateToObs}
                />
              )
            )}
          </div>
          {truncated && (
            <p className="mt-2 text-center text-xs text-muted-foreground">
              Showing newest {items.length} items.
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
