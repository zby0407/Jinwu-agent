"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  BrainCircuit,
  Database,
  Download,
  FileText,
  FlaskConical,
  Loader2,
  PackageOpen,
  RefreshCw,
  Table2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ArtifactKind, ResearchArtifact } from "@/app/api/artifacts/route";
import {
  WorkspaceFileDialog,
  workspaceFileUrl,
} from "@/app/components/WorkspaceFileDialog";

const POLL_INTERVAL_MS = 4000;

const GROUPS: Array<{
  kind: ArtifactKind;
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
}> = [
  { kind: "figure", label: "\u56fe\u8868", Icon: BarChart3 },
  { kind: "table", label: "\u6570\u636e\u8868", Icon: Table2 },
  { kind: "report", label: "\u62a5\u544a", Icon: FileText },
  { kind: "notebook", label: "Notebook", Icon: FlaskConical },
  { kind: "model", label: "\u6a21\u578b", Icon: BrainCircuit },
  { kind: "data", label: "\u6d3e\u751f\u6570\u636e", Icon: Database },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(mtime: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(mtime));
}

async function fetchArtifacts(): Promise<{
  artifacts: ResearchArtifact[];
  manifest: string;
  truncated: boolean;
}> {
  const response = await fetch("/api/artifacts", { cache: "no-store" });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      body?.error || "\u65e0\u6cd5\u52a0\u8f7d\u7814\u7a76\u4ea7\u7269\u3002"
    );
  }
  return {
    artifacts: (body?.artifacts ?? []) as ResearchArtifact[],
    manifest: body?.manifest ?? ".jinwu/artifacts.json",
    truncated: !!body?.truncated,
  };
}

export function ArtifactsPanel() {
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [manifest, setManifest] = useState(".jinwu/artifacts.json");
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ResearchArtifact | null>(null);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const next = await fetchArtifacts();
      setArtifacts(next.artifacts);
      setManifest(next.manifest);
      setTruncated(next.truncated);
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "\u65e0\u6cd5\u52a0\u8f7d\u7814\u7a76\u4ea7\u7269\u3002"
      );
    } finally {
      setLoading(false);
      if (!quiet) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh(true);
    const timer = window.setInterval(
      () => void refresh(true),
      POLL_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [refresh]);

  const grouped = useMemo(() => {
    const result = new Map<ArtifactKind, ResearchArtifact[]>();
    for (const artifact of artifacts) {
      const list = result.get(artifact.kind) ?? [];
      list.push(artifact);
      result.set(artifact.kind, list);
    }
    return result;
  }, [artifacts]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <PackageOpen className="size-4 text-[var(--brand)]" />
            {"\u7814\u7a76\u4ea7\u7269"}
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {artifacts.length}
            </span>
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {
              "\u81ea\u52a8\u6536\u96c6 Agent \u5728\u5de5\u4f5c\u533a\u751f\u6210\u7684\u56fe\u8868\u3001\u6570\u636e\u8868\u548c\u62a5\u544a"
            }
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh(false)}
          disabled={refreshing}
          className="gold-control inline-flex size-7 items-center justify-center rounded-md text-muted-foreground disabled:opacity-50"
          aria-label="\u5237\u65b0\u7814\u7a76\u4ea7\u7269"
          title="\u5237\u65b0"
        >
          <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-4 text-center text-xs text-destructive">
            {error}
          </div>
        ) : artifacts.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
            <PackageOpen className="size-8 text-muted-foreground/50" />
            <p className="text-sm font-medium text-foreground">
              {"\u6682\u65e0\u7814\u7a76\u4ea7\u7269"}
            </p>
            <p className="max-w-56 text-xs leading-relaxed text-muted-foreground">
              {
                "Agent \u751f\u6210\u56fe\u8868\u3001CSV\u3001\u5b9e\u9a8c\u62a5\u544a\u7b49\u6587\u4ef6\u540e\uff0c\u4f1a\u81ea\u52a8\u51fa\u73b0\u5728\u8fd9\u91cc\u5e76\u83b7\u5f97\u7a33\u5b9a\u7f16\u53f7\u3002"
              }
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {truncated && (
              <p className="rounded-md bg-muted px-2 py-1.5 text-[11px] text-muted-foreground">
                {
                  "\u5de5\u4f5c\u533a\u6587\u4ef6\u8f83\u591a\uff0c\u5f53\u524d\u53ea\u7d22\u5f15\u4e86\u524d "
                }
                {artifacts.length}
                {" \u4e2a\u4ea7\u7269\u3002"}
              </p>
            )}
            {GROUPS.map(({ kind, label, Icon }) => {
              const items = grouped.get(kind);
              if (!items?.length) return null;
              return (
                <section key={kind}>
                  <div className="mb-1.5 flex items-center gap-1.5 px-1 text-xs font-semibold text-muted-foreground">
                    <Icon className="size-3.5 text-[var(--brand)]" />
                    {label}
                    <span className="font-normal">{items.length}</span>
                  </div>
                  <div className="space-y-1.5">
                    {items.map((artifact) => (
                      <div
                        key={artifact.id}
                        className="group gold-row flex items-center gap-2 rounded-md border border-border bg-background/50 p-2"
                      >
                        <button
                          type="button"
                          onClick={() => setSelected(artifact)}
                          className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          title={`${artifact.label} - ${artifact.path}`}
                        >
                          <div className="flex items-center gap-2">
                            <span className="gold-badge shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold">
                              {artifact.label}
                            </span>
                            <span className="truncate text-xs font-medium text-foreground">
                              {artifact.name}
                            </span>
                          </div>
                          <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                            <span className="truncate">{artifact.path}</span>
                            <span aria-hidden="true">/</span>
                            <span className="shrink-0">
                              {formatBytes(artifact.size)}
                            </span>
                            <span aria-hidden="true">/</span>
                            <span className="shrink-0">
                              {formatTime(artifact.mtime)}
                            </span>
                          </div>
                        </button>
                        <a
                          href={workspaceFileUrl(artifact.path, true)}
                          download={artifact.name}
                          className="gold-control inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-70 group-hover:opacity-100"
                          aria-label={`\u4e0b\u8f7d ${artifact.label}`}
                          title="\u4e0b\u8f7d"
                        >
                          <Download className="size-3.5" />
                        </a>
                      </div>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-border pt-2 text-[10px] leading-relaxed text-muted-foreground">
        {"\u7f16\u53f7\u7d22\u5f15\uff1a"}
        <code className="text-foreground/80">{manifest}</code>
      </div>

      <WorkspaceFileDialog
        path={selected?.path ?? null}
        size={selected?.size}
        onClose={() => setSelected(null)}
        onChanged={() => void refresh(false)}
      />
    </div>
  );
}
