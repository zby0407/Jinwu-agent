"use client";

import { useEffect, useState } from "react";
import { Download, ExternalLink, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  queryDataSource,
  type ObservationalDataSource,
  type DataSourceQueryResult,
} from "@/lib/dataSources";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function AstronomyQueryPanel({
  source,
  showDescription = true,
}: {
  source: ObservationalDataSource;
  showDescription?: boolean;
}) {
  const [query, setQuery] = useState(source.default_query ?? "");
  const [target, setTarget] = useState(source.default_query ?? "");
  const [radius, setRadius] = useState("2");
  const [loadingQuery, setLoadingQuery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DataSourceQueryResult | null>(null);

  useEffect(() => {
    setQuery(source.default_query ?? "");
    setTarget(source.default_query ?? "");
    setRadius("2");
    setResult(null);
    setError(null);
  }, [source.id, source.default_query]);

  async function runQuery() {
    setLoadingQuery(true);
    setError(null);
    try {
      const params: Record<string, string | number> =
        source.query_mode === "tap"
          ? { query }
          : source.query_mode === "mast"
          ? { target, radius_arcmin: Number(radius) }
          : source.query_mode === "horizons"
          ? { target }
          : {};
      setResult(await queryDataSource(source.id, params));
    } catch (queryError) {
      setResult(null);
      setError(
        queryError instanceof Error ? queryError.message : "数据源查询失败。"
      );
    } finally {
      setLoadingQuery(false);
    }
  }

  function downloadCsv() {
    if (!result) return;
    const escape = (value: unknown) => {
      const text = String(value ?? "");
      return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    };
    const content = [
      result.columns.map(escape).join(","),
      ...result.rows.map((row) =>
        result.columns.map((column) => escape(row[column])).join(",")
      ),
    ].join("\r\n");
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/csv;charset=utf-8" })
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${source.id}-${result.fetched_at.slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3 border-t border-border/70 pt-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-xs font-semibold">在线查询</h4>
        <a
          href={source.docs_url ?? source.authority_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-[var(--brand)] hover:underline"
        >
          API 文档
          <ExternalLink
            className="size-3"
            aria-hidden="true"
          />
        </a>
      </div>
      {showDescription && (
        <p className="text-xs leading-5 text-muted-foreground">
          {source.description}
        </p>
      )}
      {source.query_mode === "tap" ? (
        <div className="grid gap-1.5">
          <Label htmlFor={`query-${source.id}`}>只读 ADQL 查询</Label>
          <Textarea
            id={`query-${source.id}`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-h-28 font-mono text-xs"
            spellCheck={false}
          />
          <p className="text-[11px] text-muted-foreground">
            只接受 SELECT，单次最多返回 200 行。
          </p>
        </div>
      ) : source.query_mode === "mast" ? (
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <div className="grid gap-1.5">
            <Label htmlFor="mast-target">天体名称</Label>
            <Input
              id="mast-target"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="mast-radius">搜索半径（角分）</Label>
            <Input
              id="mast-radius"
              type="number"
              min="0.01"
              max="60"
              step="0.1"
              value={radius}
              onChange={(event) => setRadius(event.target.value)}
            />
          </div>
        </div>
      ) : source.query_mode === "horizons" ? (
        <div className="grid gap-1.5">
          <Label htmlFor="horizons-target">目标名称或 Horizons ID</Label>
          <Input
            id="horizons-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          读取 {source.provider} 的最新实时记录（与太阳活动页共用缓存）。
        </p>
      )}
      <Button
        type="button"
        size="sm"
        disabled={loadingQuery || !source.enabled}
        onClick={() => void runQuery()}
      >
        {loadingQuery ? (
          <Loader2
            className="size-4 animate-spin"
            aria-hidden="true"
          />
        ) : (
          <Search
            className="size-4"
            aria-hidden="true"
          />
        )}
        查询
      </Button>
      {error && (
        <p
          role="alert"
          className="text-xs text-[var(--color-error)]"
        >
          {error}
        </p>
      )}
      {result && (
        <div className="space-y-2">
          <p className="text-[11px] text-muted-foreground">
            {result.row_count} 行 · {formatDate(result.fetched_at)}
            {result.truncated ? " · 已截断" : ""}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={downloadCsv}
          >
            <Download
              className="size-4"
              aria-hidden="true"
            />
            下载 CSV
          </Button>
          {result.columns.length > 0 ? (
            <div className="max-h-80 overflow-auto rounded-md border border-border">
              <table className="min-w-full border-collapse text-left text-[11px]">
                <thead className="sticky top-0 bg-muted/90 text-muted-foreground">
                  <tr>
                    {result.columns.map((column) => (
                      <th
                        key={column}
                        className="whitespace-nowrap border-b border-border px-2 py-1.5 font-medium"
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 50).map((row, index) => (
                    <tr
                      key={index}
                      className="even:bg-muted/30"
                    >
                      {result.columns.map((column) => (
                        <td
                          key={column}
                          className="max-w-64 whitespace-nowrap border-b border-border/50 px-2 py-1.5"
                        >
                          {String(row[column] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/20 p-2 text-[11px] leading-5">
              {result.rows.map((row) => String(row.line ?? "")).join("\n") ||
                "没有命中记录。"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
