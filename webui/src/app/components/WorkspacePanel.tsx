"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FileText,
  RefreshCw,
  Download,
  Loader2,
  Image as ImageIcon,
  Database,
  Code2,
  File as FileIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { WorkspaceFileDialog } from "@/app/components/WorkspaceFileDialog";
import type { WorkspaceEntry } from "@/app/api/workspace/route";
import { useQueryState } from "nuqs";

async function listDir(
  path: string,
  threadId: string
): Promise<WorkspaceEntry[]> {
  const res = await fetch(
    `/api/workspace?${new URLSearchParams({ path, threadId })}`
  );
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error || "无法列出工作区内容。");
  return (body?.entries ?? []) as WorkspaceEntry[];
}

async function listAll(threadId: string): Promise<{
  entries: WorkspaceEntry[];
  truncated: boolean;
}> {
  const res = await fetch(
    `/api/workspace?${new URLSearchParams({ recursive: "1", threadId })}`
  );
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error || "无法加载工作区。");
  return {
    entries: (body?.entries ?? []) as WorkspaceEntry[],
    truncated: !!body?.truncated,
  };
}

// Research-artifact categories for the "by type" view. Order here is render order.
const CATEGORIES = [
  {
    key: "docs",
    label: "论文与文档",
    Icon: FileText,
    exts: [
      "pdf",
      "tex",
      "bib",
      "md",
      "markdown",
      "txt",
      "docx",
      "doc",
      "rtf",
      "odt",
    ],
  },
  {
    key: "figures",
    label: "图表",
    Icon: ImageIcon,
    exts: [
      "png",
      "jpg",
      "jpeg",
      "gif",
      "svg",
      "webp",
      "bmp",
      "tiff",
      "tif",
      "eps",
    ],
  },
  {
    key: "data",
    label: "数据",
    Icon: Database,
    exts: [
      "json",
      "jsonl",
      "csv",
      "tsv",
      "xlsx",
      "xls",
      "parquet",
      "pkl",
      "npy",
      "npz",
      "h5",
      "hdf5",
      "db",
      "sqlite",
      "yaml",
      "yml",
      "xml",
    ],
  },
  {
    key: "code",
    label: "代码",
    Icon: Code2,
    exts: [
      "py",
      "ipynb",
      "js",
      "ts",
      "tsx",
      "jsx",
      "sh",
      "bash",
      "r",
      "jl",
      "cpp",
      "cc",
      "c",
      "h",
      "hpp",
      "java",
      "go",
      "rs",
      "m",
      "rb",
    ],
  },
] as const;
const OTHER = { key: "other", label: "其他", Icon: FileIcon } as const;

const EXT_TO_CATEGORY: Record<string, string> = {};
for (const cat of CATEGORIES) {
  for (const ext of cat.exts) EXT_TO_CATEGORY[ext] = cat.key;
}

type ViewMode = "tree" | "type";

