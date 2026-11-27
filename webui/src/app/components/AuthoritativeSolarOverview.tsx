"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  Activity,
  CalendarClock,
  CircleAlert,
  Database,
  ExternalLink,
  Loader2,
  RefreshCw,
  Sun,
} from "lucide-react";
import {
  fetchSolarActivityOverview,
  type SolarActivityOverview,
  type SolarActivitySource,
} from "@/lib/solarActivity";

function formatNumber(
  value: number | null | undefined,
  digits = 1,
  fallback = "—"
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return fallback;
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function stateLabel(rowState: "available" | "missing"): string {
  return rowState === "available" ? "已可支持" : "尚未接入";
}

function sourceBadge(source: SolarActivitySource): string {
  if (source.cache_state === "stale") return "使用旧缓存";
  return "缓存有效";
}

function MetricCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="solar-metric-card">
      <span className="solar-metric-icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        <span>{detail}</span>
      </div>
    </article>
  );
}

function monthStatus(row: {
  sunspot_provisional?: boolean;
  sunspot_number?: number | null;
  smoothed_sunspot_number?: number | null;
}): { label: string; tone: "success" | "warning" | "muted" } {
  if (row.sunspot_number == null) return { label: "缺测", tone: "muted" };
  if (row.sunspot_provisional) return { label: "暂定值", tone: "warning" };
  if (row.smoothed_sunspot_number == null)
    return { label: "平滑未满", tone: "warning" };
  return { label: "正式值", tone: "success" };
}

