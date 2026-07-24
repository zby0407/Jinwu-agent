"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Clock3,
  Database,
  ExternalLink,
  FileText,
  Library,
  Loader2,
  Network,
  Quote,
  RefreshCw,
  Search,
  ScrollText,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatFullTime } from "@/lib/time";
import {
  fetchKbEntries,
  fetchKbEntry,
  fetchKbGraph,
  fetchKbOverview,
  fetchKbReviewQueue,
  fetchKbSource,
  fetchKbSources,
  fetchKbUsage,
  type KbEntryDetail,
  type KbEntrySummary,
  type KbGraph,
  type KbGraphNode,
  type KbOverview,
  type KbReviewItem,
  type KbSourceDetail,
  type KbSourceSummary,
  type KbUsageRow,
} from "@/lib/knowledgeBase";

/* ------------------------------------------------------------------ */
/* 标签与徽章                                                          */
/* ------------------------------------------------------------------ */

const TYPE_LABELS: Record<string, string> = {
  concept: "概念",
  mechanism: "机制",
  data_source: "数据源",
  experiment_paradigm: "实验范式",
  hypothesis_template: "假设模板",
  finding: "发现",
  counterexample: "反例",
};

const STATUS_META: Record<string, { label: string; className: string }> = {
  canonical: {
    label: "正式",
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  candidate: {
    label: "候选",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  deprecated: {
    label: "已废弃",
    className: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  },
  superseded: {
    label: "已取代",
    className: "bg-muted text-muted-foreground",
  },
};

const CONFIDENCE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const KIND_META: Record<string, { label: string; className: string }> = {
  promote: {
    label: "晋升",
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  conflict: {
    label: "冲突",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  deprecate: {
    label: "废弃",
    className: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  },
};

const SOURCE_STAGE_META: Record<string, { label: string; className: string }> =
  {
    cached: {
      label: "已发现",
      className: "bg-muted text-muted-foreground",
    },
    fetched: {
      label: "已获取",
      className: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
    },
    distilled: {
      label: "已精炼",
      className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    },
  };

function Badge({ label, className }: { label: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium",
        className ?? "bg-muted text-muted-foreground"
      )}
    >
      {label}
    </span>
  );
}

function TypeBadge({ type }: { type: string }) {
  return <Badge label={TYPE_LABELS[type] ?? type} />;
}

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status];
  return (
    <Badge
      label={meta?.label ?? status}
      className={meta?.className}
    />
  );
}

function KindBadge({ kind }: { kind: string }) {
  const meta = KIND_META[kind];
  return (
    <Badge
      label={meta?.label ?? kind}
      className={meta?.className}
    />
  );
}

function fmtTs(ts: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isFinite(d.getTime()) ? formatFullTime(d) : ts;
}

