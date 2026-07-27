"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  Library,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { cn } from "@/lib/utils";
import {
  fetchKbBuiltInWiki,
  fetchKbEntry,
  type KbBuiltInCatalogEntry,
  type KbBuiltInTaskBundle,
  type KbBuiltInWiki,
  type KbEntryDetail,
} from "@/lib/knowledgeBase";

const MODULE_LABELS: Record<string, string> = {
  solar_cycle: "太阳活动周",
  dynamo_and_polar_field: "发电机与极区场",
  indicators_and_features: "观测指标与特征",
  active_regions_and_flares: "活动区与耀斑",
  data_sources: "数据来源",
  experiment_paradigms: "检验方法",
  hypothesis_templates: "假设模板",
  evidence_review: "证据审查",
  research_memory: "研究记忆",
};

const MODULE_ORDER = [
  "solar_cycle",
  "dynamo_and_polar_field",
  "indicators_and_features",
  "active_regions_and_flares",
  "data_sources",
  "experiment_paradigms",
  "hypothesis_templates",
  "evidence_review",
  "research_memory",
];

const TYPE_LABELS: Record<string, string> = {
  concept: "概念",
  mechanism: "机制",
  data_source: "数据",
  experiment_paradigm: "方法",
  hypothesis_template: "模板",
  finding: "发现",
  counterexample: "反例",
};

const FIELD_LABELS: Record<string, string> = {
  claim: "核心主张",
  mechanism: "作用机制",
  scope: "适用范围",
  observations: "观测依据",
  predictions: "可观测预测",
  falsifiers: "证伪条件",
  confounders: "混杂与替代解释",
  limitations: "局限",
  method: "检验方法",
  collection_method: "数据与使用边界",
  design: "检验设计",
  structure: "假设结构",
  procedure: "实施步骤",
  usage: "在假设阶段如何使用",
  evidence: "证据",
  caveats: "注意事项",
};

function Loading({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2
        className="size-4 animate-spin"
        aria-hidden="true"
      />
      {text}
    </div>
  );
}

function valueAsMarkdown(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        typeof item === "string"
          ? `- ${item}`
          : `- ${JSON.stringify(item, null, 2)}`
      )
      .join("\n");
  }
  if (value === null || value === undefined) return "";
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``;
}

function uniqueEntries(
  entries: KbBuiltInCatalogEntry[]
): KbBuiltInCatalogEntry[] {
  return Array.from(
    new Map(entries.map((entry) => [entry.id, entry])).values()
  );
}

function liveEntriesForTask(
  wiki: KbBuiltInWiki,
  taskId: string
): KbBuiltInCatalogEntry[] {
  if (taskId === "all") {
    return wiki.catalog_entries.filter((entry) => entry.live !== null);
  }
  const task = wiki.task_bundles.find((bundle) => bundle.id === taskId);
  return uniqueEntries(
    (task?.seed_entries ?? []).filter((entry) => entry.live !== null)
  );
}

function DirectoryEntry({
  entry,
  active,
  onSelect,
}: {
  entry: KbBuiltInCatalogEntry;
  active: boolean;
  onSelect: (entry: KbBuiltInCatalogEntry) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(entry)}
      className={cn(
        "group flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
      )}
    >
      <BookOpen
        className={cn(
          "size-3.5 shrink-0",
          active ? "text-[var(--brand)]" : "text-muted-foreground/70"
        )}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1 truncate text-sm">{entry.title_zh}</span>
      <ChevronRight
        className={cn(
          "size-3.5 shrink-0",
          active
            ? "text-foreground"
            : "text-transparent group-hover:text-muted-foreground"
        )}
        aria-hidden="true"
      />
    </button>
  );
}