export function AuthoritativeSolarOverview() {
  const [overview, setOverview] = useState<SolarActivityOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    try {
      const result = await fetchSolarActivityOverview({ refresh, months: 24 });
      setOverview(result);
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "无法读取太阳活动概览数据。"
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const monthlyRows = useMemo(
    () => [...(overview?.monthly_rows ?? [])].reverse(),
    [overview]
  );
  const cycles = useMemo(
    () => [...(overview?.cycles ?? [])].reverse(),
    [overview]
  );
  const summary = overview?.summary;
  const currentCycle = summary?.current_cycle;

  return (
    <section className="research-page solar-activity-page mt-4" aria-labelledby="authoritative-solar-heading">
      <div>
        <header className="research-page-header">
          <div>
            <h3 id="authoritative-solar-heading">权威数据表</h3>
            <p>
              核对 SILSO 和 NOAA SWPC 的月度黑子数、13 个月平滑值、F10.7 和活动周极值。
            </p>
          </div>
          <div className="research-page-actions">
            <button
              type="button"
              className="research-action research-action-secondary"
              onClick={() => void load(true)}
              disabled={loading || refreshing}
            >
              {refreshing ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="size-4" aria-hidden="true" />
              )}
              {refreshing ? "正在刷新" : "刷新数据"}
            </button>
          </div>
        </header>

        {loading ? (
          <div className="research-loading">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            正在读取权威太阳观测数据…
          </div>
        ) : error && !overview ? (
          <div className="solar-error-panel" role="alert">
            <CircleAlert className="size-5" aria-hidden="true" />
            <div>
              <strong>暂时无法读取太阳活动数据</strong>
              <p>{error}</p>
            </div>
          </div>
        ) : overview ? (
          <>
            {error && (
              <div className="research-data-errors" role="status">
                最新刷新失败：{error}。当前展示最近一次可用数据。
              </div>
            )}
            <section className="solar-metrics" aria-label="太阳活动摘要">
              <MetricCard
                icon={<Sun className="size-5" />}
                label="最新月度黑子数"
                value={formatNumber(summary?.latest_sunspot?.value)}
                detail={
                  summary?.latest_sunspot
                    ? `${summary.latest_sunspot.month} · SILSO V2.0`
                    : "暂无可用观测"
                }
              />
              <MetricCard
                icon={<Activity className="size-5" />}
                label="13 个月平滑黑子数"
                value={formatNumber(summary?.latest_smoothed_sunspot?.value)}
                detail={
                  summary?.latest_smoothed_sunspot
                    ? `${summary.latest_smoothed_sunspot.month} · 官方平滑序列`
                    : "末尾月份尚未形成官方平滑值"
                }
              />
              <MetricCard
                icon={<Database className="size-5" />}
                label="最新月度 F10.7"
                value={formatNumber(summary?.latest_f107?.value)}
                detail={
                  summary?.latest_f107
                    ? `${summary.latest_f107.month} · sfu · NOAA SWPC`
                    : "暂无可用观测"
                }
              />
              <MetricCard
                icon={<CalendarClock className="size-5" />}
                label="当前太阳活动周"
                value={
                  currentCycle ? `第 ${currentCycle.cycle} 周` : "—"
                }
                detail={
                  currentCycle
                    ? `极小期 ${currentCycle.minimum.label} · ${
                        currentCycle.completed ? "已结束" : "进行中"
                      }`
                    : "暂无活动周数据"
                }
              />
            </section>

            <section className="solar-panel" aria-labelledby="solar-monthly-heading">
              <div className="solar-panel-heading">
                <div>
                  <h3 id="solar-monthly-heading">月度太阳活动观测表</h3>
                  <p>
                    最近 {monthlyRows.length} 个月；F10.7 单位为 sfu，-1
                    缺测值已从表中剔除。
                  </p>
                </div>
                <span>
                  覆盖 {summary?.coverage_start ?? "—"} 至{" "}
                  {summary?.coverage_end ?? "—"}
                </span>
              </div>
              <div className="solar-table-wrap">
                <table className="solar-table">
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th className="numeric">黑子数</th>
                      <th className="numeric">标准差</th>
                      <th className="numeric">观测数</th>
                      <th className="numeric">13 月平滑</th>
                      <th className="numeric">F10.7</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monthlyRows.map((row) => {
                      const status = monthStatus(row);
                      return (
                        <tr key={row.month}>
                          <td className="solar-month">{row.month}</td>
                          <td className="numeric">
                            {formatNumber(row.sunspot_number)}
                          </td>
                          <td className="numeric">
                            {formatNumber(row.sunspot_stddev)}
                          </td>
                          <td className="numeric">
                            {formatInteger(row.sunspot_observations)}
                          </td>
                          <td className="numeric">
                            {formatNumber(row.smoothed_sunspot_number)}
                          </td>
                          <td className="numeric">{formatNumber(row.f107)}</td>
                          <td>
                            <span
                              className="research-data-badge"
                              data-tone={status.tone}
                            >
                              {status.label}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <div className="solar-secondary-grid">
              <section className="solar-panel" aria-labelledby="solar-cycle-heading">
                <div className="solar-panel-heading compact">
                  <div>
                    <h3 id="solar-cycle-heading">太阳活动周极值</h3>
                    <p>SILSO 官方极小 / 极大时间表，当前周期进行中。</p>
                  </div>
                </div>
                <div className="solar-table-wrap">
                  <table className="solar-table compact">
                    <thead>
                      <tr>
                        <th>活动周</th>
                        <th>极小期</th>
                        <th className="numeric">极小黑子数</th>
                        <th>极大期</th>
                        <th className="numeric">极大黑子数</th>
                        <th>时长</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cycles.map((cycle) => (
                        <tr key={cycle.cycle}>
                          <td className="solar-month">第 {cycle.cycle} 周</td>
                          <td>{cycle.minimum.label}</td>
                          <td className="numeric">
                            {formatNumber(cycle.minimum.sunspot_number)}
                          </td>
                          <td>{cycle.maximum?.label ?? "进行中"}</td>
                          <td className="numeric">
                            {cycle.maximum
                              ? formatNumber(cycle.maximum.sunspot_number)
                              : "—"}
                          </td>
                          <td>{cycle.duration?.label ?? "进行中"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="solar-panel" aria-labelledby="solar-coverage-heading">
                <div className="solar-panel-heading compact">
                  <div>
                    <h3 id="solar-coverage-heading">此表的数据覆盖</h3>
                    <p>用于核对本次查询所用观测数据的覆盖情况。</p>
                  </div>
                </div>
                <div className="solar-capability-list">
                  {overview.capabilities.map((capability) => (
                    <div className="solar-capability-row" key={capability.feature}>
                      <div>
                        <strong>{capability.feature}</strong>
                        <p>{capability.note}</p>
                      </div>
                      <span
                        className="research-data-badge"
                        data-tone={
                          capability.state === "available" ? "success" : "muted"
                        }
                      >
                        {stateLabel(capability.state)}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <section className="solar-sources" aria-labelledby="solar-sources-heading">
              <div className="solar-panel-heading compact">
                <div>
                  <h3 id="solar-sources-heading">数据来源与缓存状态</h3>
                  <p>
                    页面生成时间 {formatDateTime(overview.generated_at)}
                    ；上游每 6 小时最多刷新一次，失败时保留上一份通过校验的数据。
                  </p>
                </div>
              </div>
              <div className="solar-source-grid">
                {overview.sources.map((source) => (
                  <article className="solar-source-card" key={source.id}>
                    <div>
                      <strong>{source.title_zh}</strong>
                      <span
                        className="research-data-badge"
                        data-tone={source.stale ? "warning" : "success"}
                      >
                        {sourceBadge(source)}
                      </span>
                    </div>
                    <p>{source.provider}</p>
                    <dl>
                      <div>
                        <dt>缓存时间</dt>
                        <dd>{formatDateTime(source.fetched_at)}</dd>
                      </div>
                      <div>
                        <dt>许可</dt>
                        <dd>{source.license}</dd>
                      </div>
                    </dl>
                    <a
                      href={source.authority_url}
                      target="_blank"
                      rel="noreferrer"
                      className="research-detail-link"
                    >
                      {hostOf(source.authority_url)}
                      <ExternalLink className="size-3.5" aria-hidden="true" />
                    </a>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : null}
      </div>
    </section>
  );
}