export function WorkspacePanel() {
  const [threadId] = useQueryState("threadId");
  const [workspacePath, setWorkspacePath] = useQueryState("workspacePath");
  const [view, setView] = useState<ViewMode>("tree");

  // --- Tree view state (listing cache keyed by dir path; "" = root) ---
  const [children, setChildren] = useState<Record<string, WorkspaceEntry[]>>(
    {}
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [rootLoading, setRootLoading] = useState(false);

  // --- By-type view state (flat recursive listing) ---
  const [allFiles, setAllFiles] = useState<WorkspaceEntry[] | null>(null);
  const [typeLoading, setTypeLoading] = useState(false);
  const [truncated, setTruncated] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{
    path: string;
    size: number;
  } | null>(null);
  const [highlightedPath, setHighlightedPath] = useState<string | null>(null);

  const loadDir = useCallback(
    async (path: string) => {
      if (!threadId) throw new Error("请先开始或打开一个研究会话。");
      setLoading((prev) => new Set(prev).add(path));
      try {
        const entries = await listDir(path, threadId);
        setChildren((prev) => ({ ...prev, [path]: entries }));
        if (path === "") setError(null);
        return entries;
      } catch (err) {
        if (path === "") {
          setError(err instanceof Error ? err.message : "加载失败。");
        }
        throw err;
      } finally {
        setLoading((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
      }
    },
    [threadId]
  );

  const loadAll = useCallback(async () => {
    if (!threadId) {
      setError("请先开始或打开一个研究会话。");
      return;
    }
    setTypeLoading(true);
    try {
      const { entries, truncated } = await listAll(threadId);
      setAllFiles(entries);
      setTruncated(truncated);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败。");
    } finally {
      setTypeLoading(false);
    }
  }, [threadId]);

  // Initial tree load. loadDir surfaces root failures via `error`; catch the
  // rejection here so it doesn't become an unhandled promise rejection.
  useEffect(() => {
    setChildren({});
    setExpanded(new Set());
    setAllFiles(null);
    setSelected(null);
    if (!threadId) {
      setError("请先开始或打开一个研究会话。");
      setRootLoading(false);
      return;
    }
    setRootLoading(true);
    void loadDir("")
      .catch(() => {})
      .finally(() => setRootLoading(false));
  }, [threadId, loadDir]);

  // Load the flat listing the first time the by-type view is opened.
  useEffect(() => {
    if (view === "type" && allFiles === null && !typeLoading) void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // The artifacts panel can deep-link to a file in the workspace. Switch to
  // tree view, load and expand every ancestor, then open the existing viewer.
  useEffect(() => {
    if (!workspacePath || !threadId) return;
    let cancelled = false;
    const reveal = async () => {
      const normalized = workspacePath
        .replaceAll("\\", "/")
        .replace(/^\/+/, "");
      const parts = normalized.split("/").filter(Boolean);
      if (parts.length === 0) return;
      const ancestors = parts
        .slice(0, -1)
        .map((_, index) => parts.slice(0, index + 1).join("/"));
      setView("tree");
      setExpanded(new Set(ancestors));
      await Promise.allSettled(["", ...ancestors].map((path) => loadDir(path)));
      if (cancelled) return;
      const parent = ancestors.at(-1) ?? "";
      const entry = (await listDir(parent, threadId)).find(
        (candidate) =>
          candidate.path === normalized && candidate.type === "file"
      );
      if (entry) {
        setHighlightedPath(entry.path);
        window.setTimeout(() => {
          document
            .querySelector<HTMLElement>(
              `[data-workspace-path="${CSS.escape(entry.path)}"]`
            )
            ?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 50);
        window.setTimeout(() => setHighlightedPath(null), 2200);
      }
      await setWorkspacePath(null);
    };
    void reveal();
    return () => {
      cancelled = true;
    };
  }, [workspacePath, threadId, loadDir, setWorkspacePath]);

  const refresh = useCallback(async () => {
    setError(null);
    if (view === "type") {
      await loadAll();
      return;
    }
    setRootLoading(true);
    // Re-fetch the root plus every currently-expanded dir so an open tree stays
    // open and in sync with what the agent has written since.
    await Promise.allSettled(["", ...expanded].map((p) => loadDir(p)));
    setRootLoading(false);
  }, [view, expanded, loadDir, loadAll]);

  const toggleDir = useCallback(
    (path: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
          if (!children[path]) void loadDir(path).catch(() => {});
        }
        return next;
      });
    },
    [children, loadDir]
  );

  // Group the flat listing into categories (newest first within each group).
  const grouped = useMemo(() => {
    const map: Record<string, WorkspaceEntry[]> = {};
    for (const f of allFiles ?? []) {
      const key = EXT_TO_CATEGORY[f.ext] ?? OTHER.key;
      (map[key] ??= []).push(f);
    }
    for (const list of Object.values(map)) {
      list.sort((a, b) => b.mtime - a.mtime);
    }
    return map;
  }, [allFiles]);

  const refreshing = view === "type" ? typeLoading : rootLoading;

  const renderEntries = (path: string, depth: number): React.ReactNode => {
    const entries = children[path];
    if (!entries) return null;
    if (entries.length === 0 && depth === 0) {
      return (
        <p className="px-2 py-6 text-center text-xs text-muted-foreground">
          工作区中暂无文件
        </p>
      );
    }
    return entries.map((entry) => {
      const isOpen = expanded.has(entry.path);
      const isLoadingDir = loading.has(entry.path);
      return (
        <div key={entry.path}>
          <button
            type="button"
            data-workspace-path={entry.path}
            onClick={() =>
              entry.type === "dir"
                ? toggleDir(entry.path)
                : setSelected({ path: entry.path, size: entry.size })
            }
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-sm text-foreground transition-colors hover:bg-muted",
              highlightedPath === entry.path &&
                "bg-[var(--brand)]/15 ring-[var(--brand)]/70 ring-1 ring-inset"
            )}
            style={{ paddingLeft: `${depth * 14 + 4}px` }}
            title={entry.name}
          >
            {entry.type === "dir" ? (
              <>
                <span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
                  {isLoadingDir ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : isOpen ? (
                    <ChevronDown className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                </span>
                <Folder className="size-4 shrink-0 text-[var(--brand)]" />
              </>
            ) : (
              <>
                <span className="size-4 shrink-0" />
                <FileText className="size-4 shrink-0 text-muted-foreground" />
              </>
            )}
            <span className="truncate">{entry.name}</span>
          </button>
          {entry.type === "dir" &&
            isOpen &&
            renderEntries(entry.path, depth + 1)}
        </div>
      );
    });
  };

  const renderByType = (): React.ReactNode => {
    if (!allFiles) return null;
    if (allFiles.length === 0) {
      return (
        <p className="px-2 py-6 text-center text-xs text-muted-foreground">
          工作区中暂无文件
        </p>
      );
    }
    const groups = [...CATEGORIES, OTHER];
    return (
      <div className="space-y-3">
        {truncated && (
          <p className="px-1 text-[11px] text-muted-foreground">
            仅显示前若干文件，工作区内容已超出显示上限。
          </p>
        )}
        {groups.map((cat) => {
          const files = grouped[cat.key];
          if (!files || files.length === 0) return null;
          const Icon = cat.Icon;
          return (
            <div key={cat.key}>
              <div className="mb-0.5 flex items-center gap-1.5 px-1 text-[10px] font-semibold uppercase tracking-wider text-tertiary">
                <Icon className="size-3.5" />
                {cat.label}
                <span className="text-muted-foreground">({files.length})</span>
              </div>
              {files.map((f) => {
                const dir = f.path.includes("/")
                  ? f.path.slice(0, f.path.lastIndexOf("/"))
                  : "";
                return (
                  <button
                    key={f.path}
                    type="button"
                    onClick={() => setSelected({ path: f.path, size: f.size })}
                    className="flex w-full items-center gap-1.5 rounded-md px-1 py-1 text-left text-sm text-foreground transition-colors hover:bg-muted"
                    title={f.path}
                  >
                    <Icon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{f.name}</span>
                    {dir && (
                      <span className="ml-auto shrink-0 truncate pl-2 text-[11px] text-muted-foreground">
                        {dir}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 pb-1.5">
        {/* Tree / By-type toggle */}
        <div className="flex items-center gap-0.5 rounded-md bg-muted p-0.5 text-xs">
          {(["tree", "type"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setView(m)}
              className={cn(
                "rounded px-2 py-0.5 font-medium transition-colors",
                view === m
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              aria-pressed={view === m}
            >
              {m === "tree" ? "目录树" : "按类型"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-0.5">
          <a
            href={
              threadId
                ? `/api/workspace/download?${new URLSearchParams({ threadId })}`
                : "#"
            }
            download
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="将整个工作区下载为 ZIP"
          >
            <Download className="size-3.5" />
            全部
          </a>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            aria-label="刷新工作区"
            title="刷新"
          >
            <RefreshCw
              className={cn("size-3.5", refreshing && "animate-spin")}
            />
          </button>
        </div>
      </div>

      {error ? (
        <p className="px-2 py-6 text-center text-xs text-muted-foreground">
          {error}
        </p>
      ) : view === "tree" ? (
        rootLoading && !children[""] ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="-mx-1">{renderEntries("", 0)}</div>
        )
      ) : typeLoading && allFiles === null ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        renderByType()
      )}

      <WorkspaceFileDialog
        path={selected?.path ?? null}
        size={selected?.size}
        onClose={() => setSelected(null)}
        onChanged={refresh}
      />
    </div>
  );
}
