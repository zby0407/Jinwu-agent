"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ChevronDown,
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

const PROVIDER_OPTIONS = ["ads", "arxiv", "openalex", "crossref"] as const;

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

function ToggleSwitch({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        checked ? "bg-[var(--brand)]" : "bg-muted-foreground/30",
        disabled && "cursor-not-allowed opacity-50"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 transform rounded-full bg-background shadow transition-transform",
          checked ? "translate-x-[1.125rem]" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

function MetaGrid({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-xs">
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

function SectionTitle({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="mb-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-[var(--brand)]">{icon}</span>
        {title}
      </h3>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {description}
      </p>
    </div>
  );
}

function SourceRow({
  title,
  subtitle,
  badges,
  enabled,
  busy,
  expanded,
  onToggle,
  onToggleExpand,
  preview,
  toggleLabel,
}: {
  title: string;
  subtitle: string;
  badges: ReactNode;
  enabled: boolean;
  busy: boolean;
  expanded: boolean;
  onToggle: (next: boolean) => void;
  onToggleExpand: () => void;
  preview: ReactNode;
  toggleLabel: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-start gap-3 px-3.5 py-3">
        <button
          type="button"
          onClick={onToggleExpand}
          aria-expanded={expanded}
          className="mt-0.5 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown
            className={cn("size-4 transition-transform", expanded && "rotate-180")}
            aria-hidden="true"
          />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium leading-5">{title}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {subtitle}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">{badges}</div>
        </div>
        <ToggleSwitch
          checked={enabled}
          disabled={busy}
          onChange={onToggle}
          label={toggleLabel}
        />
      </div>
      {expanded && (
        <div className="border-t border-border/70 px-3.5 py-3">{preview}</div>
      )}
    </div>
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
  const [expandedId, setExpandedId] = useState<string | null>(null);
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
      await createLiteratureFeed({
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

  return (
    <div className="space-y-6">
      {actionError && (
        <p
          role="alert"
          className="rounded-md bg-[var(--color-error)]/10 px-3 py-2 text-xs text-[var(--color-error)]"
        >
          {actionError}
        </p>
      )}

      <section>
        <SectionTitle
          icon={
            <Database
              className="size-4"
              aria-hidden="true"
            />
          }
          title="观测数据源"
          description="权威太阳活动观测数据集。关闭后，数据摄取流程将跳过该数据源。"
        />
        {sourcesError ? (
          <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            {sourcesError}
          </p>
        ) : (
          <div className="space-y-2">
            {(sources ?? []).map((source) => {
              const registration = source.registration;
              return (
                <SourceRow
                  key={source.id}
                  title={source.title_zh}
                  subtitle={source.provider}
                  toggleLabel={`启用 ${source.title_zh}`}
                  enabled={source.enabled}
                  busy={busyId === source.id}
                  expanded={expandedId === source.id}
                  onToggle={(next) => void toggleSource(source, next)}
                  onToggleExpand={() =>
                    setExpandedId((current) =>
                      current === source.id ? null : source.id
                    )
                  }
                  badges={
                    <>
                      {registration ? (
                        <Badge tone="success">
                          已接入 · 最新数据 {formatDate(registration.coverage_end)}
                        </Badge>
                      ) : (
                        <Badge tone="muted">尚未接入</Badge>
                      )}
                      {!source.enabled && <Badge tone="warning">已停用</Badge>}
                    </>
                  }
                  preview={
                    <MetaGrid
                      rows={[
                        ["数据集 ID", source.id],
                        [
                          "权威地址",
                          <a
                            key="url"
                            href={source.authority_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-[var(--brand)] underline-offset-2 hover:underline"
                          >
                            {source.authority_url}
                            <ExternalLink
                              className="size-3"
                              aria-hidden="true"
                            />
                          </a>,
                        ],
                        [
                          "覆盖范围",
                          registration
                            ? `${formatDate(registration.coverage_start)} 至 ${formatDate(registration.coverage_end)}`
                            : "—",
                        ],
                        [
                          "记录数",
                          registration?.row_count != null
                            ? String(registration.row_count)
                            : "—",
                        ],
                        ["文件大小", formatBytes(registration?.bytes)],
                        [
                          "最近获取",
                          formatDate(
                            registration?.retrieved_at ??
                              registration?.registered_at
                          ),
                        ],
                        ["存储路径", registration?.data_path ?? "—"],
                      ]}
                    />
                  }
                />
              );
            })}
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-start justify-between gap-3">
          <SectionTitle
            icon={
              <FileText
                className="size-4"
                aria-hidden="true"
              />
            }
            title="论文源"
            description="文献主题订阅。关闭后同步任务会跳过该订阅；命中仅进入来源候选层。"
          />
        </div>
        {feedsError && (
          <p className="mb-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            {feedsError}
          </p>
        )}
        {feeds && feeds.length > 0 && (
          <div className="space-y-2">
            {feeds.map((feed) => (
              <SourceRow
                key={feed.id}
                title={feed.title_zh}
                subtitle={feed.query}
                toggleLabel={`启用 ${feed.title_zh}`}
                enabled={feed.enabled}
                busy={busyId === feed.id}
                expanded={expandedId === feed.id}
                onToggle={(next) => void toggleFeed(feed, next)}
                onToggleExpand={() =>
                  setExpandedId((current) =>
                    current === feed.id ? null : feed.id
                  )
                }
                badges={
                  <>
                    <Badge>{feed.providers.join(" · ")}</Badge>
                    <Badge tone="neutral">近 {feed.lookback_years} 年</Badge>
                    <Badge tone="neutral">候选 {feed.source_count} 篇</Badge>
                    {feed.latest_run ? (
                      <Badge
                        tone={feed.latest_run.status === "ok" ? "success" : "warning"}
                      >
                        上次同步 {formatDate(feed.latest_run.started_at)} ·{" "}
                        {feed.latest_run.status}
                      </Badge>
                    ) : (
                      <Badge tone="muted">尚未同步</Badge>
                    )}
                    {feed.custom && <Badge tone="neutral">自定义</Badge>}
                    {!feed.enabled && <Badge tone="warning">已停用</Badge>}
                  </>
                }
                preview={
                  <MetaGrid
                    rows={[
                      ["订阅 ID", feed.id],
                      ["检索式", feed.query],
                      ["检索来源", feed.providers.join(" · ")],
                      [
                        "任意匹配词",
                        (feed.required_any_terms ?? []).join("、") || "—",
                      ],
                      ["回溯年限", `${feed.lookback_years} 年`],
                      ["单次上限", String(feed.limit)],
                      ["排序", feed.sort === "recent" ? "最新优先" : "相关性优先"],
                      [
                        "上次同步",
                        feed.latest_run
                          ? `${formatDate(feed.latest_run.started_at)} · ${feed.latest_run.status} · ${feed.latest_run.result_count} 个命中`
                          : "尚未同步",
                      ],
                    ]}
                  />
                }
              />
            ))}
          </div>
        )}

        <div className="mt-3">
          {!showFeedForm ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setShowFeedForm(true);
                setFormError(null);
              }}
            >
              <Plus
                className="size-4"
                aria-hidden="true"
              />
              接入自定义论文源
            </Button>
          ) : (
            <div className="rounded-lg border border-border bg-muted/15 p-4">
              <h4 className="text-sm font-semibold">接入自定义论文源</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                新建一个文献主题订阅，写入订阅目录后可由维护流程同步命中。
              </p>
              <div className="mt-4 grid gap-3">
                <div className="grid gap-1.5 sm:grid-cols-2">
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
                      setForm({ ...form, required_any_terms: event.target.value })
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
                        setForm({ ...form, lookback_years: event.target.value })
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
                <div className="flex justify-end gap-2">
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
          )}
        </div>
      </section>

      <div className="flex items-center justify-between border-t border-border/70 pt-3">
        <p className="text-[11px] text-muted-foreground">
          开关即时生效并保存在后端配置中。
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void load()}
          className="text-muted-foreground"
        >
          <RefreshCw
            className="size-3.5"
            aria-hidden="true"
          />
          刷新
        </Button>
      </div>
    </div>
  );
}
