"use client";

import { useState } from "react";
import { ArrowUpRight, Sun } from "lucide-react";
import useSWR from "swr";
import {
  SolarCmeEvents,
  SolarContextImages,
  SolarEnvironment,
  SolarRadioFlux,
  SolarSourceLink as SourceLink,
} from "@/app/components/TodaySunProducts";
import {
  beijingDay,
  latestPoint,
  observationTime,
  readSolarCache,
  solarChannels,
  strongestRecentFlare,
  todaySunEndpoint,
  type SolarChannel,
  type TodaySunSnapshot,
} from "@/lib/todaySun";

const goesDirectory = "https://services.swpc.noaa.gov/json/goes/primary/";
const regionSource = "https://services.swpc.noaa.gov/json/solar_regions.json";
const sdoCredit = "https://sdo.gsfc.nasa.gov/resources/press.php";
const panelHeading = "text-lg font-semibold tracking-tight text-slate-900";
const tableHeading = "sticky top-0 border-y border-slate-300 bg-slate-100 text-xs text-slate-700";
const cell = "px-2 py-2";

function useTodaySun() {
  return useSWR(todaySunEndpoint, readSolarCache<TodaySunSnapshot>, { shouldRetryOnError: false });
}

export function TodaySunBrief({ onOpen }: { onOpen: () => void }) {
  const { data, error, isLoading } = useTodaySun();
  const strongest = data ? strongestRecentFlare(data) : undefined;
  const radioFlux = data?.products?.f107 && latestPoint(data.products.f107.points);

  return (
    <section aria-labelledby="today-sun-brief" className="min-w-0 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h3 id="today-sun-brief" className="inline-flex items-center gap-2 font-semibold">
          <Sun className="size-5 text-sky-700" aria-hidden="true" /> 今日太阳
        </h3>
        <button type="button" onClick={onOpen} className="inline-flex items-center gap-1 text-sm font-medium text-sky-800 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700">
          查看太阳活动详情 <ArrowUpRight className="size-4" aria-hidden="true" />
        </button>
      </div>
      {data ? (
        <>
          <p className="mt-3 text-sm leading-6 text-slate-700">
            {strongest?.peakAt ? `快照过去 24 小时最强耀斑为 ${strongest.grade}，峰值于 ${observationTime(strongest.peakAt)}。` : "快照过去 24 小时无已定级耀斑。"}
            {" NOAA 最新报告列出 "}{data.regions.rows.length} 个活动区。
          </p>
          {radioFlux && <p className="mt-1 text-sm text-slate-700">
            <SourceLink href="https://services.swpc.noaa.gov/products/10cm-flux-30-day.json">F10.7</SourceLink>
            {" "}<span className="font-medium tabular-nums">{radioFlux[1]} sfu</span>
            {" · "}<span className="text-xs text-slate-600">{observationTime(radioFlux[0])}</span>
          </p>}
          <p className="mt-3 text-xs text-slate-600">
            快照 {data.date} {observationTime(data.fetchedAt, "Asia/Shanghai", false)} 北京时间
            {" · "}<SourceLink href={goesDirectory}>NOAA SWPC</SourceLink>
          </p>
        </>
      ) : (
        <p role="status" className="mt-1 text-sm text-slate-700">
          {isLoading ? "正在读取观测快照…" : error ? "观测快照暂不可读取。" : "暂无观测快照。"}
        </p>
      )}
    </section>
  );
}

function SolarImages({ images, zone }: { images: TodaySunSnapshot["images"]; zone: string }) {
  const [channel, setChannel] = useState<SolarChannel>("0193");
  const frame = images.find((image) => image.channel === channel) ?? images[0];
  if (!frame) return <p className="text-sm text-slate-700">此快照没有太阳图像。</p>;
  const { label, instrument, description } = solarChannels[frame.channel];

  return (
    <section aria-labelledby="today-sun-disc">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 id="today-sun-disc" className={panelHeading}>太阳圆面</h3>
        <span className="text-sm text-slate-600">{instrument} · {description}</span>
      </div>
      <figure className="mt-3">
        <div className="grid min-h-[12rem] place-items-center bg-black">
          <img src={`${todaySunEndpoint}?image=${frame.channel}&at=${encodeURIComponent(frame.observedAt)}`} alt={`${instrument} ${label}太阳圆面`} width={512} height={512} className="block h-auto w-full max-w-[512px]" />
        </div>
        <div role="group" aria-label="太阳图像波段" className="flex flex-wrap border-b border-slate-200">
          {(Object.keys(solarChannels) as SolarChannel[]).map((id) => (
            <button key={id} type="button" onClick={() => setChannel(id)} disabled={!images.some(image => image.channel === id)}
              aria-pressed={frame.channel === id}
              className="border-b-2 border-transparent px-3 py-2 text-sm text-slate-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-sky-700 disabled:opacity-40 aria-pressed:border-sky-700 aria-pressed:font-semibold aria-pressed:text-sky-900">
              {solarChannels[id].label}
            </button>
          ))}
        </div>
        <figcaption className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs leading-5 text-slate-600">
          <span>观测 {observationTime(frame.observedAt, zone)}</span>
          <span>图像 <SourceLink href={sdoCredit}>{`NASA/${instrument} 科学团队`}</SourceLink> · <SourceLink href={frame.url}>原图</SourceLink></span>
        </figcaption>
      </figure>
    </section>
  );
}

