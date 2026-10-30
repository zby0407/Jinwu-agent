"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ExternalLink,
  FileText,
  Hash,
  Loader2,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { detectTextLanguage, type LanguageGuess } from "@/lib/language";
import {
  fetchDataSourcePreview,
  type DataSourcePreview,
  type DataSourcePreviewSeries,
} from "@/lib/dataSources";
import { fetchKbSource, fetchKbSources, type KbSourceSummary } from "@/lib/knowledgeBase";

const SERIES_COLORS = ["var(--brand)", "#8fb4dc"];

/* ------------------------------------------------------------------ */
/* 观测数据预览                                                          */
/* ------------------------------------------------------------------ */

function formatCell(value: string | number | null): string {
  if (value === null) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString("en-US")
      : String(value);
  }
  return value;
}

function SeriesChart({ series }: { series: DataSourcePreviewSeries[] }) {
  const width = 640;
  const height = 140;
  const padX = 8;
  const padTop = 10;
  const padBottom = 18;

  const { min, max } = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of series) {
      for (const point of s.points) {
        lo = Math.min(lo, point.value);
        hi = Math.max(hi, point.value);
      }
    }
    if (!Number.isFinite(lo)) return { min: 0, max: 1 };
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    const margin = (hi - lo) * 0.08;
    return { min: lo - margin, max: hi + margin };
  }, [series]);

  const pointCount = Math.max(...series.map((s) => s.points.length), 2);
  const xOf = (index: number) =>
    padX + (index / (pointCount - 1)) * (width - padX * 2);
  const yOf = (value: number) =>
    padTop + (1 - (value - min) / (max - min)) * (height - padTop - padBottom);

  const linePath = (points: Array<{ value: number }>) =>
    points
      .map((point, index) => `${index === 0 ? "M" : "L"}${xOf(index).toFixed(1)},${yOf(point.value).toFixed(1)}`)
      .join(" ");

  const ticks = [min, (min + max) / 2, max];

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-3">
        {series.map((s, index) => (
          <span
            key={s.name}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
          >
            <span
              className="inline-block size-2 rounded-full"
              style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }}
            />
            {s.name}
            <span className="text-muted-foreground/60">{s.point_count} 点</span>
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={series.map((s) => s.name).join("、")}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={padX}
              x2={width - padX}
              y1={yOf(tick)}
              y2={yOf(tick)}
              stroke="var(--border)"
              strokeWidth="0.5"
              strokeDasharray="2 3"
            />
            <text
              x={width - padX}
              y={yOf(tick) - 2}
              textAnchor="end"
              fontSize="9"
              fill="var(--muted-foreground)"
              opacity="0.7"
            >
              {tick.toFixed(tick === Math.floor(tick) ? 0 : 1)}
            </text>
          </g>
        ))}
        {series.map((s, index) => {
          const color = SERIES_COLORS[index % SERIES_COLORS.length];
          const path = linePath(s.points);
          const area = `${path} L${xOf(s.points.length - 1).toFixed(1)},${height - padBottom} L${xOf(0).toFixed(1)},${height - padBottom} Z`;
          return (
            <g key={s.name}>
              {series.length === 1 && (
                <path
                  d={area}
                  fill={color}
                  opacity="0.08"
                />
              )}
              <path
                d={path}
                fill="none"
                stroke={color}
                strokeWidth="1.5"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              <circle
                cx={xOf(s.points.length - 1)}
                cy={yOf(s.points[s.points.length - 1].value)}
                r="2.5"
                fill={color}
              />
            </g>
          );
        })}
        {series[0].points.length > 1 && (
          <>
            <text
              x={padX}
              y={height - 4}
              fontSize="9"
              fill="var(--muted-foreground)"
              opacity="0.8"
            >
              {series[0].points[0].label}
            </text>
            <text
              x={width - padX}
              y={height - 4}
              textAnchor="end"
              fontSize="9"
              fill="var(--muted-foreground)"
              opacity="0.8"
            >
              {series[0].points[series[0].points.length - 1].label}
            </text>
          </>
        )}
      </svg>
    </div>
  );
}

