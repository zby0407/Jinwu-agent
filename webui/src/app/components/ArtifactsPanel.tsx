"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BarChart3,
  Clipboard,
  Code2,
  Database,
  Download,
  ExternalLink,
  FileArchive,
  FileText,
  FolderSearch,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useQueryState } from "nuqs";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type { ArtifactCandidate, ArtifactCategory } from "@/lib/artifacts";
import { useResearchRunActive } from "@/lib/researchRunStatus";
import {
  WorkspaceFileDialog,
  workspaceFileUrl,
} from "@/app/components/WorkspaceFileDialog";

const GROUPS: Array<{
  key: ArtifactCategory;
  label: string;
  Icon: typeof FileText;
}> = [
  { key: "documents", label: "报告与文档", Icon: FileText },
  { key: "figures", label: "图表", Icon: BarChart3 },
  { key: "data", label: "数据", Icon: Database },
  { key: "code", label: "代码", Icon: Code2 },
  { key: "other", label: "其他", Icon: FileArchive },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function ArtifactsPanel() {
  const [threadId] = useQueryState("threadId");
  const [, setInspectorTab] = useQueryState("inspectorTab");
  const [, setWorkspacePath] = useQueryState("workspacePath");
  const isRunning = useResearchRunActive();
  const wasRunning = useRef(isRunning);
  const requestId = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactCandidate[]>([]);
  const [selected, setSelected] = useState<ArtifactCandidate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  const load = useCallback(
    async (showSpinner = false) => {
      if (!threadId) {
        setArtifacts([]);
        setError(null);
        setLoading(false);
        return;
      }
      const id = ++requestId.current;
      const controller = new AbortController();
      activeRequest.current?.abort();
      activeRequest.current = controller;
      if (showSpinner) setLoading(true);
      try {
        const response = await fetch(
          `/api/workspace/artifacts?${new URLSearchParams({ threadId })}`,
          { signal: controller.signal }
        );
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error(body?.error || "无法加载产物。");
        if (id !== requestId.current || controller.signal.aborted) return;
        setArtifacts((body?.artifacts ?? []) as ArtifactCandidate[]);
        setTruncated(Boolean(body?.truncated));
        setError(null);
      } catch (reason) {
        if (id !== requestId.current || controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "无法加载产物。");
      } finally {
        if (id === requestId.current && !controller.signal.aborted) {
          setLoading(false);
        }
      }
    },
    [threadId]
  );

  useEffect(() => {
    setArtifacts([]);
    setSelected(null);
    setError(null);
    void load(true);
    return () => {
      activeRequest.current?.abort();
      requestId.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (!isRunning) return;
    const timer = window.setInterval(() => void load(false), 5000);
    return () => window.clearInterval(timer);
  }, [isRunning, load]);

  useEffect(() => {
    if (wasRunning.current && !isRunning) void load(false);
    wasRunning.current = isRunning;
  }, [isRunning, load]);

  const grouped = useMemo(() => {
    const result = new Map<ArtifactCategory, ArtifactCandidate[]>();
    for (const artifact of artifacts) {
      const list = result.get(artifact.category) ?? [];
      list.push(artifact);
      result.set(artifact.category, list);
    }
    return result;
  }, [artifacts]);

  const copyPath = async (path: string) => {
    try {
      await navigator.clipboard.writeText(`/${path}`);
      toast.success("已复制产物路径");
    } catch {
      toast.error("无法复制路径");
    }
  };

  const locateInWorkspace = async (path: string) => {
    await setWorkspacePath(path);
    await setInspectorTab("workspace");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">
            当前会话产物
          </h2>
          <p className="text-[11px] text-muted-foreground">
            {isRunning
              ? "实验运行中，每 5 秒自动更新"
              : `${artifacts.length} 项可交付成果`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading || !threadId}
          className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
          aria-label="刷新产物"
          title="刷新产物"
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading && artifacts.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="size-5 animate-spin text-[var(--brand)]" />
          </div>
        ) : error ? (
          <div className="space-y-3 py-10 text-center">
            <p className="text-xs text-destructive">{error}</p>
            <button
              type="button"
              onClick={() => void load(true)}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted"
            >
              重试
            </button>
          </div>
        ) : artifacts.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-center text-muted-foreground">
            <FileArchive className="text-[var(--brand)]/70 size-8" />
            <p className="text-sm">暂无产物</p>
          </div>
        ) : (
          <div className="space-y-4">
            {truncated && (
              <p className="rounded-md border border-border bg-muted/40 px-2 py-1.5 text-[11px] text-muted-foreground">
                产物数量超过显示上限，仅展示最近扫描到的部分文件。
              </p>
            )}
            {GROUPS.map(({ key, label, Icon }) => {
              const items = grouped.get(key);
              if (!items?.length) return null;
              return (
                <section
                  key={key}
                  aria-labelledby={`artifact-${key}`}
                >
                  <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-tertiary">
                    <Icon className="size-3.5 text-[var(--brand)]" />
                    <h3 id={`artifact-${key}`}>{label}</h3>
                    <span className="text-muted-foreground">
                      {items.length}
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {items.map((artifact) => {
                      const directory = artifact.path.includes("/")
                        ? artifact.path.slice(0, artifact.path.lastIndexOf("/"))
                        : "";
                      return (
                        <article
                          key={artifact.path}
                          className="hover:border-[var(--brand)]/50 group rounded-lg border border-border/80 bg-background/60 p-2 transition-colors hover:bg-muted/40"
                        >
                          <button
                            type="button"
                            onClick={() => setSelected(artifact)}
                            className="w-full text-left"
                            title={`预览 ${artifact.path}`}
                          >
                            <p className="truncate text-sm font-medium text-foreground">
                              {artifact.name}
                            </p>
                            <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                              {directory}
                            </p>
                            <p className="mt-1 text-[10px] text-muted-foreground">
                              {formatBytes(artifact.size)} ·{" "}
                              {new Date(artifact.mtime).toLocaleString("zh-CN")}
                            </p>
                          </button>
                          <div className="mt-1.5 flex items-center gap-0.5 border-t border-border/60 pt-1">
                            <button
                              type="button"
                              onClick={() => setSelected(artifact)}
                              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                              title="预览"
                            >
                              <ExternalLink className="size-3.5" />
                            </button>
                            <a
                              href={workspaceFileUrl(
                                artifact.path,
                                true,
                                threadId
                              )}
                              download={artifact.name}
                              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                              title="下载"
                            >
                              <Download className="size-3.5" />
                            </a>
                            <button
                              type="button"
                              onClick={() => void copyPath(artifact.path)}
                              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                              title="复制路径"
                            >
                              <Clipboard className="size-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                void locateInWorkspace(artifact.path)
                              }
                              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                              title="在工作区定位"
                            >
                              <FolderSearch className="size-3.5" />
                            </button>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>

      <WorkspaceFileDialog
        path={selected?.path ?? null}
        size={selected?.size}
        readOnly
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