function XrayChart({ flux, zone }: { flux: TodaySunSnapshot["flux"]; zone: string }) {
  const start = Date.parse(flux.startAt);
  const end = Date.parse(flux.endAt);
  const left = 37;
  const right = 570;
  const top = 12;
  const bottom = 178;
  const levels = flux.points.flatMap(([, value]) => value === null ? [] : [Math.log10(value)]);
  const floor = Math.min(-8, Math.max(-12, Math.floor(Math.min(...levels))));
  const ceiling = Math.max(-4, Math.ceil(Math.max(...levels)));
  const x = (time: number) => left + (time - start) / (end - start) * (right - left);
  const y = (power: number) => bottom - (power - floor) / (ceiling - floor) * (bottom - top);
  let previous: number | undefined;
  const path = flux.points.map(([time, value]) => {
    if (value === null) {
      previous = undefined;
      return "";
    }
    const command = previous !== undefined && time - previous <= 90_000 ? "L" : "M";
    previous = time;
    return `${command}${x(time).toFixed(2)} ${y(Math.max(floor, Math.log10(value))).toFixed(2)}`;
  }).join(" ");

  return (
    <section aria-labelledby="today-sun-xray" className="mt-8 border-t border-slate-300 pt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 id="today-sun-xray" className={panelHeading}>GOES X 射线</h3>
        <span className="text-sm text-slate-600">0.1–0.8 nm · W/m² · 对数刻度</span>
      </div>
      <svg viewBox="0 0 590 209" className="mt-4 block w-full overflow-visible text-slate-600" role="img" aria-label="快照过去 24 小时 GOES X 射线通量曲线">
        {Array.from({ length: ceiling - floor + 1 }, (_, index) => {
          const power = floor + index;
          return <g key={power}>
            <line x1={left} x2={right} y1={y(power)} y2={y(power)} stroke="#cbd5e1" />
            <text x={left - 8} y={y(power) + 4} textAnchor="end" fill="currentColor" fontSize="14">
              {"ABCMX"[power + 8] ?? (power > -4 ? `X${10 ** (power + 4)}` : `1e${power}`)}
            </text>
          </g>;
        })}
        {[0, 6, 12, 18, 24].map((hours, index) => {
          const time = start + hours * 3_600_000;
          return <g key={hours}>
            <line x1={x(time)} x2={x(time)} y1={top} y2={bottom} stroke="#dbe5ee" />
            <text x={x(time)} y={bottom + 22} textAnchor={index === 0 ? "start" : index === 4 ? "end" : "middle"} fill="currentColor" fontSize="14">
              {observationTime(time, zone, false)}
            </text>
          </g>;
        })}
        <path d={path} fill="none" stroke="#0369a1" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
      <p className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs leading-5 text-slate-600">
        <span>观测 {observationTime(flux.startAt, zone)} – {observationTime(flux.endAt, zone)}</span>
        <span>数据 <SourceLink href={`${goesDirectory}xrays-1-day.json`}>NOAA SWPC / GOES</SourceLink></span>
      </p>
    </section>
  );
}