function PreviewStatTiles({ preview }: { preview: DataSourcePreview }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {preview.stats.slice(0, 6).map((stat) => (
        <div
          key={stat.name}
          className="rounded-md border border-border/70 bg-muted/20 px-2.5 py-2"
        >
          <p className="truncate font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            {stat.name}
          </p>
          <p className="mt-1 font-mono text-[11px] leading-4 text-foreground">
            {stat.mean.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            <span className="text-muted-foreground/60"> 均值</span>
          </p>
          <p className="font-mono text-[10px] leading-4 text-muted-foreground">
            {stat.min.toLocaleString("en-US", { maximumFractionDigits: 2 })} ~{" "}
            {stat.max.toLocaleString("en-US", { maximumFractionDigits: 2 })}
          </p>
          {stat.missing > 0 && (
            <p className="mt-0.5 text-[10px] text-[var(--color-warning)]">
              缺失 {stat.missing.toLocaleString("en-US")}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function PreviewTableRow({
  columns,
  cells,
}: {
  columns: DataSourcePreview["columns"];
  cells: Array<string | number | null>;
}) {
  return (
    <tr className="border-b border-border/40 last:border-b-0">
      {columns.map((column, index) => {
        const value = cells[index] ?? null;
        return (
          <td
            key={column.name}
            className={cn(
              "whitespace-nowrap px-2.5 py-1",
              value === null && "text-muted-foreground/50"
            )}
          >
            {formatCell(value)}
          </td>
        );
      })}
    </tr>
  );
}

function PreviewTable({ preview }: { preview: DataSourcePreview }) {
  const columns = preview.columns;
  const headRows = preview.head_rows;
  const tailRows = preview.tail_rows;

  return (
    <div className="overflow-hidden rounded-md border border-border/70">
      <div className="max-h-64 overflow-auto">
        <table className="w-full border-collapse font-mono text-[11px] leading-4">
          <thead className="sticky top-0 z-10 bg-card">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.name}
                  className="whitespace-nowrap border-b border-border/70 px-2.5 py-1.5 text-left font-medium text-muted-foreground"
                >
                  {column.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {headRows.map((cells, index) => (
              <PreviewTableRow
                key={`h${index}`}
                columns={columns}
                cells={cells}
              />
            ))}
            {preview.omitted_rows > 0 && (
              <tr>
                <td
                  colSpan={columns.length}
                  className="border-b border-border/40 px-2.5 py-1 text-center text-[10px] text-muted-foreground/70"
                >
                  … 中间省略 {preview.omitted_rows.toLocaleString("en-US")} 行 …
                </td>
              </tr>
            )}
            {tailRows.map((cells, index) => (
              <PreviewTableRow
                key={`t${index}`}
                columns={columns}
                cells={cells}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DatasetPreview({
  datasetId,
  ingested,
}: {
  datasetId: string;
  ingested: boolean;
}) {
  const [preview, setPreview] = useState<DataSourcePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDataSourcePreview(datasetId)
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : "读取数据预览失败。");
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  if (error) {
    if (!ingested || error.includes("404")) {
      return (
        <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
          尚未接入数据文件，暂无可预览的内容。完成数据摄取后，这里会显示样例行、统计与序列走势。
        </div>
      );
    }
    return (
      <p
        role="alert"
        className="rounded-md bg-[var(--color-error)]/10 px-3 py-2 text-xs text-[var(--color-error)]"
      >
        数据预览加载失败：{error}
      </p>
    );
  }

  if (!preview) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
        <Loader2
          className="size-3.5 animate-spin"
          aria-hidden="true"
        />
        正在解析数据文件…
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          数据预览 · {preview.file}
        </p>
        <p className="font-mono text-[10px] text-muted-foreground/70">
          {preview.row_count.toLocaleString("en-US")} 行 ·{" "}
          {preview.columns.length} 列
        </p>
      </div>

      {!preview.registered && (
        <p className="rounded-md bg-muted/40 px-2.5 py-1.5 text-[10px] leading-4 text-muted-foreground">
          数据文件已存在于项目目录，但还没有正式的接入回执（显示为"尚未接入"）。
        </p>
      )}

      {preview.series.length > 0 && <SeriesChart series={preview.series} />}

      {preview.stats.length > 0 && <PreviewStatTiles preview={preview} />}

      <div className="flex flex-wrap items-center gap-1.5">
        {preview.columns.map((column) => (
          <span
            key={column.name}
            className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 font-mono text-[10px] text-muted-foreground"
            title={column.numeric ? "数值列" : "文本列"}
          >
            {column.numeric ? (
              <Hash
                className="size-2.5"
                aria-hidden="true"
              />
            ) : (
              <Type
                className="size-2.5"
                aria-hidden="true"
              />
            )}
            {column.name}
          </span>
        ))}
      </div>

      <PreviewTable preview={preview} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 论文源预览                                                            */
/* ------------------------------------------------------------------ */

function SourceAbstract({
  sourceId,
  onLanguage,
}: {
  sourceId: string;
  onLanguage: (guess: LanguageGuess) => void;
}) {
  const [abstract, setAbstract] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchKbSource(sourceId)
      .then((detail) => {
        if (cancelled) return;
        const text = detail.abstract?.trim();
        setAbstract(text || null);
        if (text) onLanguage(detectTextLanguage(text));
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
    // onLanguage is a stable setState wrapper in the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId]);

  if (error) {
    return (
      <p className="text-[11px] text-muted-foreground">摘要加载失败。</p>
    );
  }
  if (abstract === null && !error) {
    return (
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Loader2
          className="size-3 animate-spin"
          aria-hidden="true"
        />
        正在读取摘要…
      </p>
    );
  }
  if (!abstract) {
    return (
      <p className="text-[11px] text-muted-foreground">
        该来源暂无摘要文本（仅元数据）。
      </p>
    );
  }
  return (
    <p className="text-[11px] leading-5 text-muted-foreground">
      {abstract.length > 600 ? `${abstract.slice(0, 600)}…` : abstract}
    </p>
  );
}

function FeedSourceRow({ source }: { source: KbSourceSummary }) {
  const [expanded, setExpanded] = useState(false);
  const [language, setLanguage] = useState<LanguageGuess>(() =>
    detectTextLanguage(source.title)
  );

  return (
    <div className="rounded-md border border-border/60 bg-muted/10">
      <div className="flex items-start gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          className="mt-0.5 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown
            className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
            aria-hidden="true"
          />
        </button>
        <div className="min-w-0 flex-1">
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="line-clamp-2 text-xs font-medium leading-5 text-foreground underline-offset-2 hover:text-[var(--brand)] hover:underline"
          >
            {source.title}
            <ExternalLink
              className="mb-0.5 ml-1 inline size-2.5 text-muted-foreground/60"
              aria-hidden="true"
            />
          </a>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
            {source.authors.length > 0 && (
              <span className="truncate">
                {source.authors.slice(0, 3).join("、")}
                {source.authors.length > 3 ? " 等" : ""}
              </span>
            )}
            {source.year && <span>{source.year}</span>}
            <span className="rounded-full bg-accent px-1.5 py-px font-mono text-[9px] uppercase">
              {source.provider}
            </span>
            <span className="rounded-full bg-accent px-1.5 py-px text-[9px]">
              {language.label}
            </span>
            {source.abstract_chars > 0 && (
              <span className="font-mono text-[9px] text-muted-foreground/60">
                摘要 {source.abstract_chars} 字符
              </span>
            )}
          </div>
        </div>
      </div>
      {expanded && (
        <div className="border-t border-border/50 px-3 py-2">
          <SourceAbstract
            sourceId={source.source_id}
            onLanguage={setLanguage}
          />
        </div>
      )}
    </div>
  );
}

export function FeedPreview({ feedId }: { feedId: string }) {
  const [sources, setSources] = useState<KbSourceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchKbSources({ feed_id: feedId, limit: 8 })
      .then((items) => {
        if (!cancelled) setSources(items);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : "读取论文列表失败。");
      });
    return () => {
      cancelled = true;
    };
  }, [feedId]);

  if (error) {
    return (
      <p
        role="alert"
        className="rounded-md bg-[var(--color-error)]/10 px-3 py-2 text-xs text-[var(--color-error)]"
      >
        论文预览加载失败：{error}
      </p>
    );
  }

  if (!sources) {
    return (
      <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
        <Loader2
          className="size-3.5 animate-spin"
          aria-hidden="true"
        />
        正在读取命中论文…
      </div>
    );
  }

  if (sources.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
        该订阅尚未命中任何论文。同步完成后，这里会显示最近命中的论文标题、来源与语言。
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <FileText
          className="size-3"
          aria-hidden="true"
        />
        最近命中论文 · 前 {sources.length} 篇
      </p>
      {sources.map((source) => (
        <FeedSourceRow
          key={source.source_id}
          source={source}
        />
      ))}
    </div>
  );
}
