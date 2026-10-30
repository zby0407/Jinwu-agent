"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Database,
  ExternalLink,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleSwitch } from "@/components/ui/switch";
import {
  fetchDataSources,
  setDataSourceEnabled,
  type ObservationalDataSource,
} from "@/lib/dataSources";
import {
  createLiteratureFeed,
  fetchKbLiteratureFeeds,
  setLiteratureFeedEnabled,
  type KbLiteratureFeed,
} from "@/lib/knowledgeBase";
import { DatasetPreview, FeedPreview } from "@/app/components/SourcePreview";

const PROVIDER_OPTIONS = ["ads", "arxiv", "openalex", "crossref"] as const;

type Selection = { kind: "dataset" | "feed"; id: string };

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${value} B`;
}

function MetaGrid({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([key, value]) => (
        <div
          key={key}
          className="contents"
        >
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="min-w-0 break-words text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "muted";
}) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2 py-0.5 text-[11px] leading-4",
        tone === "success" &&
          "bg-[var(--color-success)]/10 text-[var(--color-success)]",
        tone === "warning" &&
          "bg-[var(--color-warning)]/10 text-[var(--color-warning)]",
        tone === "muted" && "bg-muted text-muted-foreground",
        tone === "neutral" && "bg-accent text-muted-foreground"
      )}
    >
      {children}
    </span>
  );
}

/** One selectable source card in the left-hand navigation column. */
function NavItem({
  title,
  subtitle,
  statusTone,
  statusText,
  active,
  enabled,
  busy,
  toggleLabel,
  onSelect,
  onToggle,
}: {
  title: string;
  subtitle: string;
  statusTone: "success" | "warning" | "muted";
  statusText: string;
  active: boolean;
  enabled: boolean;
  busy: boolean;
  toggleLabel: string;
  onSelect: () => void;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-2.5 py-2 transition-colors",
        active
          ? "border-[var(--brand)]/50 bg-[var(--brand)]/[0.06]"
          : "border-transparent hover:border-border/70 hover:bg-muted/20"
      )}
    >
      <button
        type="button"
        aria-pressed={active}
        onClick={onSelect}
        className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <p
          className={cn(
            "truncate text-xs font-medium leading-5",
            active ? "text-foreground" : "text-foreground/90"
          )}
        >
          {title}
        </p>
        <p className="mt-0.5 truncate text-[10px] leading-4 text-muted-foreground">
          {subtitle}
        </p>
      </button>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
          <span
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              statusTone === "success" && "bg-[var(--color-success)]",
              statusTone === "warning" && "bg-[var(--color-warning)]",
              statusTone === "muted" && "bg-muted-foreground/40"
            )}
          />
          <span className="truncate">{statusText}</span>
        </span>
        <ToggleSwitch
          checked={enabled}
          disabled={busy}
          onChange={onToggle}
          label={toggleLabel}
        />
      </div>
    </div>
  );
}

function SidebarGroup({
  icon,
  title,
  count,
  children,
}: {
  icon: ReactNode;
  title: string;
  count?: number;
  children: ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-1.5 flex items-center gap-1.5 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span className="text-[var(--brand)]">{icon}</span>
        {title}
        {count !== undefined && (
          <span className="font-normal text-muted-foreground/60">{count}</span>
        )}
      </h3>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

interface CustomFeedFormState {
  id: string;
  title_zh: string;
  query: string;
  providers: string[];
  lookback_years: string;
  limit: string;
  sort: "relevance" | "recent";
  required_any_terms: string;
}

const EMPTY_FORM: CustomFeedFormState = {
  id: "",
  title_zh: "",
  query: "",
  providers: ["arxiv"],
  lookback_years: "3",
  limit: "25",
  sort: "relevance",
  required_any_terms: "",
};

export function DataSourceSettings() {
  const [sources, setSources] = useState<ObservationalDataSource[] | null>(
    null
  );
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [feeds, setFeeds] = useState<KbLiteratureFeed[] | null>(null);
  const [feedsError, setFeedsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [showFeedForm, setShowFeedForm] = useState(false);
  const [form, setForm] = useState<CustomFeedFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setActionError(null);
    const [sourcesResult, feedsResult] = await Promise.allSettled([
      fetchDataSources(),
      fetchKbLiteratureFeeds(),
    ]);
    if (sourcesResult.status === "fulfilled") {
      setSources(sourcesResult.value.sources);
      setSourcesError(null);
    } else {
      setSources(null);
      setSourcesError(
        sourcesResult.reason instanceof Error
          ? sourcesResult.reason.message
          : "无法读取观测数据源。"
      );
    }
    if (feedsResult.status === "fulfilled") {
      setFeeds(feedsResult.value.feeds);
      setFeedsError(
        feedsResult.value.status === "ok"
          ? null
          : feedsResult.value.diagnostic || "论文源订阅目录不可用。"
      );
    } else {
      setFeeds(null);
      setFeedsError(
        feedsResult.reason instanceof Error
          ? feedsResult.reason.message
          : "无法读取论文源订阅。"
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep a valid selection: default to the first observational source,
  // falling back to the first literature feed.
  useEffect(() => {
    if (showFeedForm) return;
    if (selection) {
      const exists =
        selection.kind === "dataset"
          ? (sources ?? []).some((item) => item.id === selection.id)
          : (feeds ?? []).some((item) => item.id === selection.id);
      if (exists) return;
    }
    if (feeds && feeds.length > 0) {
      setSelection({ kind: "feed", id: feeds[0].id });
    } else if (sources && sources.length > 0) {
      setSelection({ kind: "dataset", id: sources[0].id });
    } else {
      setSelection(null);
    }
  }, [sources, feeds, selection, showFeedForm]);

  async function toggleSource(source: ObservationalDataSource, next: boolean) {
    setBusyId(source.id);
    setActionError(null);
    try {
      await setDataSourceEnabled(source.id, next);
      setSources((current) =>
        (current ?? []).map((item) =>
          item.id === source.id ? { ...item, enabled: next } : item
        )
      );
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "切换观测数据源失败。"
      );
    } finally {
      setBusyId(null);
    }
  }

  async function toggleFeed(feed: KbLiteratureFeed, next: boolean) {
    setBusyId(feed.id);
    setActionError(null);
    try {
      await setLiteratureFeedEnabled(feed.id, next);
      setFeeds((current) =>
        (current ?? []).map((item) =>
          item.id === feed.id ? { ...item, enabled: next } : item
        )
      );
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "切换论文源失败。"
      );
    } finally {
      setBusyId(null);
    }
  }

  async function submitCustomFeed() {
    setFormError(null);
    if (!form.id.trim() || !form.query.trim() || form.providers.length === 0) {
      setFormError("请填写 ID、检索式，并至少选择一个检索来源。");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createLiteratureFeed({
        id: form.id.trim(),
        title_zh: form.title_zh.trim() || undefined,
        query: form.query.trim(),
        providers: form.providers,
        lookback_years: Number(form.lookback_years) || 3,
        limit: Number(form.limit) || 25,
        sort: form.sort,
        required_any_terms: form.required_any_terms
          .split(/[,，]/)
          .map((term) => term.trim())
          .filter(Boolean),
      });
      setShowFeedForm(false);
      setForm(EMPTY_FORM);
      const catalog = await fetchKbLiteratureFeeds();
      setFeeds(catalog.feeds);
      setSelection({ kind: "feed", id: created.id });
    } catch (error) {
      setFormError(
        error instanceof Error ? error.message : "创建自定义论文源失败。"
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
        <Loader2
          className="size-4 animate-spin"
          aria-hidden="true"
        />
        正在读取数据源状态…
      </div>
    );
  }

  const selectedDataset =
    selection?.kind === "dataset"
      ? (sources ?? []).find((item) => item.id === selection.id) ?? null
      : null;
  const selectedFeed =
    selection?.kind === "feed"
      ? (feeds ?? []).find((item) => item.id === selection.id) ?? null
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {actionError && (
        <p
          role="alert"
          className="rounded-md bg-[var(--color-error)]/10 px-3 py-2 text-xs text-[var(--color-error)]"
        >
          {actionError}
        </p>
      )}

      <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto md:grid-cols-[minmax(240px,282px)_minmax(0,1fr)] md:overflow-hidden">
        {/* 左栏：数据源列表 + 添加入口 */}
        <aside
          aria-label="数据源列表"
          className="flex h-[min(60vh,560px)] min-h-[380px] flex-col md:h-auto md:min-h-0"
        >
          <div className="mb-2 flex items-center justify-between px-1">
            <p className="text-[11px] font-medium text-muted-foreground">
              数据源
            </p>
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RefreshCw
                className="size-3"
                aria-hidden="true"
              />
              刷新
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
            <SidebarGroup
              icon={
                <FileText
                  className="size-3"
                  aria-hidden="true"
                />
              }
              title="论文源"
              count={feeds?.length}
            >
              {feedsError && (
                <p className="px-1 text-[10px] leading-4 text-muted-foreground">
                  {feedsError}
                </p>
              )}
              {(feeds ?? []).map((feed) => (
                <NavItem
                  key={feed.id}
                  title={feed.title_zh}
                  subtitle={feed.query}
                  statusTone={
                    feed.latest_run
                      ? feed.latest_run.status === "ok"
                        ? "success"
                        : "warning"
                      : "muted"
                  }
                  statusText={
                    feed.latest_run
                      ? `候选 ${feed.source_count} 篇 · ${formatDate(feed.latest_run.started_at)}`
                      : "尚未同步"
                  }
                  active={
                    selection?.kind === "feed" && selection.id === feed.id
                  }
                  enabled={feed.enabled}
                  busy={busyId === feed.id}
                  toggleLabel={`启用 ${feed.title_zh}`}
                  onSelect={() => {
                    setShowFeedForm(false);
                    setSelection({ kind: "feed", id: feed.id });
                  }}
                  onToggle={(next) => void toggleFeed(feed, next)}
                />
              ))}
              <button
                type="button"
                onClick={() => {
                  setShowFeedForm(true);
                  setFormError(null);
                }}
                className={cn(
                  "flex w-full items-center gap-1.5 rounded-lg border border-dashed border-border px-2.5 py-2 text-[11px] text-muted-foreground transition-colors hover:border-[var(--brand)]/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  showFeedForm && "border-[var(--brand)]/60 text-foreground"
                )}
              >
                <Plus
                  className="size-3"
                  aria-hidden="true"
                />
                接入自定义论文源
              </button>
            </SidebarGroup>

            <SidebarGroup
              icon={
                <Database
                  className="size-3"
                  aria-hidden="true"
                />
              }
              title="观测数据源"
              count={sources?.length}
            >
              {sourcesError && (
                <p className="px-1 text-[10px] leading-4 text-muted-foreground">
                  {sourcesError}
                </p>
              )}
              {(sources ?? []).map((source) => {
                const registration = source.registration;
                return (
                  <NavItem
                    key={source.id}
                    title={source.title_zh}
                    subtitle={source.provider}
                    statusTone={registration ? "success" : "muted"}
                    statusText={
                      registration
                        ? `已接入 · ${formatDate(registration.coverage_end)}`
                        : "尚未接入"
                    }
                    active={
                      selection?.kind === "dataset" &&
                      selection.id === source.id
                    }
                    enabled={source.enabled}
                    busy={busyId === source.id}
                    toggleLabel={`启用 ${source.title_zh}`}
                    onSelect={() => {
                      setShowFeedForm(false);
                      setSelection({ kind: "dataset", id: source.id });
                    }}
                    onToggle={(next) => void toggleSource(source, next)}
                  />
                );
              })}
            </SidebarGroup>
          </div>
          <p className="mt-2 shrink-0 border-t border-border/60 px-1 pt-2 text-[10px] leading-4 text-muted-foreground/70">
            开关即时生效并保存在后端配置中。
          </p>
        </aside>

        {/* 右栏：详情与预览 */}
        <section
          aria-label="数据源详情与预览"
          className="min-h-0 overflow-visible border-t border-border/70 pt-4 pr-1 md:overflow-y-auto md:border-l md:border-t-0 md:pl-4 md:pt-0"
        >
          {showFeedForm ? (
            <div>
              <h3 className="text-sm font-semibold">接入自定义论文源</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                新建一个文献主题订阅，写入订阅目录后可由维护流程同步命中。
              </p>
              <div className="mt-4 grid gap-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="grid gap-1.5">
                    <Label htmlFor="feed-id">订阅 ID</Label>
                    <Input
                      id="feed-id"
                      value={form.id}
                      onChange={(event) =>
                        setForm({ ...form, id: event.target.value })
                      }
                      placeholder="my_custom_topic"
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <p className="text-[11px] text-muted-foreground">
                      3-80 位小写字母、数字或下划线
                    </p>
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="feed-title">名称</Label>
                    <Input
                      id="feed-title"
                      value={form.title_zh}
                      onChange={(event) =>
                        setForm({ ...form, title_zh: event.target.value })
                      }
                      placeholder="例如：日冕洞与高速流"
                      autoComplete="off"
                    />
                  </div>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="feed-query">检索式</Label>
                  <Input
                    id="feed-query"
                    value={form.query}
                    onChange={(event) =>
                      setForm({ ...form, query: event.target.value })
                    }
                    placeholder="coronal hole high speed stream solar wind"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    英文关键词效果更稳，不超过 500 字符
                  </p>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="feed-any-terms">任意匹配词（可选）</Label>
                  <Input
                    id="feed-any-terms"
                    value={form.required_any_terms}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        required_any_terms: event.target.value,
                      })
                    }
                    placeholder="coronal hole, high speed stream"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    逗号分隔；命中任意一个词才进入候选
                  </p>
                </div>
                <fieldset>
                  <legend className="mb-1.5 text-sm font-medium">
                    检索来源
                  </legend>
                  <div className="flex flex-wrap gap-3">
                    {PROVIDER_OPTIONS.map((provider) => (
                      <label
                        key={provider}
                        className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      >
                        <input
                          type="checkbox"
                          checked={form.providers.includes(provider)}
                          onChange={(event) =>
                            setForm({
                              ...form,
                              providers: event.target.checked
                                ? [...form.providers, provider]
                                : form.providers.filter(
                                    (item) => item !== provider
                                  ),
                            })
                          }
                          className="size-3.5 rounded border-border accent-[var(--brand)]"
                        />
                        {provider}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor="feed-lookback">回溯年限</Label>
                    <Input
                      id="feed-lookback"
                      type="number"
                      min={1}
                      max={10}
                      value={form.lookback_years}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          lookback_years: event.target.value,
                        })
                      }
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="feed-limit">单次上限</Label>
                    <Input
                      id="feed-limit"
                      type="number"
                      min={1}
                      max={50}
                      value={form.limit}
                      onChange={(event) =>
                        setForm({ ...form, limit: event.target.value })
                      }
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="feed-sort">排序</Label>
                    <select
                      id="feed-sort"
                      value={form.sort}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          sort: event.target.value as "relevance" | "recent",
                        })
                      }
                      className="h-9 rounded-md border border-border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    >
                      <option value="relevance">相关性优先</option>
                      <option value="recent">最新优先</option>
                    </select>
                  </div>
                </div>
                {formError && (
                  <p
                    role="alert"
                    className="text-xs text-[var(--color-error)]"
                  >
                    {formError}
                  </p>
                )}
                <div className="sticky bottom-0 flex justify-end gap-2 border-t border-border/60 bg-card pt-3">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setShowFeedForm(false);
                      setForm(EMPTY_FORM);
                      setFormError(null);
                    }}
                  >
                    取消
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={submitting}
                    onClick={() => void submitCustomFeed()}
                  >
                    {submitting && (
                      <Loader2
                        className="size-4 animate-spin"
                        aria-hidden="true"
                      />
                    )}
                    创建订阅
                  </Button>
                </div>
              </div>
            </div>
          ) : selectedDataset ? (
            <div className="space-y-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">
                    {selectedDataset.title_zh}
                  </h3>
                  {selectedDataset.registration ? (
                    <Badge tone="success">已接入</Badge>
                  ) : (
                    <Badge tone="muted">尚未接入</Badge>
                  )}
                  {!selectedDataset.enabled && (
                    <Badge tone="warning">已停用</Badge>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {selectedDataset.provider} · 权威太阳活动观测数据集
                </p>
              </div>
              <MetaGrid
                rows={[
                  ["数据集 ID", selectedDataset.id],
                  [
                    "权威地址",
                    <a
                      key="url"
                      href={selectedDataset.authority_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-[var(--brand)] underline-offset-2 hover:underline"
                    >
                      {selectedDataset.authority_url}
                      <ExternalLink
                        className="size-3"
                        aria-hidden="true"
                      />
                    </a>,
                  ],
                  [
                    "覆盖范围",
                    selectedDataset.registration
                      ? `${formatDate(selectedDataset.registration.coverage_start)} 至 ${formatDate(selectedDataset.registration.coverage_end)}`
                      : "—",
                  ],
                  [
                    "记录数",
                    selectedDataset.registration?.row_count != null
                      ? String(selectedDataset.registration.row_count)
                      : "—",
                  ],
                  [
                    "文件大小",
                    formatBytes(selectedDataset.registration?.bytes),
                  ],
                  [
                    "最近获取",
                    formatDate(
                      selectedDataset.registration?.retrieved_at ??
                        selectedDataset.registration?.registered_at
                    ),
                  ],
                  [
                    "存储路径",
                    selectedDataset.registration?.data_path ?? "—",
                  ],
                ]}
              />
              <DatasetPreview
                datasetId={selectedDataset.id}
                ingested={Boolean(selectedDataset.registration)}
              />
            </div>
          ) : selectedFeed ? (
            <div className="space-y-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">
                    {selectedFeed.title_zh}
                  </h3>
                  <Badge>{selectedFeed.providers.join(" · ")}</Badge>
                  <Badge tone="neutral">
                    近 {selectedFeed.lookback_years} 年
                  </Badge>
                  <Badge tone="neutral">
                    候选 {selectedFeed.source_count} 篇
                  </Badge>
                  {selectedFeed.latest_run ? (
                    <Badge
                      tone={
                        selectedFeed.latest_run.status === "ok"
                          ? "success"
                          : "warning"
                      }
                    >
                      上次同步 {formatDate(selectedFeed.latest_run.started_at)} ·{" "}
                      {selectedFeed.latest_run.status}
                    </Badge>
                  ) : (
                    <Badge tone="muted">尚未同步</Badge>
                  )}
                  {selectedFeed.custom && <Badge tone="neutral">自定义</Badge>}
                  {!selectedFeed.enabled && (
                    <Badge tone="warning">已停用</Badge>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  文献主题订阅 · 命中仅进入来源候选层
                </p>
              </div>
              <MetaGrid
                rows={[
                  ["订阅 ID", selectedFeed.id],
                  ["检索式", selectedFeed.query],
                  ["检索来源", selectedFeed.providers.join(" · ")],
                  [
                    "任意匹配词",
                    (selectedFeed.required_any_terms ?? []).join("、") || "—",
                  ],
                  ["回溯年限", `${selectedFeed.lookback_years} 年`],
                  ["单次上限", String(selectedFeed.limit)],
                  [
                    "排序",
                    selectedFeed.sort === "recent" ? "最新优先" : "相关性优先",
                  ],
                  [
                    "上次同步",
                    selectedFeed.latest_run
                      ? `${formatDate(selectedFeed.latest_run.started_at)} · ${selectedFeed.latest_run.status} · ${selectedFeed.latest_run.result_count} 个命中`
                      : "尚未同步",
                  ],
                ]}
              />
              <FeedPreview feedId={selectedFeed.id} />
            </div>
          ) : (
            <div className="flex h-full min-h-40 items-center justify-center rounded-lg border border-dashed border-border px-4 text-center text-xs text-muted-foreground">
              请选择左侧的数据源，在右侧查看详情与预览。
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