function pct(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function plainTitle(value: string): string {
  return value
    .replace(/<[^>]*>/g, "")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .trim();
}

/* ------------------------------------------------------------------ */
/* 通用状态块                                                          */
/* ------------------------------------------------------------------ */

function Loading({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-4 text-xs text-muted-foreground">
      <Loader2
        className="size-4 animate-spin"
        aria-hidden="true"
      />
      {text}
    </div>
  );
}

function ErrorText({ error }: { error: string }) {
  return (
    <p
      className="px-1 text-xs text-[var(--color-error)]"
      role="status"
    >
      {error}
    </p>
  );
}

function EmptyState({
  icon: Icon,
  title,
  hint,
}: {
  icon: typeof Library;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <Icon
        className="size-9 text-muted-foreground/60"
        aria-hidden="true"
      />
      <p className="text-sm text-muted-foreground">{title}</p>
      <p className="text-xs text-muted-foreground/80">{hint}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Overview：Wiki 编译状态与知识缺口                                   */
/* ------------------------------------------------------------------ */

function OverviewView({ refreshKey }: { refreshKey: string }) {
  const [overview, setOverview] = useState<KbOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbOverview()
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("加载 Wiki 概览失败——后端在运行吗？");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (loading) return <Loading text="分析 Wiki 覆盖情况…" />;
  if (error) return <ErrorText error={error} />;
  if (!overview) return null;

  const metrics = [
    { label: "知识条目", value: overview.entries, hint: "已编译" },
    {
      label: "原始来源",
      value: overview.sources,
      hint: `${overview.source_families} 个来源族`,
    },
    {
      label: "已精炼",
      value: overview.distilled_sources,
      hint: `${pct(overview.coverage.distillation_rate)} 覆盖`,
    },
    {
      label: "待审核",
      value: overview.pending_reviews,
      hint: `${overview.by_status.candidate ?? 0} 个候选`,
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 overflow-hidden rounded-md border border-border bg-background lg:grid-cols-4">
        {metrics.map((metric) => (
          <div
            key={metric.label}
            className="border-b border-border p-4 even:border-l lg:border-b-0 lg:border-l lg:first:border-l-0"
          >
            <p className="text-xs text-muted-foreground">{metric.label}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">
              {metric.value}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{metric.hint}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)]">
        <section className="rounded-md border border-border bg-background p-4">
          <h3 className="text-sm font-semibold">来源处理</h3>
          <div className="mt-4 grid grid-cols-4 gap-2">
            {[
              ["发现", overview.sources],
              ["获取", overview.fetched_sources],
              ["精炼", overview.distilled_sources],
              ["正式", overview.by_status.canonical ?? 0],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <p className="text-lg font-semibold tabular-nums">{value}</p>
                <p className="text-xs text-muted-foreground">{label}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-foreground/70 transition-[width]"
              style={{ width: pct(overview.coverage.distillation_rate) }}
            />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            已精炼 {pct(overview.coverage.distillation_rate)} 的原始来源
          </p>

          <div className="mt-6 border-t border-border pt-4">
            <h3 className="text-sm font-semibold">来源分布</h3>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(overview.by_provider).map(([provider, count]) => (
                <Badge
                  key={provider}
                  label={`${provider} · ${count}`}
                />
              ))}
              {Object.keys(overview.by_provider).length === 0 && (
                <span className="text-sm text-muted-foreground">尚无来源</span>
              )}
            </div>
          </div>
        </section>

        <section>
          <div className="mb-2 flex items-center gap-1.5">
            <AlertTriangle className="size-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">待补内容</h3>
          </div>
          {overview.gaps.length === 0 ? (
            <p className="rounded-md border border-border bg-background p-4 text-sm text-muted-foreground">
              暂未检测到结构性缺口。
            </p>
          ) : (
            <div className="overflow-hidden rounded-md border border-border bg-background">
              {overview.gaps.map((gap, index) => (
                <div
                  key={gap.code}
                  className={cn("p-4", index > 0 && "border-t border-border")}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{gap.label}</span>
                    <Badge label={String(gap.count)} />
                  </div>
                  <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                    {gap.hint}
                  </p>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Browse：条目浏览 + 详情                                             */
/* ------------------------------------------------------------------ */

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-2 text-[11px] leading-relaxed">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function ContentFields({ content }: { content: Record<string, unknown> }) {
  const keys = Object.keys(content);
  if (keys.length === 0)
    return <p className="text-xs text-muted-foreground">暂无内容</p>;
  return (
    <div className="flex flex-col gap-2">
      {keys.map((key) => {
        const value = content[key];
        return (
          <div key={key}>
            <p className="mb-0.5 font-mono text-[11px] font-semibold text-muted-foreground">
              {key}
            </p>
            {Array.isArray(value) ? (
              <ul className="list-disc pl-4 text-xs text-foreground">
                {value.map((item, i) => (
                  <li
                    key={i}
                    className="break-words"
                  >
                    {typeof item === "string" ? item : JSON.stringify(item)}
                  </li>
                ))}
              </ul>
            ) : typeof value === "string" ? (
              <p className="whitespace-pre-wrap break-words text-xs text-foreground">
                {value}
              </p>
            ) : (
              <JsonBlock value={value} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function EntryDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<KbEntryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbEntry(id)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDetail(null);
        setError(
          err instanceof Error && err.message.startsWith("HTTP 404")
            ? "条目不存在或已被移除。"
            : "加载条目详情失败，请稍后重试。"
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex w-fit items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft
          className="size-3.5"
          aria-hidden="true"
        />
        返回列表
      </button>
      {loading && <Loading text="加载详情…" />}
      {error && <ErrorText error={error} />}
      {detail && (
        <div className="flex flex-col gap-5 text-sm">
          <div>
            <p className="break-words text-xl font-semibold">{detail.title}</p>
            <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
              {detail.id}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              <TypeBadge type={detail.type} />
              <StatusBadge status={detail.status} />
              <Badge
                label={`置信度 ${
                  CONFIDENCE_LABELS[detail.confidence] ?? detail.confidence
                }`}
              />
              <Badge
                label={`v${detail.version} · ${detail.version_count} 个版本`}
              />
            </div>
          </div>

          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">来源</dt>
            <dd className="break-all">
              {detail.source_type} · {detail.source_ref || "—"}
            </dd>
            <dt className="text-muted-foreground">适用范围</dt>
            <dd className="break-words">{detail.valid_range || "—"}</dd>
            <dt className="text-muted-foreground">创建</dt>
            <dd>
              {fmtTs(detail.created_at)} · {detail.created_by || "—"}
            </dd>
            <dt className="text-muted-foreground">更新</dt>
            <dd>{fmtTs(detail.updated_at)}</dd>
            {(detail.related_entries ?? []).length > 0 && (
              <>
                <dt className="text-muted-foreground">关联条目</dt>
                <dd className="flex flex-col gap-1">
                  {(detail.related_entries ?? []).map((related) => (
                    <span
                      key={related.id}
                      className="break-words"
                    >
                      {related.title}{" "}
                      <span className="font-mono text-[10px] text-muted-foreground">
                        ({related.type})
                      </span>
                    </span>
                  ))}
                </dd>
              </>
            )}
          </dl>

          {detail.source && (
            <div className="rounded-md border border-sky-500/20 bg-sky-500/5 p-2.5">
              <div className="flex items-center gap-1.5">
                <FileText className="size-3.5 text-sky-600 dark:text-sky-400" />
                <p className="font-semibold">原始文献</p>
              </div>
              <p className="mt-1 break-words font-medium">
                {plainTitle(detail.source.title)}
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {detail.source.provider}
                {detail.source.year ? ` · ${detail.source.year}` : ""}
                {detail.source.abstract_chars
                  ? ` · 摘要 ${detail.source.abstract_chars} 字符`
                  : ""}
              </p>
              {detail.source.url && (
                <a
                  href={detail.source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex items-center gap-1 text-[11px] text-sky-600 hover:underline dark:text-sky-400"
                >
                  打开来源
                  <ExternalLink className="size-3" />
                </a>
              )}
            </div>
          )}

          <div>
            <p className="mb-1 font-semibold text-foreground">内容</p>
            <ContentFields content={detail.content} />
          </div>

          {(Object.keys(detail.evidence ?? {}).length > 0 ||
            (detail.evidence_gaps ?? []).length > 0) && (
            <div>
              <p className="mb-1 font-semibold text-foreground">证据定位</p>
              <div className="flex flex-col gap-1.5">
                {Object.entries(detail.evidence ?? {}).map(
                  ([field, evidence]) => (
                    <div
                      key={field}
                      className="rounded-md border border-border bg-background p-2"
                    >
                      <div className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
                        <Quote className="size-3" />
                        {field}
                        {evidence.location ? ` · ${evidence.location}` : ""}
                      </div>
                      <p className="mt-1 border-l-2 border-amber-500/50 pl-2 text-xs leading-relaxed">
                        {evidence.quote || evidence.text || "—"}
                      </p>
                    </div>
                  )
                )}
                {(detail.evidence_gaps ?? []).map((gap, index) => (
                  <p
                    key={`${gap.field ?? "gap"}-${index}`}
                    className="rounded-md bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300"
                  >
                    证据缺口：{gap.field || "未指定字段"}
                    {gap.note ? ` · ${gap.note}` : ""}
                  </p>
                ))}
              </div>
            </div>
          )}

          <div>
            <p className="mb-1 font-semibold text-foreground">完整溯源元数据</p>
            {Object.keys(detail.provenance).length > 0 ? (
              <JsonBlock value={detail.provenance} />
            ) : (
              <p className="text-muted-foreground">暂无溯源记录</p>
            )}
          </div>

          <div>
            <p className="mb-1 font-semibold text-foreground">
              版本历史{" "}
              <span className="font-normal text-muted-foreground">
                ({detail.versions.length})
              </span>
            </p>
            <div className="flex flex-col gap-1">
              {detail.versions.map((v) => (
                <div
                  key={v.version}
                  className="rounded-md border border-border bg-background px-2 py-1.5"
                >
                  <span className="font-medium">v{v.version}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    · {fmtTs(v.changed_at)} · {v.changed_by || "—"}
                    {v.reason ? ` · ${v.reason}` : ""}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function BrowseView() {
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [entries, setEntries] = useState<KbEntrySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const seqRef = useRef(0);

  const load = useCallback(
    (filters: { type: string; status: string; q: string }) => {
      const seq = ++seqRef.current;
      setLoading(true);
      fetchKbEntries({ ...filters, limit: 100 })
        .then((rows) => {
          if (seqRef.current !== seq) return;
          setEntries(rows);
          setError(null);
        })
        .catch(() => {
          if (seqRef.current !== seq) return;
          setError("加载知识条目失败——后端在运行吗？");
        })
        .finally(() => {
          if (seqRef.current === seq) setLoading(false);
        });
    },
    []
  );

  // 选择器变化立即刷新；关键词输入防抖 300ms。
  useEffect(() => {
    const timer = setTimeout(() => load({ type, status, q: q.trim() }), 300);
    return () => clearTimeout(timer);
  }, [type, status, q, load]);

  if (selectedId) {
    return (
      <EntryDetail
        id={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }

  const selectClass =
    "h-9 min-w-[7rem] shrink-0 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <select
          aria-label="按类型过滤"
          value={type}
          onChange={(e) => setType(e.target.value)}
          className={selectClass}
        >
          <option value="">全部类型</option>
          {Object.entries(TYPE_LABELS).map(([value, label]) => (
            <option
              key={value}
              value={value}
            >
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="按状态过滤"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className={selectClass}
        >
          <option value="">全部状态</option>
          {Object.entries(STATUS_META).map(([value, meta]) => (
            <option
              key={value}
              value={value}
            >
              {meta.label}
            </option>
          ))}
        </select>
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索标题 / 内容 / 来源…"
            aria-label="搜索知识条目"
            className="h-9 w-full rounded-md border border-border bg-background pl-8 pr-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
      </div>

      {loading && <Loading />}
      {error && <ErrorText error={error} />}
      {!loading && !error && entries.length === 0 && (
        <EmptyState
          icon={BookOpen}
          title="暂无知识条目"
          hint="知识库还没有符合条件的条目；agent 沉淀候选条目后会显示在这里。"
        />
      )}

      {!loading && (
        <div className="grid gap-2 lg:grid-cols-2">
          {entries.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setSelectedId(entry.id)}
              className="flex min-h-28 w-full flex-col gap-2 rounded-md border border-border bg-background p-4 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <span className="break-words text-sm font-medium">
                {entry.title}
              </span>
              <span className="flex flex-wrap items-center gap-1">
                <TypeBadge type={entry.type} />
                <StatusBadge status={entry.status} />
                <Badge
                  label={`置信度 ${
                    CONFIDENCE_LABELS[entry.confidence] ?? entry.confidence
                  }`}
                />
              </span>
              <span className="mt-auto text-xs text-muted-foreground">
                {fmtTs(entry.updated_at)}
                {entry.source_ref ? ` · ${entry.source_ref}` : ""}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Sources：原始来源与精炼状态                                         */
/* ------------------------------------------------------------------ */

function SourceDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [source, setSource] = useState<KbSourceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbSource(id)
      .then((data) => {
        if (cancelled) return;
        setSource(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("加载来源详情失败。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex w-fit items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        返回来源
      </button>
      {loading && <Loading text="加载原始来源…" />}
      {error && <ErrorText error={error} />}
      {source && (
        <>
          <div>
            <div className="flex flex-wrap items-center gap-1">
              <Badge label={source.provider || "unknown"} />
              <Badge
                label={SOURCE_STAGE_META[source.stage]?.label ?? source.stage}
                className={SOURCE_STAGE_META[source.stage]?.className}
              />
              {source.year && <Badge label={String(source.year)} />}
            </div>
            <p className="mt-2 text-xl font-semibold leading-relaxed">
              {plainTitle(source.title)}
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {source.authors?.join(", ") || "作者未知"}
            </p>
            <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
              {source.source_id}
            </p>
          </div>

          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">DOI</dt>
            <dd className="break-all">{source.doi || "—"}</dd>
            <dt className="text-muted-foreground">文献族</dt>
            <dd className="break-all font-mono text-[10px]">
              {source.family_id || "—"}
            </dd>
            <dt className="text-muted-foreground">发现时间</dt>
            <dd>{fmtTs(source.last_seen_at || "")}</dd>
            <dt className="text-muted-foreground">精炼次数</dt>
            <dd>{source.distillation_count}</dd>
          </dl>

          {source.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex w-fit items-center gap-1 text-xs text-sky-600 hover:underline dark:text-sky-400"
            >
              打开原始页面
              <ExternalLink className="size-3" />
            </a>
          )}

          <div>
            <p className="mb-1 text-xs font-semibold">原始摘要</p>
            <p className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background p-2.5 text-xs leading-relaxed">
              {source.abstract || "该索引记录没有可用摘要。"}
            </p>
          </div>

          <div>
            <p className="mb-1 text-xs font-semibold">
              已生成的 Wiki 条目 ({source.distillations.length})
            </p>
            {source.distillations.length === 0 ? (
              <p className="rounded-md bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
                该来源尚未精炼，当前只能作为检索线索，不能直接充当已核验知识。
              </p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {source.distillations.map((item) => (
                  <div
                    key={`${item.entry_id}:${item.focus}`}
                    className="rounded-md border border-border bg-background p-2"
                  >
                    <div className="flex flex-wrap items-center gap-1">
                      {item.entry_type && <TypeBadge type={item.entry_type} />}
                      {item.entry_status && (
                        <StatusBadge status={item.entry_status} />
                      )}
                      <Badge label={item.relevance || "未标注相关性"} />
                    </div>
                    <p className="mt-1 text-xs font-medium">
                      {item.entry_title || item.entry_id}
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      精炼焦点：{item.focus}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SourcesView() {
  const [provider, setProvider] = useState("");
  const [state, setState] = useState("");
  const [q, setQ] = useState("");
  const [sources, setSources] = useState<KbSourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const seq = ++seqRef.current;
      setLoading(true);
      fetchKbSources({
        provider,
        state,
        q: q.trim(),
        limit: 100,
      })
        .then((rows) => {
          if (seqRef.current !== seq) return;
          setSources(rows);
          setError(null);
        })
        .catch(() => {
          if (seqRef.current === seq)
            setError("加载原始来源失败——后端在运行吗？");
        })
        .finally(() => {
          if (seqRef.current === seq) setLoading(false);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [provider, state, q]);

  if (selectedId) {
    return (
      <SourceDetail
        id={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }

  const providers = Array.from(
    new Set(sources.map((source) => source.provider).filter(Boolean))
  ).sort();
  const selectClass =
    "h-9 min-w-[8rem] rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row">
        <select
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          aria-label="按来源提供方过滤"
          className={selectClass}
        >
          <option value="">全部来源</option>
          {providers.map((value) => (
            <option
              key={value}
              value={value}
            >
              {value}
            </option>
          ))}
        </select>
        <select
          value={state}
          onChange={(event) => setState(event.target.value)}
          aria-label="按精炼阶段过滤"
          className={selectClass}
        >
          <option value="">全部阶段</option>
          <option value="cached">已发现</option>
          <option value="fetched">已获取</option>
          <option value="distilled">已精炼</option>
        </select>
      </div>
      <div className="relative sm:flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder="搜索题目、作者、DOI…"
          className="h-9 w-full rounded-md border border-border bg-background pl-8 pr-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {loading && <Loading />}
      {error && <ErrorText error={error} />}
      {!loading && !error && sources.length === 0 && (
        <EmptyState
          icon={Database}
          title="暂无原始来源"
          hint="使用文献检索工具发现来源后，会先进入这里，再逐步精炼为 Wiki 条目。"
        />
      )}
      {!loading && (
        <div className="grid gap-2 lg:grid-cols-2">
          {sources.map((source) => (
            <button
              key={source.source_id}
              type="button"
              onClick={() => setSelectedId(source.source_id)}
              className="min-h-32 rounded-md border border-border bg-background p-4 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <div className="flex items-start gap-2">
                <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium leading-relaxed">
                    {plainTitle(source.title)}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1">
                    <Badge label={source.provider} />
                    <Badge
                      label={
                        SOURCE_STAGE_META[source.stage]?.label ?? source.stage
                      }
                      className={SOURCE_STAGE_META[source.stage]?.className}
                    />
                    {source.year && <Badge label={String(source.year)} />}
                  </span>
                  <span className="mt-1 block truncate text-[10px] text-muted-foreground">
                    {source.authors?.slice(0, 3).join(", ") ||
                      source.doi ||
                      "—"}
                  </span>
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Graph：来源—条目关系网络                                             */
/* ------------------------------------------------------------------ */

function GraphView({ refreshKey }: { refreshKey: string }) {
  const [graph, setGraph] = useState<KbGraph | null>(null);
  const [selected, setSelected] = useState<KbGraphNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbGraph()
      .then((data) => {
        if (cancelled) return;
        setGraph(data);
        setSelected(null);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("加载知识图谱失败——后端在运行吗？");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const layout = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const visibleNodes = graph.nodes
      .filter((node) => node.degree > 0)
      .slice(0, 18);
    const visibleIds = new Set(visibleNodes.map((node) => node.id));
    const positioned = visibleNodes.map((node, index) => {
      const angle = (index / Math.max(visibleNodes.length, 1)) * Math.PI * 2;
      const radius = index % 2 === 0 ? 160 : 118;
      return {
        ...node,
        x: 340 + Math.cos(angle) * radius,
        y: 190 + Math.sin(angle) * radius,
      };
    });
    const byId = new Map(positioned.map((node) => [node.id, node]));
    return {
      nodes: positioned,
      edges: graph.edges
        .filter(
          (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)
        )
        .map((edge) => ({
          ...edge,
          sourceNode: byId.get(edge.source)!,
          targetNode: byId.get(edge.target)!,
        })),
    };
  }, [graph]);

  if (loading) return <Loading text="构建来源—知识关系图…" />;
  if (error) return <ErrorText error={error} />;
  if (!graph || graph.nodes.length === 0) {
    return (
      <EmptyState
        icon={Network}
        title="知识图谱为空"
        hint="条目绑定来源或建立关联后，关系会显示在这里。"
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 overflow-hidden rounded-md border border-border">
        {[
          ["节点", graph.stats.nodes],
          ["关系", graph.stats.edges],
          ["孤立", graph.stats.orphans],
        ].map(([label, value]) => (
          <div
            key={String(label)}
            className="border-l border-border bg-background p-3 text-center first:border-l-0"
          >
            <p className="text-xl font-semibold tabular-nums">{value}</p>
            <p className="text-xs text-muted-foreground">{label}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="overflow-hidden rounded-md border border-border bg-muted/20">
          <svg
            viewBox="0 0 680 380"
            className="h-auto w-full"
            role="img"
            aria-label="来源与 Wiki 条目的关系网络"
          >
            {layout.edges.map((edge) => (
              <line
                key={`${edge.source}:${edge.target}:${edge.relation}`}
                x1={edge.sourceNode.x}
                y1={edge.sourceNode.y}
                x2={edge.targetNode.x}
                y2={edge.targetNode.y}
                stroke={
                  edge.relation === "distilled_into"
                    ? "rgb(14 165 233 / 0.45)"
                    : "rgb(245 158 11 / 0.35)"
                }
                strokeWidth={Math.min(3, Math.max(1, edge.weight / 2))}
              />
            ))}
            {layout.nodes.map((node) => (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelected(node)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ")
                    setSelected(node);
                }}
                className="cursor-pointer outline-none"
              >
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={Math.min(12, 6 + node.degree)}
                  fill={
                    node.kind === "source"
                      ? "rgb(14 165 233)"
                      : node.status === "canonical"
                      ? "rgb(16 185 129)"
                      : "rgb(245 158 11)"
                  }
                  opacity={selected && selected.id !== node.id ? 0.45 : 0.9}
                />
                <title>{node.title}</title>
              </g>
            ))}
          </svg>
          <div className="flex flex-wrap gap-3 border-t border-border px-3 py-2 text-xs text-muted-foreground">
            <span>● 蓝：原始来源</span>
            <span>● 绿：正式条目</span>
            <span>● 黄：候选条目</span>
          </div>
        </div>

        {selected ? (
          <div className="h-fit rounded-md border border-border bg-background p-4">
            <div className="flex flex-wrap items-center gap-1">
              <Badge label={selected.kind === "source" ? "来源" : "条目"} />
              {selected.kind === "entry" && <TypeBadge type={selected.type} />}
              {selected.kind === "entry" && (
                <StatusBadge status={selected.status} />
              )}
              <Badge label={`${selected.degree} 条关系`} />
            </div>
            <p className="mt-3 text-sm font-medium leading-6">
              {plainTitle(selected.title)}
            </p>
            {selected.evidence_count !== undefined && (
              <p className="mt-2 text-xs text-muted-foreground">
                已定位证据 {selected.evidence_count} 条
              </p>
            )}
          </div>
        ) : (
          <div className="h-fit rounded-md border border-border bg-background p-4">
            <p className="text-sm font-medium">节点信息</p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              点击节点查看来源或条目。图中优先显示关系最密集的 18
              个节点，孤立条目保留在统计中。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Review：审核队列（只读）                                            */
/* ------------------------------------------------------------------ */

function ReviewView({ refreshKey }: { refreshKey: string }) {
  const [items, setItems] = useState<KbReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbReviewQueue("pending")
      .then((rows) => {
        if (cancelled) return;
        setItems(rows);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("加载审核队列失败——后端在运行吗？");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div className="flex flex-col gap-2">
      <p className="px-1 text-[11px] text-muted-foreground">
        晋升 / 冲突 / 废弃的待审事项。批准与驳回在聊天中的审批件完成。
      </p>
      {loading && <Loading />}
      {error && <ErrorText error={error} />}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          icon={ScrollText}
          title="审核队列为空"
          hint="没有待审的知识晋升或冲突事项。"
        />
      )}
      <div className="flex flex-col gap-1.5">
        {items.map((item) => (
          <div
            key={item.id}
            className="rounded-md border border-border bg-background px-2.5 py-2"
          >
            <div className="flex items-center gap-1.5">
              <KindBadge kind={item.kind} />
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
                {item.entry_id || "—"}
              </span>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                #{item.id}
              </span>
            </div>
            {Object.keys(item.payload).length > 0 && (
              <div className="mt-1.5">
                <JsonBlock value={item.payload} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Usage：使用溯源时间线                                               */
/* ------------------------------------------------------------------ */

function UsageView({ refreshKey }: { refreshKey: string }) {
  const [runIdInput, setRunIdInput] = useState("");
  const [runId, setRunId] = useState("");
  const [rows, setRows] = useState<KbUsageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchKbUsage(runId)
      .then((data) => {
        if (cancelled) return;
        setRows(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("加载使用记录失败——后端在运行吗？");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, refreshKey]);

  return (
    <div className="flex flex-col gap-2">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setRunId(runIdInput.trim());
        }}
        className="flex items-center gap-1.5"
      >
        <input
          type="text"
          value={runIdInput}
          onChange={(e) => setRunIdInput(e.target.value)}
          placeholder="按 run_id 过滤（留空看最近 50 条）…"
          aria-label="按 run_id 过滤"
          className="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          className="h-7 shrink-0 rounded-md border border-border px-2 text-xs font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          应用
        </button>
      </form>

      {loading && <Loading />}
      {error && <ErrorText error={error} />}
      {!loading && !error && rows.length === 0 && (
        <EmptyState
          icon={Clock3}
          title="暂无使用记录"
          hint="agent 读取知识条目时会自动记录溯源日志（R4）。"
        />
      )}
      <div className="flex flex-col gap-1.5">
        {rows.map((row) => (
          <div
            key={row.id}
            className="rounded-md border border-border bg-background px-2.5 py-2 text-xs"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-foreground">
                {row.agent || "—"}
              </span>
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {fmtTs(row.ts)}
              </span>
            </div>
            <p className="mt-0.5 break-words text-foreground">
              {row.entry_title || row.entry_id}
            </p>
            {row.purpose && (
              <p className="mt-0.5 break-words text-muted-foreground">
                用途：{row.purpose}
              </p>
            )}
            <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground/80">
              {row.run_id || "—"}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 面板主体                                                            */
/* ------------------------------------------------------------------ */

type KbView = "overview" | "browse" | "sources" | "graph" | "review" | "usage";

const VIEWS: Array<{
  key: KbView;
  label: string;
  description: string;
  Icon: typeof Library;
}> = [
  {
    key: "overview",
    label: "概览",
    description: "知识库覆盖、来源处理进度与待补内容",
    Icon: Library,
  },
  {
    key: "browse",
    label: "知识条目",
    description: "浏览经过整理的概念、机制、发现与反例",
    Icon: BookOpen,
  },
  {
    key: "sources",
    label: "文献来源",
    description: "查看原始文献及其获取和精炼状态",
    Icon: Database,
  },
  {
    key: "graph",
    label: "关系图谱",
    description: "检查文献、证据与知识条目之间的连接",
    Icon: Network,
  },
  {
    key: "review",
    label: "待审核",
    description: "查看尚未确认的晋升、冲突和废弃事项",
    Icon: ScrollText,
  },
  {
    key: "usage",
    label: "使用记录",
    description: "按研究任务追踪知识条目的引用情况",
    Icon: Clock3,
  },
];

/**
 * Full-page research wiki: raw sources, compiled entries, grounding graph,
 * review queue and usage provenance.
 */
export function KnowledgePanel({
  refreshSignal = 0,
}: {
  refreshSignal?: number;
}) {
  const [view, setView] = useState<KbView>("overview");
  const [refreshKey, setRefreshKey] = useState(0);
  const effectiveKey = `${refreshSignal}:${refreshKey}`;
  const activeView = VIEWS.find((item) => item.key === view) ?? VIEWS[0];

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-background">
      <header className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-[var(--brand)]">
            <Library
              className="size-4"
              aria-hidden="true"
            />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold">科研 Wiki</h1>
            <p className="truncate text-xs text-muted-foreground">
              文献来源、知识条目与证据链
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setRefreshKey((key) => key + 1)}
          aria-label="刷新科研 Wiki"
          title="刷新"
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw
            className="size-4"
            aria-hidden="true"
          />
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-52 shrink-0 flex-col border-r border-border p-2 md:flex">
          <nav
            aria-label="科研 Wiki"
            className="space-y-0.5"
          >
            {VIEWS.map(({ key, label, Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  view === key
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                )}
              >
                <Icon
                  className="size-4 shrink-0"
                  aria-hidden="true"
                />
                {label}
              </button>
            ))}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div
            role="tablist"
            aria-label="科研 Wiki 页面"
            className="flex shrink-0 items-center gap-0.5 overflow-x-auto border-b border-border px-2 py-1 md:hidden"
          >
            {VIEWS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={view === key}
                onClick={() => setView(key)}
                className={cn(
                  "shrink-0 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  view === key
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <main className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6 sm:py-6">
              <div className="mb-5">
                <h2 className="text-xl font-semibold">{activeView.label}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {activeView.description}
                </p>
              </div>

              {view === "overview" ? (
                <OverviewView refreshKey={effectiveKey} />
              ) : view === "browse" ? (
                <BrowseView key={effectiveKey} />
              ) : view === "sources" ? (
                <SourcesView key={effectiveKey} />
              ) : view === "graph" ? (
                <GraphView refreshKey={effectiveKey} />
              ) : view === "review" ? (
                <ReviewView refreshKey={effectiveKey} />
              ) : (
                <UsageView refreshKey={effectiveKey} />
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
