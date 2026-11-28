"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  ArrowLeft,
  ArrowRight,
  Database,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  Satellite,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fetchDataSources,
  type ObservationalDataSource,
} from "@/lib/dataSources";
import {
  fetchKbLiteratureFeeds,
  type KbLiteratureFeed,
} from "@/lib/knowledgeBase";
import { DatasetPreview, FeedPreview } from "@/app/components/SourcePreview";
import { AstronomyQueryPanel } from "@/app/components/DataSourceQueryPanel";

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

function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

function hostOf(url: string | undefined): string {
  if (!url) return "—";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function DataBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "muted";
}) {
  return (
    <span
      className="research-data-badge"
      data-tone={tone}
    >
      {children}
    </span>
  );
}

function MetaItems({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="research-detail-meta">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ExternalAuthorityLink({ url, label }: { url: string; label: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="research-detail-link"
    >
      {label}
      <ExternalLink
        className="size-3.5"
        aria-hidden="true"
      />
    </a>
  );
}

function SourceRow({
  icon,
  title,
  description,
  meta,
  badges,
  actionLabel,
  onOpen,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  meta: string;
  badges: ReactNode;
  actionLabel: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="research-data-row"
      onClick={onOpen}
    >
      <span
        className="research-data-icon"
        aria-hidden="true"
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="research-data-title-line">
          <strong>{title}</strong>
          {badges}
        </span>
        <span className="research-data-desc">{description}</span>
        <span className="research-data-meta">{meta}</span>
      </span>
      <span className="research-data-open">
        {actionLabel}
        <ArrowRight
          className="size-4"
          aria-hidden="true"
        />
      </span>
    </button>
  );
}

function SourceGroup({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <section
      className="research-data-section"
      aria-labelledby={`${title}-heading`}
    >
      <div className="research-data-section-heading">
        <h3 id={`${title}-heading`}>{title}</h3>
        <span>{count} 项</span>
      </div>
      <div className="research-data-list">{children}</div>
    </section>
  );
}

function datasetStatus(source: ObservationalDataSource) {
  if (source.query_mode !== "local_dataset") return "online" as const;
  return source.registration ? ("ingested" as const) : ("missing" as const);
}

function datasetBadges(source: ObservationalDataSource) {
  const status = datasetStatus(source);
  return (
    <>
      <DataBadge tone={status === "missing" ? "muted" : "success"}>
        {source.query_mode === "local_dataset"
          ? status === "ingested"
            ? "本地数据 · 已接入"
            : "本地数据 · 未接入"
          : "在线 API"}
      </DataBadge>
      {source.query_mode !== "local_dataset" && (
        <DataBadge>{source.query_mode.toUpperCase()}</DataBadge>
      )}
      {!source.enabled && <DataBadge tone="warning">已停用</DataBadge>}
    </>
  );
}

function datasetRowMeta(source: ObservationalDataSource): string {
  if (source.query_mode !== "local_dataset") {
    return [
      source.provider,
      `端点 ${hostOf(source.authority_url)}`,
      source.enabled ? "支持在线查询" : "查询已停用",
    ].join(" · ");
  }
  const registration = source.registration;
  if (!registration) {
    return [
      source.provider,
      `来源 ${hostOf(source.authority_url)}`,
      "尚未接入数据文件",
    ].join(" · ");
  }
  const parts = [source.provider];
  if (registration.coverage_start || registration.coverage_end) {
    parts.push(
      `覆盖 ${formatDate(registration.coverage_start)} 至 ${formatDate(
        registration.coverage_end
      )}`
    );
  }
  if (registration.row_count != null)
    parts.push(`${formatCount(registration.row_count)} 行`);
  if (registration.bytes != null) parts.push(formatBytes(registration.bytes));
  return parts.join(" · ");
}

function feedBadges(feed: KbLiteratureFeed) {
  return (
    <>
      <DataBadge
        tone={
          feed.latest_run?.status === "ok"
            ? "success"
            : feed.latest_run
            ? "warning"
            : "muted"
        }
      >
        {feed.latest_run
          ? `已同步 · ${
              feed.latest_run.status === "ok"
                ? "完成"
                : feed.latest_run.status === "partial"
                ? "部分完成"
                : "不可用"
            }`
          : "文献订阅 · 未同步"}
      </DataBadge>
      {feed.custom && <DataBadge>自定义</DataBadge>}
      {!feed.enabled && <DataBadge tone="warning">已停用</DataBadge>}
    </>
  );
}

function feedRowMeta(feed: KbLiteratureFeed): string {
  return [
    feed.providers.join(" / "),
    `候选 ${formatCount(feed.source_count)} 篇`,
    feed.latest_run
      ? `上次同步 ${formatDate(feed.latest_run.started_at)}`
      : "尚未同步",
  ].join(" · ");
}

function DatasetDetail({ source }: { source: ObservationalDataSource }) {
  const registration = source.registration;
  const isLocal = source.query_mode === "local_dataset";
  const rows: Array<[string, ReactNode]> = [
    ["ID", source.id],
    ["类型", isLocal ? "内置观测数据集" : "在线查询服务"],
    ["提供方", source.provider],
    [
      "权威地址",
      <ExternalAuthorityLink
        key="authority"
        url={source.authority_url}
        label={hostOf(source.authority_url)}
      />,
    ],
  ];
  if (isLocal) {
    rows.push(
      [
        "覆盖范围",
        registration
          ? `${formatDate(registration.coverage_start)} 至 ${formatDate(
              registration.coverage_end
            )}`
          : "—",
      ],
      ["记录数", formatCount(registration?.row_count)],
      ["文件大小", formatBytes(registration?.bytes)],
      [
        "最近获取",
        formatDate(registration?.retrieved_at ?? registration?.registered_at),
      ],
      ["存储路径", registration?.data_path ?? "—"]
    );
  } else {
    rows.push(
      ["查询方式", source.query_mode.toUpperCase()],
      ["默认对象 / 查询", source.default_query || "—"],
      ["返回上限", "单次最多 200 行"]
    );
  }

  return (
    <article className="research-detail-panel">
      <header className="research-detail-header">
        <div className="min-w-0">
          <p className="research-eyebrow">
            {isLocal ? "OBSERVATIONAL DATASET" : "ONLINE DATA SERVICE"}
          </p>
          <div className="research-data-title-line research-detail-title-line">
            <h3>{source.title_zh}</h3>
            {datasetBadges(source)}
          </div>
          <p>{source.description || "系统内置数据源。"}</p>
        </div>
        <ExternalAuthorityLink
          url={source.authority_url}
          label="权威来源"
        />
      </header>
      <MetaItems items={rows} />
      <div className="research-detail-body">
        {isLocal ? (
          <>
            <div className="research-detail-body-heading">
              <h4>数据预览</h4>
              <p>读取已接入文件的样例行、字段统计与序列走势。</p>
            </div>
            <DatasetPreview
              datasetId={source.id}
              ingested={Boolean(registration)}
            />
          </>
        ) : source.enabled ? (
          <AstronomyQueryPanel
            source={source}
            showDescription={false}
          />
        ) : (
          <div className="research-detail-empty">
            此在线数据源已在配置中停用，启用后才能发起查询。
          </div>
        )}
      </div>
    </article>
  );
}

function FeedDetail({ feed }: { feed: KbLiteratureFeed }) {
  return (
    <article className="research-detail-panel">
      <header className="research-detail-header">
        <div className="min-w-0">
          <p className="research-eyebrow">LITERATURE FEED</p>
          <div className="research-data-title-line research-detail-title-line">
            <h3>{feed.title_zh}</h3>
            {feedBadges(feed)}
          </div>
          <p>文献主题订阅，同步命中的论文进入来源候选层。</p>
        </div>
      </header>
      <MetaItems
        items={[
          ["ID", feed.id],
          ["检索式", feed.query],
          ["检索来源", feed.providers.join(" / ")],
          ["回溯范围", `${feed.lookback_years} 年`],
          ["单次上限", String(feed.limit)],
          ["排序", feed.sort === "recent" ? "最新优先" : "相关性优先"],
          ["候选论文", `${formatCount(feed.source_count)} 篇`],
          [
            "上次同步",
            feed.latest_run
              ? `${formatDate(feed.latest_run.started_at)} · ${
                  feed.latest_run.result_count
                } 个命中`
              : "尚未同步",
          ],
        ]}
      />
      <div className="research-detail-body">
        <div className="research-detail-body-heading">
          <h4>论文预览</h4>
          <p>查看该订阅最近命中的论文，展开可读摘要。</p>
        </div>
        <FeedPreview feedId={feed.id} />
      </div>
    </article>
  );
}

export function DataCenter() {
  const [sources, setSources] = useState<ObservationalDataSource[] | null>(
    null
  );
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [feeds, setFeeds] = useState<KbLiteratureFeed[] | null>(null);
  const [feedsError, setFeedsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selection, setSelection] = useState<Selection | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
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
          : "无法读取内置数据源目录。"
      );
    }
    if (feedsResult.status === "fulfilled") {
      setFeeds(feedsResult.value.feeds);
      setFeedsError(
        feedsResult.value.status === "ok"
          ? null
          : feedsResult.value.diagnostic || "文献订阅目录不可用。"
      );
    } else {
      setFeeds(null);
      setFeedsError(
        feedsResult.reason instanceof Error
          ? feedsResult.reason.message
          : "无法读取文献订阅目录。"
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selection) return;
    const exists =
      selection.kind === "dataset"
        ? (sources ?? []).some((item) => item.id === selection.id)
        : (feeds ?? []).some((item) => item.id === selection.id);
    if ((sources !== null || feeds !== null) && !exists) setSelection(null);
  }, [feeds, selection, sources]);

  const selectedDataset =
    selection?.kind === "dataset"
      ? (sources ?? []).find((item) => item.id === selection.id) ?? null
      : null;
  const selectedFeed =
    selection?.kind === "feed"
      ? (feeds ?? []).find((item) => item.id === selection.id) ?? null
      : null;

  const localDatasets = useMemo(
    () =>
      (sources ?? []).filter((source) => source.query_mode === "local_dataset"),
    [sources]
  );
  const onlineSources = useMemo(
    () =>
      (sources ?? []).filter((source) => source.query_mode !== "local_dataset"),
    [sources]
  );
  const literatureFeeds = feeds ?? [];
  const ingestedCount = localDatasets.filter((source) =>
    Boolean(source.registration)
  ).length;

  if (selectedDataset || selectedFeed) {
    return (
      <div className="research-data-center">
        <button
          type="button"
          className="research-text-action research-back-action"
          onClick={() => setSelection(null)}
        >
          <ArrowLeft
            className="size-4"
            aria-hidden="true"
          />
          返回数据源目录
        </button>
        {selectedDataset ? (
          <DatasetDetail source={selectedDataset} />
        ) : selectedFeed ? (
          <FeedDetail feed={selectedFeed} />
        ) : null}
      </div>
    );
  }

  return (
    <div className="research-data-center">
      <div
        className="research-data-count"
        aria-label="数据源统计"
      >
        <strong>
          {formatCount((sources ?? []).length + literatureFeeds.length)}
          <span> 个数据源</span>
        </strong>
        <span>{ingestedCount} 个本地数据集已接入</span>
        <span>{onlineSources.length} 个在线服务</span>
        <span>{literatureFeeds.length} 个文献订阅</span>
        <button
          type="button"
          className="research-icon-action research-data-refresh"
          aria-label="刷新数据源"
          title="刷新数据源"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshCw
            className={cn("size-4", loading && "animate-spin")}
            aria-hidden="true"
          />
        </button>
      </div>

      {loading && !sources && !feeds ? (
        <div
          className="research-loading"
          role="status"
        >
          <Loader2
            className="size-4 animate-spin"
            aria-hidden="true"
          />
          正在读取数据源目录…
        </div>
      ) : (
        <>
          {(sourcesError || feedsError) && (
            <div
              className="research-data-errors"
              role="alert"
            >
              {sourcesError && <p>观测数据源：{sourcesError}</p>}
              {feedsError && <p>文献订阅：{feedsError}</p>}
            </div>
          )}

          <SourceGroup
            title="太阳观测数据集"
            count={localDatasets.length}
          >
            {localDatasets.map((source) => (
              <SourceRow
                key={source.id}
                icon={<Database className="size-4" />}
                title={source.title_zh}
                description={source.description || "官方太阳活动观测数据集。"}
                meta={datasetRowMeta(source)}
                badges={datasetBadges(source)}
                actionLabel="预览"
                onOpen={() => setSelection({ kind: "dataset", id: source.id })}
              />
            ))}
          </SourceGroup>

          <SourceGroup
            title="在线观测与档案服务"
            count={onlineSources.length}
          >
            {onlineSources.map((source) => (
              <SourceRow
                key={source.id}
                icon={<Satellite className="size-4" />}
                title={source.title_zh}
                description={source.description || "公共在线数据服务。"}
                meta={datasetRowMeta(source)}
                badges={datasetBadges(source)}
                actionLabel="查询"
                onOpen={() => setSelection({ kind: "dataset", id: source.id })}
              />
            ))}
          </SourceGroup>

          <SourceGroup
            title="文献订阅"
            count={literatureFeeds.length}
          >
            {literatureFeeds.map((feed) => (
              <SourceRow
                key={feed.id}
                icon={<FileText className="size-4" />}
                title={feed.title_zh}
                description={feed.query}
                meta={feedRowMeta(feed)}
                badges={feedBadges(feed)}
                actionLabel="预览"
                onOpen={() => setSelection({ kind: "feed", id: feed.id })}
              />
            ))}
          </SourceGroup>

          {!sourcesError &&
            !feedsError &&
            (sources ?? []).length === 0 &&
            literatureFeeds.length === 0 && (
              <div className="research-detail-empty">
                后端尚未返回任何内置数据源。
              </div>
            )}
        </>
      )}
    </div>
  );
}