export function TodaySun({ zone }: { zone: string }) {
  const { data, error, isLoading } = useTodaySun();
  const strongest = data ? strongestRecentFlare(data) : undefined;
  const older = data && data.date < beijingDay(new Date());
  const products = data?.products ?? {};

  return (
    <>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
            <span>公开观测数据 · 每日快照</span>
            {data && <span><span className={older ? "font-medium text-amber-800" : undefined}>{older ? "历史快照" : "缓存写入"}</span> {observationTime(data.fetchedAt, zone)}</span>}
          </p>

        {data ? <>
          <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] lg:gap-0">
            <div className="min-w-0 lg:pr-7">
              <SolarImages images={data.images} zone={zone} />
              <XrayChart flux={data.flux} zone={zone} />
              <SolarContextImages products={products} date={data.date} zone={zone} />
            </div>
            <div className="min-w-0 border-t border-slate-300 pt-5 lg:border-l lg:border-t-0 lg:pl-7 lg:pt-0">
              <section aria-labelledby="today-sun-flares">
                <h3 id="today-sun-flares" className={panelHeading}>太阳耀斑</h3>
                <p className="mt-1 text-sm leading-5 text-slate-700">
                  {strongest?.peakAt ? `快照过去 24 小时最强 ${strongest.grade}，峰值 ${observationTime(strongest.peakAt, zone)}。` : "快照过去 24 小时无已定级耀斑。"}
                </p>
                <div className="mt-3 max-h-72 overflow-auto">
                  <table className="w-full whitespace-nowrap text-left text-sm">
                    <caption className="sr-only">最近 7 天 GOES 耀斑事件</caption>
                    <thead className={tableHeading}><tr>
                      {["等级", "开始", "峰值", "结束"].map(label => <th key={label} className={cell} scope="col">{label}</th>)}
                    </tr></thead>
                    <tbody className="divide-y divide-slate-200">
                      {data.flares.map((event) => <tr key={event.startAt} className="tabular-nums">
                        <th scope="row" className={`${cell} font-semibold text-sky-900`}>{event.grade ?? "—"}</th>
                        <td className={cell}>{observationTime(event.startAt, zone)}</td>
                        <td className={cell}>{event.peakAt ? observationTime(event.peakAt, zone) : "—"}</td>
                        <td className={cell}>{event.endAt ? observationTime(event.endAt, zone) : "—"}</td>
                      </tr>)}
                      {!data.flares.length && <tr><td colSpan={4} className="px-2 py-3 text-slate-600">最近 7 天未列出耀斑事件。</td></tr>}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-600">近 7 天 · 数据 <SourceLink href={`${goesDirectory}xray-flares-7-day.json`}>NOAA SWPC / GOES</SourceLink></p>
              </section>

              <section aria-labelledby="today-sun-regions" className="mt-7 border-t border-slate-300 pt-5">
                <h3 id="today-sun-regions" className={panelHeading}>太阳活动区</h3>
                <p className="mt-1 text-sm text-slate-700">NOAA 报告日期 {data.regions.reportDate} UTC · {data.regions.rows.length} 个区域</p>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full whitespace-nowrap text-left text-sm">
                    <thead className={tableHeading}><tr>
                      {["区号", "位置", "黑子", "面积 (µHem)", "黑子分类", "磁分类"].map(label => <th key={label} className={cell} scope="col">{label}</th>)}
                    </tr></thead>
                    <tbody className="divide-y divide-slate-200">
                      {data.regions.rows.map((region) => <tr key={region.number} className="tabular-nums">
                        <th scope="row" className={`${cell} font-semibold text-sky-900`}>{region.number}</th>
                        <td className={cell}>{region.location}</td><td className={cell}>{region.spots ?? "—"}</td>
                        <td className={cell}>{region.area ?? "—"}</td><td className={cell}>{region.spotClass || "—"}</td>
                        <td className={cell}>{region.magneticClass || "—"}</td>
                      </tr>)}
                      {!data.regions.rows.length && <tr><td colSpan={6} className="px-2 py-3 text-slate-600">此报告未列出带位置的活动区。</td></tr>}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-600">数据 <SourceLink href={regionSource}>NOAA SWPC / Solar Regions</SourceLink></p>
              </section>
              <SolarRadioFlux product={products.f107} date={data.date} zone={zone} />
              <SolarCmeEvents product={products.cme} date={data.date} zone={zone} />
            </div>
          </div>
          <SolarEnvironment products={products} date={data.date} zone={zone} />
        </> : (
          <p role="status" className="mt-6 text-sm text-slate-700">
            {isLoading ? "正在读取本地观测快照…" : error ? "本地观测快照暂不可读取。" : "暂无本地观测快照，等待数据更新。"}
          </p>
        )}
        <footer className="mt-8 border-t border-slate-300 pt-3 text-xs text-slate-600">
          栏目参考 <SourceLink href="https://www.spaceweatherlive.com/zh-cn/tai-yang-huo-dong.html">SpaceWeatherLive</SourceLink>
        </footer>
    </>
  );
}