function EntryArticle({
  entry,
  titleById,
  refreshKey,
  onSelectRelated,
}: {
  entry: KbBuiltInCatalogEntry;
  titleById: Map<string, string>;
  refreshKey: number;
  onSelectRelated: (id: string) => void;
}) {
  const [detail, setDetail] = useState<KbEntryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchKbEntry(entry.id)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) {
          setDetail(null);
          setError("这个条目暂时无法读取，请稍后再试。");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entry.id, refreshKey]);

  if (loading) return <Loading text="正在打开条目…" />;
  if (error)
    return <p className="py-8 text-sm text-[var(--color-error)]">{error}</p>;
  if (!detail) return null;

  const contentFields = Object.entries(detail.content).filter(
    ([, value]) => valueAsMarkdown(value).trim().length > 0
  );

  return (
    <article className="mx-auto w-full max-w-3xl pb-16">
      <div className="border-b border-border pb-6">
        <p className="text-sm text-muted-foreground">
          {MODULE_LABELS[entry.module] ?? entry.module}
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
          {entry.title_zh}
        </h1>
        <span className="mt-3 inline-flex rounded-full bg-accent px-2 py-0.5 text-xs text-muted-foreground">
          {TYPE_LABELS[entry.type] ?? entry.type}
        </span>
      </div>

      <div className="mt-7 space-y-8">
        {contentFields.map(([key, value]) => (
          <section key={key}>
            {key !== "definition" && (
              <h2 className="mb-3 text-lg font-semibold">
                {FIELD_LABELS[key] ?? key}
              </h2>
            )}
            <MarkdownContent
              content={valueAsMarkdown(value)}
              className="text-[15px] leading-7"
            />
          </section>
        ))}
      </div>

      <aside className="mt-10 rounded-lg border border-border bg-muted/20 p-4 text-sm">
        {detail.valid_range && (
          <div className="grid gap-1 sm:grid-cols-[5rem_1fr]">
            <span className="text-muted-foreground">适用范围</span>
            <span className="leading-6">{detail.valid_range}</span>
          </div>
        )}
        {detail.source_ref && (
          <div className="mt-3 grid gap-1 border-t border-border pt-3 sm:grid-cols-[5rem_1fr]">
            <span className="text-muted-foreground">参考来源</span>
            <span className="break-words leading-6">{detail.source_ref}</span>
          </div>
        )}
        {detail.related_entries.length > 0 && (
          <div className="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-[5rem_1fr]">
            <span className="text-muted-foreground">相关条目</span>
            <div className="flex flex-wrap gap-2">
              {detail.related_entries.map((related) => {
                const relatedTitle = titleById.get(related.id);
                return relatedTitle ? (
                  <button
                    key={related.id}
                    type="button"
                    onClick={() => onSelectRelated(related.id)}
                    className="rounded-md border border-border bg-background px-2 py-1 text-left text-xs transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {relatedTitle}
                  </button>
                ) : null;
              })}
            </div>
          </div>
        )}
      </aside>
    </article>
  );
}

