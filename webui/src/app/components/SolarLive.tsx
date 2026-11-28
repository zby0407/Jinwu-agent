"use client";

import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { CalendarDays, CircleDot, Orbit, Radiation, RefreshCw, Sun, X, Zap } from "lucide-react";
import { HistoryControls, nearestFrame, useHistoryTimes, type HistorySelection } from "@/app/components/SolarHistory";
import { SolarSourceLink as SourceLink } from "@/app/components/SolarSourceLink";
import { observationTime } from "@/lib/todaySun";
import {
  fetchSolarLive,
  flareColor,
  flareTone,
  liveImageUrl,
  magneticLabel,
  projectDisk,
  solarLiveKey,
  solarLiveOptions,
  type LiveImageKey,
  type LivePoint,
  type LiveRegion,
  type SolarLiveOverview,
} from "@/lib/solarLive";
import { formatHistoryTime, historyImageUrl, type HistoryChannelKey } from "@/lib/solarLive";
import { cn } from "@/lib/utils";

// SDO 1024 px browse images put the limb at 47.0 % of the width from centre.
const HMI_DISK = 0.47;
const HOUR = 3_600_000;

type ImageSection = SolarLiveOverview["images"][LiveImageKey];

function Section({ id, icon, title, aside, children, note }: {
  id: string;
  icon: ReactNode;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  note?: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="min-w-0">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 id={id} className="inline-flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-900">
          {icon}
          {title}
        </h3>
        {aside && <div className="text-xs text-slate-500">{aside}</div>}
      </header>
      <div className="mt-3">{children}</div>
      {note && <p className="mt-2 text-xs leading-5 text-slate-500">{note}</p>}
    </section>
  );
}

function Unavailable({ children }: { children: ReactNode }) {
  return <p role="status" className="py-6 text-sm text-slate-500">{children}</p>;
}

