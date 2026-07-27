"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  ExternalLink,
  FileSearch,
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
  fetchKbLiteratureDeltas,
  fetchKbLiteratureFeeds,
  fetchKbSources,
  fetchKbWikiPatches,
  type KbBuiltInCatalogEntry,
  type KbBuiltInTaskBundle,
  type KbBuiltInWiki,
  type KbEntryDetail,
  type KbLiteratureDelta,
  type KbLiteratureFeed,
  type KbSourceSummary,
  type KbWikiCandidatePatch,
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

const DELTA_LABELS: Record<string, string> = {
  new_source: "新增来源",
  new_version: "新版本",
  metadata_updated: "元数据更新",
  source_retracted: "撤稿复核",
  feed_discovered: "订阅新命中",
  feed_removed: "订阅移除",
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

function ResearchSources({
  feed,
  sources,
  deltas,
  pendingPatches,
  loading,
  error,
}: {
  feed: KbLiteratureFeed | null;
  sources: KbSourceSummary[];
  deltas: KbLiteratureDelta[];
  pendingPatches: KbWikiCandidatePatch[];
  loading: boolean;
  error: string | null;
}) {
  if (loading) return <Loading text="正在读取研究来源…" />;
  if (error)
    return <p className="py-8 text-sm text-[var(--color-error)]">{error}</p>;

  const seenTitles = new Set<string>();
  const visibleSources = [...sources]
    .sort((left, right) => {
      const leftDate = left.publication_date || `${left.year || 0}`;
      const rightDate = right.publication_date || `${right.year || 0}`;
      return rightDate.localeCompare(leftDate);
    })
    .filter((source) => {
      const titleKey = source.title
        .normalize("NFKC")
        .toLocaleLowerCase()
        .replace(/[^\p{L}\p{N}]+/gu, "");
      if (!titleKey || seenTitles.has(titleKey)) return false;
      seenTitles.add(titleKey);
      return true;
    });

  return (
    <div className="mx-auto w-full max-w-4xl pb-16">
      <div className="border-b border-border pb-6">
        <p className="text-sm text-muted-foreground">动态来源候选层</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
          {feed?.title_zh ?? "最新研究来源"}
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
          这里只展示已通过主题门禁并按文献族去重的来源。论文命中不等于 Wiki
          已采纳；它仍需绑定具体研究问题、逐字证据蒸馏和审核。
        </p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-accent px-2.5 py-1 text-muted-foreground">
            近 {feed?.lookback_years ?? "—"} 年
          </span>
          <span className="rounded-full bg-accent px-2.5 py-1 text-muted-foreground">
            {feed?.providers.join(" · ") || "尚无来源配置"}
          </span>
          <span className="rounded-full bg-accent px-2.5 py-1 text-muted-foreground">
            当前 {visibleSources.length} 篇候选论文
          </span>
          <span className="rounded-full bg-accent px-2.5 py-1 text-muted-foreground">
            本期变化 {deltas.length}
          </span>
          <span
            className={cn(
              "rounded-full px-2.5 py-1",
              pendingPatches.length
                ? "bg-[var(--color-warning)]/10 text-[var(--color-warning)]"
                : "bg-accent text-muted-foreground"
            )}
          >
            待审 Wiki 补丁 {pendingPatches.length}
          </span>
          {feed?.latest_run ? (
            <span
              className={cn(
                "rounded-full px-2.5 py-1",
                feed.latest_run.status === "ok"
                  ? "bg-[var(--color-success)]/10 text-[var(--color-success)]"
                  : "bg-[var(--color-warning)]/10 text-[var(--color-warning)]"
              )}
            >
              上次同步 {feed.latest_run.status} · {feed.latest_run.result_count}{" "}
              个候选命中
            </span>
          ) : (
            <span className="rounded-full bg-muted px-2.5 py-1 text-muted-foreground">
              尚未同步
            </span>
          )}
        </div>
      </div>

      {deltas.length > 0 && (
        <section className="mt-6 rounded-lg border border-border bg-muted/15 p-4">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="text-sm font-semibold">增量收件箱</h2>
            <span className="text-xs text-muted-foreground">
              变化只触发评估，不会自动改写 Wiki
            </span>
          </div>
          <div className="mt-3 space-y-2">
            {deltas.slice(0, 5).map((delta) => (
              <div
                key={delta.event_key}
                className="flex items-start justify-between gap-4 rounded-md bg-background px-3 py-2 text-xs"
              >
                <div className="min-w-0">
                  <span
                    className={cn(
                      "mr-2 inline-flex rounded px-1.5 py-0.5",
                      delta.event_type === "source_retracted"
                        ? "bg-[var(--color-error)]/10 text-[var(--color-error)]"
                        : "bg-accent text-muted-foreground"
                    )}
                  >
                    {DELTA_LABELS[delta.event_type] ?? delta.event_type}
                  </span>
                  <span className="text-foreground">
                    {String(delta.payload.title || delta.source_id)}
                  </span>
                </div>
                <time className="shrink-0 text-muted-foreground">
                  {delta.detected_at.slice(0, 10)}
                </time>
              </div>
            ))}
          </div>
        </section>
      )}

      {visibleSources.length === 0 ? (
        <div className="py-16 text-center">
          <FileSearch className="mx-auto size-8 text-muted-foreground/60" />
          <p className="mt-3 text-sm text-muted-foreground">
            这个主题还没有已同步的来源候选。
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            维护流程可调用 lit_feed_sync({feed?.id ?? "feed_id"})。
          </p>
        </div>
      ) : (
        <div className="mt-6 space-y-3">
          {visibleSources.map((source) => (
            <article
              key={source.source_id}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium leading-6 text-foreground underline-offset-4 hover:underline"
                  >
                    {source.title || source.source_id}
                  </a>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {source.authors.slice(0, 4).join(" · ") || "作者信息缺失"}
                  </p>
                </div>
                <ExternalLink
                  className="mt-1 size-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                <span className="rounded bg-accent px-2 py-0.5">
                  {source.provider}
                </span>
                <span className="rounded bg-accent px-2 py-0.5">
                  {source.publication_date || source.year || "日期未知"}
                </span>
                {source.is_refereed && (
                  <span className="bg-[var(--color-success)]/10 rounded px-2 py-0.5 text-[var(--color-success)]">
                    同行评审
                  </span>
                )}
                {source.is_retracted && (
                  <span className="bg-[var(--color-error)]/10 rounded px-2 py-0.5 text-[var(--color-error)]">
                    撤稿风险
                  </span>
                )}
                <span className="rounded bg-accent px-2 py-0.5">
                  {source.stage === "distilled"
                    ? "已蒸馏候选"
                    : source.stage === "fetched"
                    ? "已抓取"
                    : "仅缓存"}
                </span>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

export function KnowledgePanel({
  refreshSignal = 0,
}: {
  refreshSignal?: number;
}) {
  const [wiki, setWiki] = useState<KbBuiltInWiki | null>(null);
  const [viewMode, setViewMode] = useState<"wiki" | "sources">("wiki");
  const [selectedTask, setSelectedTask] = useState("all");
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feeds, setFeeds] = useState<KbLiteratureFeed[]>([]);
  const [literatureTotal, setLiteratureTotal] = useState(0);
  const [selectedFeedId, setSelectedFeedId] = useState("");
  const [sources, setSources] = useState<KbSourceSummary[]>([]);
  const [deltas, setDeltas] = useState<KbLiteratureDelta[]>([]);
  const [pendingPatches, setPendingPatches] = useState<
    KbWikiCandidatePatch[]
  >([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

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

  useEffect(() => {
    let cancelled = false;
    fetchKbLiteratureFeeds()
      .then((catalog) => {
        if (cancelled) return;
        setFeeds(catalog.feeds);
        setLiteratureTotal(catalog.total_sources || 0);
        setSelectedFeedId((current) =>
          catalog.feeds.some((feed) => feed.id === current)
            ? current
            : catalog.feeds[0]?.id ?? ""
        );
        if (catalog.status !== "ok") {
          setSourcesError(catalog.diagnostic || "研究来源订阅暂时不可用。");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFeeds([]);
          setLiteratureTotal(0);
          setSourcesError("无法读取研究来源订阅。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, refreshSignal]);

  useEffect(() => {
    if (viewMode !== "sources" || !selectedFeedId) return;
    let cancelled = false;
    setSourcesLoading(true);
    setSourcesError(null);
    Promise.all([
      fetchKbSources({ feed_id: selectedFeedId, limit: 100 }),
      fetchKbLiteratureDeltas({
        feed_id: selectedFeedId,
        include_baseline: false,
        limit: 20,
      }),
      fetchKbWikiPatches({ status: "pending", limit: 100 }),
    ])
      .then(([rows, deltaRows, patchRows]) => {
        if (!cancelled) {
          setSources(rows);
          setDeltas(deltaRows);
          setPendingPatches(patchRows);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSources([]);
          setDeltas([]);
          setPendingPatches([]);
          setSourcesError("无法读取这个主题的来源候选。");
        }
      })
      .finally(() => {
        if (!cancelled) setSourcesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedFeedId, viewMode, refreshKey, refreshSignal]);

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
  const activeFeed = feeds.find((feed) => feed.id === selectedFeedId) ?? null;

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

        <div
          role="tablist"
          aria-label="Wiki 内容视图"
          className="flex gap-1 border-t border-border/70 px-4 pt-2 sm:px-5"
        >
          {[
            ["wiki", "内置知识"],
            [
              "sources",
              literatureTotal ? `研究来源（${literatureTotal}）` : "研究来源",
            ],
          ].map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={viewMode === mode}
              onClick={() => setViewMode(mode as "wiki" | "sources")}
              className={cn(
                "rounded-t-md px-3 py-2 text-sm transition-colors",
                viewMode === mode
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {viewMode === "wiki" && !loading && wiki?.available && (
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

        {viewMode === "sources" && (
          <div className="flex flex-col gap-2 border-t border-border/70 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
            <label
              htmlFor="wiki-feed"
              className="shrink-0 text-xs font-medium text-muted-foreground"
            >
              按研究主题浏览
            </label>
            <select
              id="wiki-feed"
              value={selectedFeedId}
              onChange={(event) => setSelectedFeedId(event.target.value)}
              className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring sm:w-64"
            >
              {feeds.map((feed) => (
                <option
                  key={feed.id}
                  value={feed.id}
                >
                  {feed.title_zh}（{feed.source_count}）
                </option>
              ))}
            </select>
            <p className="min-w-0 truncate text-xs text-muted-foreground">
              {activeFeed?.query ?? "尚无研究订阅。"}
            </p>
          </div>
        )}
      </header>

      {viewMode === "sources" ? (
        <main className="min-h-0 flex-1 overflow-y-auto px-5 py-7 sm:px-8 lg:px-12 lg:py-10">
          <ResearchSources
            feed={activeFeed}
            sources={sources}
            deltas={deltas}
            pendingPatches={pendingPatches}
            loading={sourcesLoading}
            error={sourcesError}
          />
        </main>
      ) : loading ? (
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
