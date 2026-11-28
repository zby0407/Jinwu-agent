"use client";

import { useState } from "react";
import useSWR from "swr";
import { AuthoritativeSolarOverview } from "@/app/components/AuthoritativeSolarOverview";
import { SolarSeriesChart } from "@/app/components/SolarSeriesChart";
import { SolarSourceLink } from "@/app/components/TodaySunProducts";
import { beijingDay, readSolarCache, solarCycleEndpoint, type CycleArchive, type SolarPoint } from "@/lib/todaySun";

const silso = "https://www.sidc.be/SILSO/datafiles";
const extrema = "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt";
const noaa = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json";
const monthGap = 45 * 86_400_000;
const blue = "#0369a1";
const amber = "#b45309";
const section = "border-t border-slate-300 pt-5";

function observations(rows: [string, number | null][], start?: string, end?: string): SolarPoint[] {
  return rows.filter(([month]) => (!start || month >= start) && (!end || month < end))
    .map(([month, value]) => [Date.parse(`${month}-01T00:00:00Z`), value]);
}

const year = (time: number) => String(new Date(time).getUTCFullYear());
const yearMonth = (time: number) => new Date(time).toISOString().slice(0, 7);

function CycleCharts({ archive }: { archive: CycleArchive }) {
  const [cycle, setCycle] = useState<number | null>(null);
  const index = archive.cycles.findIndex((entry) => entry.number === cycle);
  const selected = index < 0 ? undefined : archive.cycles[index];
  const start = selected?.start;
  const end = selected && archive.cycles[index + 1]?.start;
  const monthly = observations(archive.monthly, start, end);
  const smoothed = observations(archive.smoothed, start, end);
  const confirmed = archive.cycles.flatMap(({ number, peak }): SolarPoint[] => peak === null ? [] : [[number, peak]]);
  const ongoing = archive.cycles.flatMap(({ number, start, peak }, position): SolarPoint[] => {
    if (peak !== null) return [];
    const available = observations(archive.smoothed, start, archive.cycles[position + 1]?.start)
      .flatMap(([, value]) => value === null ? [] : [value]);
    return available.length ? [[number, Math.max(...available)]] : [];
  });
  const series = [
    { label: "月均黑子数", color: "#b91c1c", points: monthly },
    { label: "13 个月平滑", color: blue, points: smoothed },
  ];
  const lastMonth = archive.monthly.at(-1)?.[0];
  const archiveStart = archive.monthly[0]?.[0];
  const interval = selected ? (end ? `起于 ${start} · 下一周起于 ${end}` : `起于 ${start} · 截至 ${lastMonth}`) : `${archiveStart} — ${lastMonth}`;

  return <>
    <section aria-labelledby="cycle-sunspots" className={section}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 id="cycle-sunspots" className="text-xl font-semibold tracking-tight">国际太阳黑子数</h3>
          <p className="mt-1 text-sm text-slate-600">{interval}{selected?.peakAt && ` · SILSO 峰值 ${selected.peakAt}（${selected.peak}）`}</p>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          活动周
          <select className="rounded border-slate-300 bg-white py-1.5 pl-2 pr-7 text-sm"
            value={cycle ?? "all"} onChange={(event) => setCycle(event.target.value === "all" ? null : Number(event.target.value))}>
            <option value="all">全部历史</option>
            {archive.cycles.slice().reverse().map(({ number, start }) => (
              <option key={number} value={number}>第 {number} 周 · {start}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="mx-auto mt-1 max-w-6xl">
        <SolarSeriesChart key={`sunspots-${cycle}`} title="国际太阳黑子数月均与 13 个月平滑曲线"
          unit="国际黑子数" series={series} formatTime={selected ? yearMonth : year} formatDetailTime={yearMonth} maxGap={monthGap} zero />
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-600">
        最新月份可能为临时值，记录末端未发布的平滑值留空。
        {" "}<SolarSourceLink href={silso}>WDC-SILSO · 比利时皇家天文台 · V2.0</SolarSourceLink>
        {" · "}<SolarSourceLink href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</SolarSourceLink>
      </p>
    </section>

    <div className="mt-7 grid gap-7 xl:grid-cols-2">
      <section aria-labelledby="cycle-peaks" className={`min-w-0 ${section}`}>
        <h3 id="cycle-peaks" className="text-xl font-semibold tracking-tight">各活动周的峰值强度</h3>
        <p className="mt-1 text-sm text-slate-600">以 13 个月平滑黑子数峰值比较。</p>
        <SolarSeriesChart title="各太阳活动周的平滑黑子数峰值" unit="国际黑子数"
          series={[
            { label: "SILSO 已公布峰值", color: blue, points: confirmed },
            { label: "进行中活动周 · 截至档案最高", color: amber, points: ongoing },
          ]} formatTime={(number) => `第 ${Math.round(number)} 周`}
          maxGap={0.55} zero bars />
        <p className="mt-3 text-xs leading-5 text-slate-600"><SolarSourceLink href={extrema}>WDC-SILSO 活动周极值表</SolarSourceLink></p>
      </section>

      <section aria-labelledby="cycle-radio" className={`min-w-0 ${section}`}>
        <h3 id="cycle-radio" className="text-xl font-semibold tracking-tight">历史 F10.7 月均通量</h3>
        <p className="mt-1 text-sm text-slate-600">随活动周筛选，使用实际观测覆盖。</p>
        <SolarSeriesChart key={`radio-${cycle}`} title="历史 F10.7 月均通量" unit="sfu"
          series={[{ label: "月均 F10.7", color: amber, points: observations(archive.f107, start, end) }]}
          formatTime={selected ? yearMonth : year} formatDetailTime={yearMonth} maxGap={monthGap} />
        <p className="mt-3 text-xs leading-5 text-slate-600">
          档案覆盖 {archive.f107[0]?.[0]} — {archive.f107.at(-1)?.[0]} · <SolarSourceLink href={noaa}>NOAA SWPC 观测指数</SolarSourceLink>
        </p>
      </section>
    </div>
  </>;
}

export function SolarCycle() {
  const [showOverview, setShowOverview] = useState(false);
  const { data, error, isLoading } = useSWR(solarCycleEndpoint, readSolarCache<CycleArchive>, {
    shouldRetryOnError: false,
    revalidateOnFocus: false,
  });
  return <>
    <p className="mb-5 text-sm text-slate-600">
      历史档案{data && ` · 冻结于 ${beijingDay(new Date(data.frozenAt))}`}
    </p>
    {data
      ? <CycleCharts archive={data} />
      : <p role="status" className={`${section} text-sm text-slate-700`}>
        {isLoading ? "正在读取本地历史档案…" : error ? "历史档案暂不可读取。" : "暂无本地历史档案。"}
      </p>}
    <details className={`mt-8 ${section}`} onToggle={(event) => setShowOverview(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm font-medium text-sky-800">查询权威数据表</summary>
      {showOverview && <AuthoritativeSolarOverview />}
    </details>
    <footer className={`mt-8 ${section} text-xs text-slate-600`}>
      栏目参考 <SolarSourceLink href="https://www.spaceweatherlive.com/zh-cn/tai-yang-huo-dong/tai-yang-zhou-qi.html">SpaceWeatherLive 太阳周期</SolarSourceLink>
    </footer>
  </>;
}