export function KnowledgePanel({
  refreshSignal = 0,
}: {
  refreshSignal?: number;
}) {
  const [wiki, setWiki] = useState<KbBuiltInWiki | null>(null);
  const [selectedTask, setSelectedTask] = useState("all");
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchKbBuiltInWiki()
      .then((data) => {
        if (cancelled) return;
        setWiki(data);
        const liveEntries = data.catalog_entries.filter(
          (entry) => entry.live !== null
        );
        setSelectedEntryId((current) =>
          liveEntries.some((entry) => entry.id === current)
            ? current
            : liveEntries[0]?.id ?? null
        );
        if (!data.available) {
          setError(data.error || "太阳活动 Wiki 暂时不可用。");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("无法连接太阳活动 Wiki，请检查后端服务。");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, refreshSignal]);

  const taskBundles = useMemo(
    () =>
      (wiki?.task_bundles ?? []).filter((bundle) =>
        bundle.seed_entries.some((entry) => entry.live !== null)
      ),
    [wiki]
  );

  const taskEntries = useMemo(
    () => (wiki ? liveEntriesForTask(wiki, selectedTask) : []),
    [wiki, selectedTask]
  );

  const visibleEntries = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return taskEntries;
    return taskEntries.filter((entry) =>
      `${entry.title_zh} ${MODULE_LABELS[entry.module] ?? entry.module}`
        .toLocaleLowerCase()
        .includes(query)
    );
  }, [search, taskEntries]);

  const groupedEntries = useMemo(() => {
    const groups = new Map<string, KbBuiltInCatalogEntry[]>();
    for (const entry of visibleEntries) {
      const items = groups.get(entry.module) ?? [];
      items.push(entry);
      groups.set(entry.module, items);
    }
    return [...groups.entries()].sort(
      ([left], [right]) =>
        MODULE_ORDER.indexOf(left) - MODULE_ORDER.indexOf(right)
    );
  }, [visibleEntries]);

  const allLiveEntries = useMemo(
    () => wiki?.catalog_entries.filter((entry) => entry.live !== null) ?? [],
    [wiki]
  );
  const selectedEntry =
    allLiveEntries.find((entry) => entry.id === selectedEntryId) ?? null;
  const titleById = useMemo(
    () => new Map(allLiveEntries.map((entry) => [entry.id, entry.title_zh])),
    [allLiveEntries]
  );
  const activeTask =
    taskBundles.find((bundle) => bundle.id === selectedTask) ?? null;

  function selectTask(taskId: string) {
    if (!wiki) return;
    const entries = liveEntriesForTask(wiki, taskId);
    setSelectedTask(taskId);
    setSearch("");
    setSelectedEntryId(entries[0]?.id ?? null);
  }

  function selectRelated(id: string) {
    if (!titleById.has(id)) return;
    setSelectedEntryId(id);
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-background">
      <header className="shrink-0 border-b border-border">
        <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-[var(--brand)]">
              <Library
                className="size-4"
                aria-hidden="true"
              />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold">
                太阳活动研究 Wiki
              </h1>
              <p className="truncate text-xs text-muted-foreground">
                太阳活动周、极区场、活动代理与耀斑
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setRefreshKey((key) => key + 1)}
            aria-label="刷新 Wiki"
            title="刷新"
            className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw
              className="size-4"
              aria-hidden="true"
            />
          </button>
        </div>

        {!loading && wiki?.available && (
          <div className="flex flex-col gap-2 border-t border-border/70 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
            <label
              htmlFor="wiki-task"
              className="shrink-0 text-xs font-medium text-muted-foreground"
            >
              按研究任务浏览
            </label>
            <select
              id="wiki-task"
              value={selectedTask}
              onChange={(event) => selectTask(event.target.value)}
              className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring sm:w-56"
            >
              <option value="all">全部知识</option>
              {taskBundles.map((task: KbBuiltInTaskBundle) => (
                <option
                  key={task.id}
                  value={task.id}
                >
                  {task.title_zh}
                </option>
              ))}
            </select>
            <p className="min-w-0 truncate text-xs text-muted-foreground">
              {activeTask?.purpose_zh ??
                "浏览当前 Wiki 中已经整理完成的知识条目。"}
            </p>
          </div>
        )}
      </header>

      {loading ? (
        <div className="px-6">
          <Loading text="正在打开太阳活动 Wiki…" />
        </div>
      ) : error ? (
        <p className="px-6 py-8 text-sm text-[var(--color-error)]">{error}</p>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <aside className="flex w-full shrink-0 flex-col border-b border-border bg-muted/10 lg:w-72 lg:border-b-0 lg:border-r">
            <div className="border-b border-border p-3">
              <div className="relative">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden="true"
                />
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索目录"
                  aria-label="搜索 Wiki 目录"
                  className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>

            <nav
              aria-label="Wiki 目录"
              className="max-h-64 min-h-0 overflow-y-auto p-2 lg:max-h-none lg:flex-1"
            >
              {groupedEntries.map(([module, entries]) => (
                <section
                  key={module}
                  className="mb-4 last:mb-0"
                >
                  <h2 className="px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {MODULE_LABELS[module] ?? module}
                  </h2>
                  <div className="space-y-0.5">
                    {entries.map((entry) => (
                      <DirectoryEntry
                        key={entry.id}
                        entry={entry}
                        active={entry.id === selectedEntryId}
                        onSelect={(nextEntry) =>
                          setSelectedEntryId(nextEntry.id)
                        }
                      />
                    ))}
                  </div>
                </section>
              ))}
              {visibleEntries.length === 0 && (
                <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                  没有匹配的条目
                </p>
              )}
            </nav>
          </aside>

          <main className="min-h-0 flex-1 overflow-y-auto px-5 py-7 sm:px-8 lg:px-12 lg:py-10">
            {selectedEntry ? (
              <EntryArticle
                entry={selectedEntry}
                titleById={titleById}
                refreshKey={refreshKey}
                onSelectRelated={selectRelated}
              />
            ) : (
              <div className="mx-auto max-w-xl py-16 text-center">
                <BookOpen className="mx-auto size-8 text-muted-foreground/60" />
                <p className="mt-3 text-sm text-muted-foreground">
                  从目录中选择一个条目开始阅读。
                </p>
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