function SolarImage({ imageKey, section, alt, zone, overlay, wide, large, history }: {
  imageKey: LiveImageKey;
  section: ImageSection;
  alt: string;
  zone: string;
  overlay?: ReactNode;
  wide?: boolean;
  large?: boolean;
  history?: { src: string; caption: string } | null;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  if (!history && section.status !== "ok") return <div className="grid aspect-square place-items-center bg-slate-950 text-sm text-slate-400">图像暂不可用</div>;
  const meta = section.status === "ok" ? section : null;
  const src = history ? history.src : meta ? liveImageUrl(imageKey, meta) : "";
  return (
    <figure className={cn(wide ? "max-w-3xl" : large ? "max-w-2xl" : "max-w-xl")}>
      <div className={cn("relative bg-black", wide ? "aspect-[1024/960]" : "aspect-square")}>
        {failed === src ? (
          <div className="grid h-full place-items-center text-sm text-slate-400">图像暂不可用</div>
        ) : (
          <img src={src} alt={alt} className="absolute inset-0 h-full w-full object-contain" onError={() => setFailed(src)} />
        )}
        {failed !== src && overlay}
      </div>
      <figcaption className="flex flex-wrap justify-between gap-x-3 gap-y-1 pt-1.5 text-xs text-slate-500">
        {history ? (
          <>
            <span>{meta?.instrument ?? "SDO"} · {meta?.label ?? ""} · {history.caption}</span>
            <span>图像 <SourceLink href="https://sdo.gsfc.nasa.gov/assets/img/browse/">NASA SDO 快视图档案</SourceLink></span>
          </>
        ) : (
          <>
            <span>{meta?.instrument} · {meta?.label}{meta?.observed_at && ` · ${observationTime(meta.observed_at, zone)}`}</span>
            {meta && <span>图像 <SourceLink href={meta.source_url}>{meta.credit}</SourceLink></span>}
          </>
        )}
      </figcaption>
    </figure>
  );
}

function regionTone(mag: string | null) {
  const value = mag?.toUpperCase() ?? "";
  if (value.includes("D")) return "bg-red-600";
  if (value.includes("G")) return "bg-amber-600";
  return "bg-emerald-700";
}

function DiskGrid({ b0 }: { b0: number }) {
  const lines = useMemo(() => {
    const trace = (samples: [number, number][]) => samples
      .map(([lat, lon]) => projectDisk(lat, lon, b0))
      .filter((point) => point.visible)
      .map((point) => `${point.x.toFixed(4)},${(-point.y).toFixed(4)}`)
      .join(" ");
    const range = (from: number, to: number, step: number) => Array.from({ length: Math.floor((to - from) / step) + 1 }, (_, index) => from + index * step);
    return [
      ...range(-75, 75, 15).map((lat) => ({ key: `lat${lat}`, strong: lat === 0, points: trace(range(-90, 90, 3).map((lon) => [lat, lon])) })),
      ...range(-90, 90, 15).map((lon) => ({ key: `lon${lon}`, strong: lon === 0, points: trace(range(-90, 90, 3).map((lat) => [lat, lon])) })),
    ];
  }, [b0]);
  return (
    <svg viewBox="-1 -1 2 2" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
      {lines.map((line) => <polyline key={line.key} points={line.points} fill="none" stroke={line.strong ? "#0369a1" : "#0ea5e9"} strokeOpacity={line.strong ? 0.75 : 0.45} strokeWidth={line.strong ? 1.2 : 0.8} vectorEffect="non-scaling-stroke" />)}
    </svg>
  );
}

function RegionOverlay({ rows, b0, labels, grid }: { rows: LiveRegion[]; b0: number; labels: boolean; grid: boolean }) {
  const inset = `${(0.5 - HMI_DISK) * 100}%`;
  return (
    <div className="pointer-events-none absolute" style={{ left: inset, top: inset, right: inset, bottom: inset }}>
      {grid && <DiskGrid b0={b0} />}
      {labels && rows.filter((row) => row.disk.visible).map((row) => (
        <span key={row.number} className="absolute" style={{ left: `${(row.disk.x + 1) * 50}%`, top: `${(1 - row.disk.y) * 50}%` }}>
          <span className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/90 bg-black/30" />
          <span className={cn("absolute -translate-x-1/2 -translate-y-[155%] rounded px-1.5 py-px font-mono text-[11px] font-semibold text-white shadow ring-1 ring-white/70", regionTone(row.mag_class))}>
            {row.number}
          </span>
        </span>
      ))}
    </div>
  );
}

function Toggle({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={pressed} onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 aria-pressed:border-sky-300 aria-pressed:bg-sky-50 aria-pressed:text-sky-900">
      <span className={cn("size-1.5 rounded-full", pressed ? "bg-sky-600" : "bg-slate-300")} aria-hidden="true" />
      {children}
    </button>
  );
}

function SunspotSection({ data, zone, hist }: { data: SolarLiveOverview; zone: string; hist?: { src: string; caption: string } | null }) {
  const [labels, setLabels] = useState(true);
  const [grid, setGrid] = useState(false);
  const regions = data.regions;
  const visible = regions.status === "ok" ? regions.rows.filter((row) => row.disk.visible) : [];
  return (
    <Section id="live-sunspots" icon={<Sun className="size-4 text-amber-500" aria-hidden="true" />} title="太阳黑子区域"
      aside={!hist && regions.status === "ok" && `NOAA 报告 ${regions.report_date}`}
      note={!hist && regions.status === "ok" && <>位置按较差自转推算到图像时刻 · 数据 <SourceLink href={regions.source_url}>NOAA SWPC Solar Region Summary</SourceLink></>}>
      {!hist && (
        <div className="mb-2 flex flex-wrap gap-1.5" role="group" aria-label="日面叠加">
          <Toggle pressed={labels} onClick={() => setLabels(!labels)}>活动区编号</Toggle>
          <Toggle pressed={grid} onClick={() => setGrid(!grid)}>日面经纬网</Toggle>
        </div>
      )}
      <SolarImage imageKey="hmi" section={data.images.hmi} zone={zone} large alt="SDO/HMI 连续谱太阳圆面与活动区标注" history={hist}
        overlay={!hist && regions.status === "ok" && <RegionOverlay rows={regions.rows} b0={regions.b0_deg} labels={labels} grid={grid} />} />
      {hist ? <Unavailable>历史活动区报告（NOAA SRS 档案）接入中，当前仅展示历史图像。</Unavailable> : regions.status !== "ok" ? <Unavailable>活动区报告暂不可读取。</Unavailable> : (
        <div className="mt-4 overflow-x-auto border-t border-slate-200">
          <table className="w-full whitespace-nowrap text-left text-sm">
            <caption className="sr-only">日面可见活动区</caption>
            <thead className="text-xs text-slate-600">
              <tr>{["活动区", "黑子数", "磁类型", "McIntosh", "位置", "C / M / X 概率"].map((label) => <th key={label} scope="col" className="border-b border-slate-200 px-2 py-2 font-medium first:pl-0">{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100 tabular-nums">
              {visible.map((row) => (
                <tr key={row.number}>
                  <th scope="row" className="px-2 py-2 pl-0">
                    <span className={cn("rounded px-1.5 py-0.5 font-mono text-xs font-semibold text-white", regionTone(row.mag_class))}>{row.number}</span>
                  </th>
                  <td className="px-2 py-2">{row.spots ?? "—"}</td>
                  <td className="px-2 py-2 font-medium">{magneticLabel(row.mag_class) ?? "—"}</td>
                  <td className="px-2 py-2">{row.spot_class ?? "—"}</td>
                  <td className="px-2 py-2 font-mono text-xs text-slate-600">{row.location}</td>
                  <td className="px-2 py-2 text-slate-600">{[row.c_probability, row.m_probability, row.x_probability].map((value) => value === null ? "—" : `${value}%`).join(" / ")}</td>
                </tr>
              ))}
              {!visible.length && <tr><td colSpan={6} className="px-2 py-4 pl-0 text-slate-500">当前日面无编号活动区。</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

type ChartSeries = { label: string; color: string; points: LivePoint[]; colorAt?: (value: number) => string };

function LogTimeChart({ title, series, start, end, floor, ceiling, yLabel, ticks, tickLabel, bands, formatValue, zone, maxGap }: {
  title: string;
  series: ChartSeries[];
  start: number;
  end: number;
  floor: number;
  ceiling: number;
  yLabel: (power: number) => string;
  ticks: number[];
  tickLabel: (time: number) => string;
  bands?: { from: number; to: number; fill: string; label: string }[];
  formatValue: (value: number) => string;
  zone: string;
  maxGap: number;
}) {
  const [cursor, setCursor] = useState<number | null>(null);
  const [left, right, top, bottom] = [46, bands ? 570 : 588, 10, 196];
  const x = (time: number) => left + (time - start) / Math.max(end - start, 1) * (right - left);
  const y = (power: number) => bottom - (power - floor) / (ceiling - floor) * (bottom - top);
  const clampLog = (value: number) => Math.min(ceiling, Math.max(floor, Math.log10(value)));
  const paths = series.flatMap((item) => {
    const runs: { d: string; color: string }[] = [];
    let previous: LivePoint | null = null;
    let current: { d: string; color: string } | null = null;
    for (const point of item.points) {
      const [time, value] = point;
      if (time < start || time > end) continue;
      if (value === null) { previous = null; current = null; continue; }
      const color = item.colorAt?.(value) ?? item.color;
      const coord = `${x(time).toFixed(1)} ${y(clampLog(value)).toFixed(1)}`;
      const joined = previous && previous[1] !== null && time - previous[0] <= maxGap;
      if (!current || !joined || current.color !== color) {
        current = { d: joined && previous ? `M${x(previous[0]).toFixed(1)} ${y(clampLog(previous[1] as number)).toFixed(1)} L${coord}` : `M${coord}`, color };
        runs.push(current);
      } else current.d += ` L${coord}`;
      previous = point;
    }
    return runs.map((run, index) => ({ ...run, key: `${item.label}-${index}` }));
  });
  const nearest = (points: LivePoint[], at: number) => {
    let best: LivePoint | undefined;
    for (const point of points) if (point[1] !== null && (!best || Math.abs(point[0] - at) < Math.abs(best[0] - at))) best = point;
    return best && Math.abs(best[0] - at) <= maxGap ? best : undefined;
  };
  const readout = cursor === null ? null : series.map((item) => ({ item, point: nearest(item.points, cursor) }));
  return (
    <figure>
      <div className="flex min-h-5 flex-wrap items-center justify-between gap-x-3 text-xs text-slate-500">
        <figcaption className="font-medium text-slate-700">{title}</figcaption>
        {readout ? <span className="tabular-nums">{observationTime(cursor as number, zone)}{readout.map(({ item, point }) => <span key={item.label} className="ml-2" style={{ color: item.color }}>{item.label} {point ? formatValue(point[1] as number) : "—"}</span>)}</span> : null}
      </div>
      <svg viewBox="0 0 600 222" className="mt-1 block w-full touch-none select-none text-slate-500" role="img" aria-label={title}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const svgX = (event.clientX - rect.left) / rect.width * 600;
          setCursor(svgX < left || svgX > right ? null : start + (svgX - left) / (right - left) * (end - start));
        }}
        onPointerLeave={() => setCursor(null)}>
        {bands?.map((band) => (
          <g key={band.label}>
            <rect x={left} width={right - left} y={y(Math.min(band.to, ceiling))} height={y(band.from) - y(Math.min(band.to, ceiling))} fill={band.fill} />
            <text x={right + 4} y={(y(band.from) + y(Math.min(band.to, ceiling))) / 2 + 4} fontSize="11" fontWeight="600" fill="#9a3412">{band.label}</text>
          </g>
        ))}
        {Array.from({ length: ceiling - floor }, (_, index) => floor + index).flatMap((power) =>
          [2, 3, 4, 5, 6, 7, 8, 9].map((step) => <line key={`${power}-${step}`} x1={left} x2={right} y1={y(power + Math.log10(step))} y2={y(power + Math.log10(step))} stroke="#e2e8f0" strokeWidth="0.6" />))}
        {Array.from({ length: ceiling - floor + 1 }, (_, index) => floor + index).map((power) => (
          <g key={power}>
            <line x1={left} x2={right} y1={y(power)} y2={y(power)} stroke="#cbd5e1" />
            <text x={left - 7} y={y(power) + 4} textAnchor="end" fontSize="11" fill="currentColor">{yLabel(power)}</text>
          </g>
        ))}
        {ticks.map((time) => (
          <g key={time}>
            <line x1={x(time)} x2={x(time)} y1={bottom} y2={bottom + 4} stroke="#94a3b8" />
            <text x={x(time)} y={bottom + 18} textAnchor="middle" fontSize="11" fill="currentColor">{tickLabel(time)}</text>
          </g>
        ))}
        <line x1={left} x2={right} y1={bottom} y2={bottom} stroke="#94a3b8" />
        {paths.map((path) => <path key={path.key} d={path.d} fill="none" stroke={path.color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />)}
        {cursor !== null && <line x1={x(cursor)} x2={x(cursor)} y1={top} y2={bottom} stroke="#0f172a" strokeOpacity="0.35" strokeDasharray="3 3" />}
      </svg>
      {series.length > 1 && (
        <div className="mt-1 flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-slate-600">
          {series.map((item) => <span key={item.label} className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: item.color }} />{item.label}</span>)}
        </div>
      )}
    </figure>
  );
}

function alignedTicks(start: number, end: number, step: number, zone: string) {
  const offset = zone === "UTC" ? 0 : 8 * HOUR;
  const ticks: number[] = [];
  for (let time = Math.ceil((start + offset) / step) * step - offset; time <= end; time += step) ticks.push(time);
  return ticks;
}

const flareLetter = (power: number) => "ABCMX"[power + 8] ?? "";

function FlareSection({ data, zone, hist }: { data: SolarLiveOverview; zone: string; hist?: { src: string; caption: string } | null }) {
  const { xray, flares } = data;
  const chart = useMemo(() => {
    if (xray.status !== "ok") return null;
    const end = Date.parse(xray.latest_at);
    const start = end - 2 * HOUR;
    const values = xray.points.flatMap(([time, value]) => value !== null && time >= start ? [Math.log10(value)] : []);
    return {
      start,
      end,
      floor: Math.min(-7, Math.floor(Math.min(...values, -7))),
      ceiling: Math.min(-3, Math.max(-5, Math.ceil(Math.max(...values, -5)))),
    };
  }, [xray]);
  return (
    <Section id="live-flares" icon={<Zap className="size-4 text-red-500" aria-hidden="true" />} title="太阳耀斑"
      aside={xray.status === "ok" && `GOES 更新 ${observationTime(xray.latest_at, zone)}`}
      note={xray.status === "ok" && <>0.1–0.8 nm 软 X 射线 · 数据 <SourceLink href={xray.source_url}>NOAA SWPC / GOES XRS</SourceLink></>}>
      <SolarImage imageKey="aia131" section={data.images.aia131} zone={zone} alt="SDO/AIA 131 Å 太阳圆面" history={hist} />
      {hist ? <Unavailable>历史 X 射线流量曲线与耀斑清单（NOAA NCEI 档案）接入中，当前仅展示历史图像。</Unavailable> : xray.status !== "ok" || !chart ? <Unavailable>GOES X 射线数据暂不可读取。</Unavailable> : (
        <div className="mt-5">
          <LogTimeChart title="过去两小时的太阳活动" zone={zone} maxGap={3 * 60_000}
            series={[{ label: "0.1–0.8 nm", color: "#047857", points: xray.points, colorAt: (value) => flareColor[flareLetter(Math.floor(Math.log10(value)))] ?? "#047857" }]}
            start={chart.start} end={chart.end} floor={chart.floor} ceiling={chart.ceiling} yLabel={flareLetter}
            ticks={alignedTicks(chart.start, chart.end, HOUR / 2, zone)} tickLabel={(time) => observationTime(time, zone, false)}
            formatValue={(value) => `${value.toExponential(2)} W/m²`} />
          <dl className="mt-4 grid grid-cols-3 gap-x-4 border-t border-slate-200 pt-3">
            {([["当前值", xray.current], ["2 小时最大值", xray.max_2h], ["24 小时最大值", xray.max_24h]] as const).map(([label, level]) => (
              <div key={label}>
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd className={cn("mt-0.5 text-2xl font-semibold tabular-nums tracking-tight", flareTone(level.class))}>{level.class ?? "—"}</dd>
              </div>
            ))}
          </dl>
          {flares.status === "ok" && flares.events.length > 0 && (
            <div className="mt-4 border-t border-slate-200 pt-3">
              <h4 className="text-xs font-medium text-slate-700">最近耀斑（7 天）</h4>
              <ul className="mt-1 divide-y divide-slate-100 text-sm">
                {flares.events.slice(0, 5).map((event) => (
                  <li key={event.begin_at} className="flex items-center justify-between gap-3 py-1.5 tabular-nums">
                    <span className={cn("w-12 font-semibold", flareTone(event.class))}>{event.class}</span>
                    <span className="text-xs text-slate-500">峰值 {event.peak_at ? observationTime(event.peak_at, zone) : "—"}</span>
                    <span className="text-xs text-slate-500">{observationTime(event.begin_at, zone, false)} – {event.end_at ? observationTime(event.end_at, zone, false) : "进行中"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

function CoronalHoleSection({ data, zone, hist }: { data: SolarLiveOverview; zone: string; hist?: { src: string; caption: string } | null }) {
  return (
    <Section id="live-coronal-holes" icon={<CircleDot className="size-4 text-sky-500" aria-hidden="true" />} title="日冕洞"
      note="AIA 193 Å 图像中的大片暗区通常对应冕洞，是高速太阳风的源区。">
      <SolarImage imageKey="aia193" section={data.images.aia193} zone={zone} alt="SDO/AIA 193 Å 太阳圆面" history={hist} />
    </Section>
  );
}

const coronagraphs = [
  { key: "ccor1", label: "CCOR-1" },
  { key: "ccor2", label: "CCOR-2" },
  { key: "lasco", label: "LASCO C2" },
] as const;

function CmeSection({ data, zone, hist }: { data: SolarLiveOverview; zone: string; hist?: boolean }) {
  const [active, setActive] = useState<(typeof coronagraphs)[number]["key"]>("ccor1");
  return (
    <Section id="live-cme" icon={<Orbit className="size-4 text-rose-500" aria-hidden="true" />} title="日冕物质抛射"
      note="日冕仪遮挡日面，用于观察向外抛射的日冕物质；CCOR 为 NOAA 业务化日冕仪。">
      {hist ? <Unavailable>SOHO/LASCO 历史日冕仪图像接入中（CCOR 系列 2024 年后才有观测）。</Unavailable> : <>
      <div role="tablist" aria-label="日冕仪" className="mb-2 flex gap-1.5">
        {coronagraphs.map(({ key, label }) => (
          <button key={key} type="button" role="tab" aria-selected={active === key} onClick={() => setActive(key)}
            disabled={data.images[key].status !== "ok"}
            className="rounded-full border border-slate-200 px-3 py-1 text-sm font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-40 aria-selected:border-sky-700 aria-selected:bg-sky-700 aria-selected:text-white">
            {label}
          </button>
        ))}
      </div>
      <SolarImage key={active} imageKey={active} section={data.images[active]} zone={zone} wide={active !== "lasco"} alt={`${coronagraphs.find((item) => item.key === active)?.label} 日冕仪图像`} />
      </>}
    </Section>
  );
}

const protonBands = [
  { from: 1, to: 2, fill: "#fef9c3", label: "S1" },
  { from: 2, to: 3, fill: "#fef08a", label: "S2" },
  { from: 3, to: 4, fill: "#fed7aa", label: "S3" },
  { from: 4, to: 5, fill: "#fecaca", label: "S4" },
  { from: 5, to: 6, fill: "#fca5a5", label: "S5" },
];
const powerLabel = (power: number) => ({ [-2]: "0.01", [-1]: "0.1", 0: "1", 1: "10", 2: "100", 3: "1k", 4: "10k", 5: "100k", 6: "1M" } as Record<number, string>)[power] ?? `1e${power}`;

function ProtonSection({ data, zone, hist }: { data: SolarLiveOverview; zone: string; hist?: boolean }) {
  const { protons } = data;
  const chart = useMemo(() => {
    if (protons.status !== "ok") return null;
    const all = Object.values(protons.series).flat();
    const times = all.map(([time]) => time);
    const values = all.flatMap(([, value]) => value === null ? [] : [Math.log10(value)]);
    return { start: Math.min(...times), end: Math.max(...times), floor: Math.min(-1, Math.floor(Math.min(...values))) };
  }, [protons]);
  const dayLabel = new Intl.DateTimeFormat("zh-CN", { timeZone: zone, month: "numeric", day: "numeric" });
  return (
    <Section id="live-protons" icon={<Radiation className="size-4 text-red-500" aria-hidden="true" />} title="高能太阳质子"
      aside={protons.status === "ok" && (protons.s_scale > 0 ? <span className="font-semibold text-red-600">太阳辐射风暴 S{protons.s_scale}</span> : "未达 S1 阈值")}
      note={protons.status === "ok" && <>积分通量 粒子 · cm⁻² · s⁻¹ · sr⁻¹ · S 级按 ≥10 MeV 判定 · 数据 <SourceLink href={protons.source_url}>NOAA SWPC / GOES</SourceLink></>}>
      {hist ? <Unavailable>历史质子通量曲线（NOAA NCEI 档案）接入中。</Unavailable> : protons.status !== "ok" || !chart ? <Unavailable>GOES 质子通量暂不可读取。</Unavailable> : (
        <LogTimeChart title="过去三天积分质子通量" zone={zone} maxGap={15 * 60_000}
          series={[
            { label: "≥10 MeV", color: "#15803d", points: protons.series.gte10 },
            { label: "≥50 MeV", color: "#1d4ed8", points: protons.series.gte50 },
            { label: "≥100 MeV", color: "#c026d3", points: protons.series.gte100 },
          ]}
          start={chart.start} end={chart.end} floor={chart.floor} ceiling={6} yLabel={powerLabel} bands={protonBands}
          ticks={alignedTicks(chart.start, chart.end, 24 * HOUR, zone)} tickLabel={(time) => dayLabel.format(time)}
          formatValue={(value) => value.toPrecision(3)} />
      )}
    </Section>
  );
}

const liveSections = [
  { id: "sunspots", text: "太阳黑子区域", icon: Sun },
  { id: "coronal-holes", text: "日冕洞", icon: CircleDot },
  { id: "cme", text: "日冕物质抛射", icon: Orbit },
  { id: "flares", text: "太阳耀斑", icon: Zap },
  { id: "protons", text: "高能太阳质子", icon: Radiation },
] as const;
type LiveSectionId = (typeof liveSections)[number]["id"];

export function SolarLive({ zone }: { zone: string }) {
  const { data, error, isLoading, isValidating, mutate } = useSWR(solarLiveKey, fetchSolarLive, solarLiveOptions);
  const [active, setActive] = useState<LiveSectionId>("sunspots");
  const [history, setHistory] = useState<HistorySelection | null>(null);
  const [editing, setEditing] = useState(false);
  const listing = useHistoryTimes(history?.date ?? null);
  const frameFor = (key: HistoryChannelKey) => {
    if (!history || !listing.data) return null;
    const frame = nearestFrame(listing.data.channels[key]?.times ?? [], history.minutes);
    if (!frame) return null;
    return { src: historyImageUrl(key, history.date, frame.stamp), caption: `${history.date} ${formatHistoryTime(frame.stamp)}` };
  };
  const sectionIndex = (id: LiveSectionId) => liveSections.findIndex((section) => section.id === id);
  const panels: Record<LiveSectionId, (data: SolarLiveOverview) => ReactNode> = {
    sunspots: (overview) => <SunspotSection data={overview} zone={zone} hist={history ? frameFor("hmi") : null} />,
    "coronal-holes": (overview) => <CoronalHoleSection data={overview} zone={zone} hist={history ? frameFor("aia193") : null} />,
    cme: (overview) => <CmeSection data={overview} zone={zone} hist={Boolean(history)} />,
    flares: (overview) => <FlareSection data={overview} zone={zone} hist={history ? frameFor("aia131") : null} />,
    protons: (overview) => <ProtonSection data={overview} zone={zone} hist={Boolean(history)} />,
  };
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
        <span>NOAA SWPC 与 NASA SDO 实时观测 · 每分钟自动更新{data && ` · 最近读取 ${observationTime(data.generated_at, zone)}`}</span>
        <button type="button" onClick={() => void mutate()} disabled={isValidating} aria-label="刷新实时观测"
          className="research-icon-action" title="刷新实时观测">
          <RefreshCw className={cn("size-4", isValidating && "animate-spin")} aria-hidden="true" />
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {history ? (
          <>
            <span className="inline-flex items-center gap-1.5 rounded bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-900">
              <CalendarDays className="size-3.5" aria-hidden="true" />
              历史模式 · {history.date}
            </span>
            <button type="button" onClick={() => setEditing(!editing)}
              className="rounded-full border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-slate-300 hover:text-slate-900">
              {editing ? "收起时间选择" : "调整日期时刻"}
            </button>
            <button type="button" onClick={() => { setHistory(null); setEditing(false); }}
              className="inline-flex items-center gap-1 rounded-full border border-sky-700 px-2.5 py-1 text-xs font-medium text-sky-800 hover:bg-sky-50">
              <X className="size-3.5" aria-hidden="true" />
              返回实时观测
            </button>
          </>
        ) : (
          <button type="button"
            onClick={() => {
              setHistory({ date: new Date(Date.now() - 86_400_000).toISOString().slice(0, 10), minutes: 12 * 60 });
              setEditing(true);
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900">
            <CalendarDays className="size-3.5" aria-hidden="true" />
            查看历史观测
          </button>
        )}
      </div>
      {history && editing && (
        <div className="mt-3 border-y border-slate-200 py-4">
          <HistoryControls selection={history} onChange={setHistory}
            listing={listing.data} loading={listing.isLoading} loadError={listing.error} />
        </div>
      )}
      <div className="mt-5 grid items-start gap-x-8 gap-y-4 lg:grid-cols-[12.5rem_minmax(0,1fr)]">
          <div role="tablist" aria-label="实时观测" aria-orientation="vertical"
            className="flex gap-1 overflow-x-auto max-lg:border-b max-lg:border-slate-200 max-lg:pb-2 lg:sticky lg:top-4 lg:flex-col lg:self-start">
            {liveSections.map(({ id, text, icon: Icon }) => (
              <button key={id} type="button" role="tab" id={`live-tab-${id}`} aria-controls={`live-panel-${id}`} aria-selected={active === id}
                tabIndex={active === id ? 0 : -1} onClick={() => setActive(id)}
                onKeyDown={(event) => {
                  if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const index = sectionIndex(id);
                  const next = event.key === "Home" ? liveSections[0].id : event.key === "End" ? liveSections[liveSections.length - 1].id
                    : liveSections[(index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : liveSections.length - 1)) % liveSections.length].id;
                  setActive(next);
                  document.getElementById(`live-tab-${next}`)?.focus();
                }}
                className={cn(
                  "flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-md px-3 py-2 text-left text-sm font-medium transition",
                  "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700",
                  active === id && "bg-sky-100 font-semibold text-sky-950",
                )}>
                <Icon className={cn("size-4 shrink-0", active === id ? "text-sky-800" : "text-slate-400")} aria-hidden="true" />
                {text}
              </button>
            ))}
          </div>
          <div key={active} id={`live-panel-${active}`} role="tabpanel" aria-labelledby={`live-tab-${active}`} className="min-w-0">
            {data ? panels[active](data) : (
              <p role="status" className="py-6 text-sm text-slate-600">
                {isLoading ? "正在读取实时观测…" : error ? `实时观测暂不可读取：${error instanceof Error ? error.message : "未知错误"}` : "暂无实时观测。"}
              </p>
            )}
          </div>
        </div>
      <footer className="mt-8 border-t border-slate-300 pt-3 text-xs text-slate-600">
        栏目参考 <SourceLink href="https://www.spaceweatherlive.com/zh-cn/tai-yang-huo-dong.html">SpaceWeatherLive</SourceLink> · 数据与图像直接来自 NOAA SWPC、NASA SDO 与 ESA/NASA SOHO
      </footer>
    </>
  );
}
