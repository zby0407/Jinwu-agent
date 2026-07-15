"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Network, Fingerprint, History, RefreshCw } from "lucide-react";
import { setMemorySeenAt } from "@/lib/memoryActivity";
import { ObservationGraph } from "@/app/components/ObservationGraph";
import { IdentityTab } from "@/app/components/IdentityTab";
import {
  HistoryTab,
  type ExecEntryClient,
  type TimelineItem,
} from "@/app/components/HistoryTab";
import type { ObsGraphData } from "@/lib/observationGraph";
import { cn } from "@/lib/utils";

interface MemoryPanelProps {
  initialTab?: "identity" | "knowledge" | "history" | null;
  initialObsId?: string | null;
  initialExecId?: string | null;
}

function isMemoryTab(
  value: unknown
): value is "identity" | "knowledge" | "history" {
  return value === "identity" || value === "knowledge" || value === "history";
}

export function MemoryPanel({
  initialTab,
  initialObsId,
  initialExecId,
}: MemoryPanelProps = {}) {
  const [listing, setListing] = useState<{
    entries: Array<{
      path: string;
      size: number;
      mtime: number;
      editable: boolean;
    }>;
  } | null>(null);
  const [listingLoading, setListingLoading] = useState(true);

  const [activeTab, setActiveTab] = useState<
    "identity" | "knowledge" | "history"
  >(isMemoryTab(initialTab) ? initialTab : "identity");

  const [obsData, setObsData] = useState<ObsGraphData | null>(null);
  const [obsLoading, setObsLoading] = useState(false);
  const [obsError, setObsError] = useState<string | null>(null);

  const [execData, setExecData] = useState<{
    entries: ExecEntryClient[];
    truncated: boolean;
  } | null>(null);
  const [execLoading, setExecLoading] = useState(false);
  const [execError, setExecError] = useState<string | null>(null);

  const [highlightObsId, setHighlightObsId] = useState<string | null>(
    initialObsId ?? null
  );

  const obsReqRef = useRef(0);
  const execReqRef = useRef(0);

  const loadListing = useCallback(async () => {
    setListingLoading(true);
    try {
      const res = await fetch("/api/memory");
      const data = (await res.json()) as {
        entries: Array<{
          path: string;
          size: number;
          mtime: number;
          editable: boolean;
        }>;
      };
      if (!res.ok) return;
      setListing(data);
      const latest = data.entries.reduce(
        (max, e) => (e.mtime > max ? e.mtime : max),
        0
      );
      if (latest > 0) setMemorySeenAt(latest);
    } catch {
      void 0;
    } finally {
      setListingLoading(false);
    }
  }, []);

  const loadObservations = useCallback(async () => {
    const reqId = ++obsReqRef.current;
    setObsLoading(true);
    setObsError(null);
    try {
      const res = await fetch("/api/memory/observations", {
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (reqId !== obsReqRef.current) return;
      if (!res.ok)
        throw new Error((data as { error?: string }).error || "Failed.");
      setObsData(data as ObsGraphData);
    } catch (e) {
      if (reqId !== obsReqRef.current) return;
      setObsError(e instanceof Error ? e.message : "Failed to load.");
    } finally {
      if (reqId === obsReqRef.current) setObsLoading(false);
    }
  }, []);

  const loadExecutions = useCallback(async () => {
    const reqId = ++execReqRef.current;
    setExecLoading(true);
    setExecError(null);
    try {
      const res = await fetch("/api/memory/executions", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (reqId !== execReqRef.current) return;
      if (!res.ok)
        throw new Error((data as { error?: string }).error || "Failed.");
      setExecData(data as { entries: ExecEntryClient[]; truncated: boolean });
    } catch (e) {
      if (reqId !== execReqRef.current) return;
      setExecError(e instanceof Error ? e.message : "Failed to load.");
    } finally {
      if (reqId === execReqRef.current) setExecLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadListing();
  }, [loadListing]);

  useEffect(() => {
    if (isMemoryTab(initialTab)) {
      setActiveTab(initialTab);
    }
  }, [initialTab]);

  useEffect(() => {
    if (!initialObsId) {
      setHighlightObsId(null);
      return;
    }
    setHighlightObsId(initialObsId);
    setActiveTab("knowledge");
  }, [initialObsId]);

  useEffect(() => {
    if (initialExecId) setActiveTab("history");
  }, [initialExecId]);

  useEffect(() => {
    if (activeTab !== "history") return;
    const refresh = () => {
      void loadExecutions();
      void loadObservations();
    };
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [activeTab, loadExecutions, loadObservations]);

  useEffect(() => {
    if (activeTab === "knowledge" && !obsData && !obsLoading) {
      void loadObservations();
    }
  }, [activeTab, obsData, obsLoading, loadObservations]);

  const timelineItems = useMemo<TimelineItem[] | null>(() => {
    if (!execData && !obsData) return null;
    const items: TimelineItem[] = [];
    for (const e of execData?.entries ?? []) {
      items.push({ kind: "execution", ...e });
    }
    for (const n of obsData?.nodes ?? []) {
      items.push({
        kind: "observation",
        id: n.id,
        created_at: n.created_at,
        summary: n.summary,
        memory_type: n.memory_type,
        scope: n.scope,
      });
    }
    items.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    return items;
  }, [execData, obsData]);

  const TABS = [
    { id: "identity" as const, label: "Identity", Icon: Fingerprint },
    { id: "knowledge" as const, label: "Knowledge", Icon: Network },
    { id: "history" as const, label: "History", Icon: History },
  ];

  const handleTabClick = (id: "identity" | "knowledge" | "history") => {
    setActiveTab(id);
    if (id !== "knowledge") setHighlightObsId(null);
  };

  const handleNavigateToObs = (obsId: string) => {
    setHighlightObsId(obsId);
    setActiveTab("knowledge");
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-shrink-0 items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold sm:text-2xl">金乌记忆</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            金乌的{" "}
            <span className="font-medium text-[var(--brand)]">
              self-evolving memory system
            </span>{" "}
            — continuously learns and refines what it knows about you, your
            research taste, and lessons from past experiments.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            if (activeTab === "history") {
              loadExecutions();
              loadObservations();
            } else if (activeTab === "knowledge") loadObservations();
            else void loadListing();
          }}
          disabled={
            activeTab === "history"
              ? execLoading || obsLoading
              : activeTab === "knowledge"
              ? obsLoading
              : listingLoading
          }
          aria-label="Refresh"
          title="Refresh"
          className="mt-0.5 flex-shrink-0 rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <RefreshCw
            className={`size-4 ${
              (activeTab === "history" && (execLoading || obsLoading)) ||
              (activeTab === "knowledge" && obsLoading) ||
              (activeTab === "identity" && listingLoading)
                ? "animate-spin"
                : ""
            }`}
            aria-hidden="true"
          />
        </button>
      </header>

      <div
        className="grid flex-shrink-0 grid-cols-3 gap-1 border-b border-border px-2 pt-1 sm:flex sm:items-center sm:px-3"
        role="tablist"
        aria-label="Memory views"
      >
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            onClick={() => handleTabClick(id)}
            className={cn(
              "flex min-w-0 items-center justify-center gap-1 rounded-t-md border-b-2 px-1.5 py-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:gap-1.5 sm:px-3 sm:text-sm",
              activeTab === id
                ? "border-[var(--brand)] bg-accent text-foreground"
                : "border-transparent text-muted-foreground hover:bg-accent hover:text-foreground"
            )}
          >
            <Icon
              className="size-3.5"
              aria-hidden="true"
            />
            {label}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {activeTab === "identity" && (
          <IdentityTab
            listing={listing}
            listingLoading={listingLoading}
          />
        )}
        {activeTab === "knowledge" && (
          <ObservationGraph
            data={obsData}
            loading={obsLoading}
            error={obsError}
            highlightNodeId={highlightObsId}
          />
        )}
        {activeTab === "history" && (
          <div className="flex min-h-0 w-full flex-1 flex-col">
            <HistoryTab
              items={timelineItems}
              truncated={Boolean(execData?.truncated)}
              loading={execLoading || obsLoading}
              error={execError || obsError}
              highlightExecId={initialExecId}
              onRefresh={() => {
                loadExecutions();
                loadObservations();
              }}
              onNavigateToObs={handleNavigateToObs}
            />
          </div>
        )}
      </div>
    </div>
  );
}
