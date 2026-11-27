"use client";

import { useState } from "react";
import { SolarSeriesChart } from "@/app/components/SolarSeriesChart";
import { beijingDay, latestPoint, observationTime, todaySunEndpoint, type ProductImageKey, type SolarProducts } from "@/lib/todaySun";

const swpc = "https://services.swpc.noaa.gov/";
const hour = 3_600_000;
const blue = "#0369a1";
const amber = "#b45309";
const subsection = "mt-7 border-t border-slate-300 pt-5";
const heading = "text-lg font-semibold tracking-tight text-slate-900";
const chartNote = "mt-2 text-xs leading-5 text-slate-600";

export function SolarSourceLink({ href, children }: { href: string; children: string }) {
  return <a href={href} target="_blank" rel="noreferrer" className="text-sky-800 underline decoration-sky-300 underline-offset-2 hover:text-sky-950">{children}</a>;
}

function CacheStamp({ cachedAt, date, zone }: { cachedAt: string; date: string; zone: string }) {
  return <span> · {beijingDay(new Date(cachedAt)) < date ? "沿用缓存 " : "缓存 "}{observationTime(cachedAt, zone)}</span>;
}

const maps = {
  synoptic: { label: "日冕洞", credit: "NOAA SWPC 综合观测图", source: "https://www.spaceweather.gov/products/solar-synoptic-map", detail: "图上标出日冕洞边界与极性，制图时间见原图。" },
  coronagraph: { label: "日冕仪", credit: "NOAA GOES-19 / CCOR-1", source: "https://www.spaceweather.gov/products/coronagraph", detail: "外日冕参考影像，影像与事件目录各有独立时间。" },
  farSide: { label: "太阳背面", credit: "NSO / GONG", source: "https://farside.nso.edu/", detail: "利用声波传播时间估计背面磁活动的日震反演图。" },
} as const;

export function SolarContextImages({ products, date, zone }: { products: SolarProducts; date: string; zone: string }) {
  const [selected, setSelected] = useState<ProductImageKey>("synoptic");
  const product = products[selected];
  return <section className={subsection} aria-labelledby="solar-context-maps">
    <h3 id="solar-context-maps" className={heading}>日冕与太阳背面</h3>
    <div role="group" aria-label="观测图类型" className="mt-3 flex flex-wrap gap-x-1 border-b border-slate-300">
      {(Object.keys(maps) as ProductImageKey[]).map((key) => <button
        type="button" key={key} aria-pressed={selected === key} onClick={() => setSelected(key)}
        className="border-b-2 border-transparent px-3 py-2 text-sm text-slate-600 hover:bg-sky-50 aria-pressed:border-sky-700 aria-pressed:font-semibold aria-pressed:text-sky-900"
      >{maps[key].label}</button>)}
    </div>
    {product ? <figure className="mt-3">
      <div className="grid min-h-40 place-items-center border-y border-slate-200 bg-white">
        <img className="block max-h-[29rem] max-w-full object-contain" loading="lazy"
          src={`${todaySunEndpoint}?image=${selected}&at=${encodeURIComponent(product.observedAt ?? product.cachedAt)}`}
          alt={`${maps[selected].credit} · ${maps[selected].label}`} />
      </div>
      <figcaption className={chartNote}>
        <span>{maps[selected].detail} </span>
        <SolarSourceLink href={maps[selected].source}>{maps[selected].credit}</SolarSourceLink>
        {" · "}<SolarSourceLink href={product.url}>原图</SolarSourceLink>
        {product.observedAt ? ` · 观测 ${observationTime(product.observedAt, zone)}` : ""}
        {!product.observedAt && product.modifiedAt ? ` · 源文件更新 ${observationTime(product.modifiedAt, zone)}` : ""}
        <CacheStamp cachedAt={product.cachedAt} date={date} zone={zone} />
      </figcaption>
    </figure> : <p className={chartNote}>此产品暂无本地观测图。</p>}
  </section>;
}

export function SolarRadioFlux({ product, date, zone }: { product: SolarProducts["f107"]; date: string; zone: string }) {
  const latest = product && latestPoint(product.points);
  return <section className={subsection} aria-labelledby="solar-f107">
    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
      <h3 id="solar-f107" className={heading}>F10.7 太阳射电通量</h3>
      {latest && <p className="text-sm font-semibold tabular-nums text-sky-900">{latest[1]} sfu</p>}
    </div>
    {product ? <>
      <SolarSeriesChart title="近 30 日 F10.7 射电通量" unit="sfu" series={[{ label: "日值", color: blue, points: product.points }]}
        formatTime={(time) => observationTime(time, zone).split(" ")[0]} formatDetailTime={(time) => observationTime(time, zone)} maxGap={36 * hour} />
      <p className={chartNote}>近 30 日{latest && ` · 最新观测 ${observationTime(latest[0], zone)}`}
        {" · "}<SolarSourceLink href={swpc + "products/10cm-flux-30-day.json"}>NOAA SWPC</SolarSourceLink>
        <CacheStamp cachedAt={product.cachedAt} date={date} zone={zone} />
      </p>
    </> : <p className={chartNote}>此产品暂无本地数据。</p>}
  </section>;
}

export function SolarCmeEvents({ product, date, zone }: { product: SolarProducts["cme"]; date: string; zone: string }) {
  const maxSpeed = Math.max(1, ...(product?.events.map((event) => event.speed) ?? []));
  return <section className={subsection} aria-labelledby="solar-cme">
    <h3 id="solar-cme" className={heading}>近期日冕物质抛射</h3>
    {product ? <>
      <p className={chartNote}>自动探测 · 目录发布 {observationTime(product.issuedAt, zone)}</p>
      <div className="mt-3 max-h-60 overflow-auto">
        <table className="w-full whitespace-nowrap text-left text-sm tabular-nums">
          <thead className="sticky top-0 bg-slate-100 text-xs text-slate-700"><tr>
            <th scope="col" className="px-2 py-2">首次探测</th><th scope="col" className="px-2 py-2">角宽</th>
            <th scope="col" className="px-2 py-2">中位估计速度 (km/s)</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-200">
            {product.events.map((event) => <tr key={`${event.at}-${event.angle}-${event.speed}`}>
              <td className="px-2 py-2">{observationTime(event.at, zone)}</td>
              <td className="px-2 py-2">{event.angle}°</td>
              <td className="px-2 py-2"><span className="inline-flex min-w-24 items-center gap-2">
                <span className="h-1.5 bg-sky-600" style={{ width: `${event.speed / maxSpeed * 76}px` }} aria-hidden="true" />
                {event.speed}
              </span></td>
            </tr>)}
            {!product.events.length && <tr><td colSpan={3} className="px-2 py-2">这一期目录未列出 CME。</td></tr>}
          </tbody>
        </table>
      </div>
      <p className={chartNote}>
        <SolarSourceLink href="https://www.sidc.be/cactus/">SIDC / CACTus 自动目录</SolarSourceLink>{ " · " }
        <SolarSourceLink href="https://www.spaceweather.gov/products/wsa-enlil-solar-wind-prediction">NOAA 到达预报</SolarSourceLink>
        <CacheStamp cachedAt={product.cachedAt} date={date} zone={zone} />
      </p>
    </> : <p className={chartNote}>此产品暂无本地数据。</p>}
  </section>;
}

const windMetrics = {
  speed: { label: "质子速度", unit: "km/s", file: "rtsw_wind_1m.json" },
  density: { label: "质子数密度", unit: "cm⁻³", file: "rtsw_wind_1m.json" },
  bz: { label: "磁场 Bz（GSM）", unit: "nT", file: "rtsw_mag_1m.json" },
} as const;

export function SolarEnvironment({ products, date, zone }: { products: SolarProducts; date: string; zone: string }) {
  const [metric, setMetric] = useState<keyof typeof windMetrics>("speed");
  const { protons, wind, magnetic, kp } = products;
  const plasma = metric === "bz" ? magnetic : wind;
  const selected = metric === "bz" ? magnetic?.bz : wind?.[metric];
  const windEnd = selected && latestPoint(selected);
  const kpEnd = kp && latestPoint(kp.points);
  const protonEnd = protons && latestPoint(protons.gte10);
  const { label, unit, file } = windMetrics[metric];
  const clock = (time: number) => observationTime(time, zone, false);
  const datetime = (time: number) => observationTime(time, zone);
  return <section className="mt-8 border-t border-slate-300 pt-5" aria-labelledby="solar-environment">
    <h3 id="solar-environment" className="text-xl font-semibold tracking-tight text-slate-900">近地空间天气</h3>
    <div className="mt-4 grid gap-8 lg:grid-cols-2">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className={heading}>太阳风与行星际磁场</h4>
          <select aria-label="太阳风指标" value={metric} onChange={(event) => setMetric(event.target.value as keyof typeof windMetrics)}
            className="rounded border-slate-300 bg-white py-1 pl-2 pr-7 text-sm">
            {Object.entries(windMetrics).map(([key, item]) => <option value={key} key={key}>{item.label}</option>)}
          </select>
        </div>
        {plasma && selected ? <>
          <SolarSeriesChart title={`近 24 小时${label}`} unit={unit} series={[{ label, color: blue, points: selected }]}
            formatTime={clock} formatDetailTime={datetime} maxGap={2 * 60_000} zero={metric === "bz"} />
          <p className={chartNote}>主用航天器 {plasma.sources.join(" / ")}{windEnd && ` · 观测截止 ${datetime(windEnd[0])}`}
            {" · "}<SolarSourceLink href={swpc + "json/rtsw/" + file}>NOAA SWPC RTSW</SolarSourceLink>
            <CacheStamp cachedAt={plasma.cachedAt} date={date} zone={zone} />
          </p>
        </> : <p className={chartNote}>此指标暂无本地数据。</p>}
      </div>
      <div className="min-w-0">
        <h4 className={heading}>GOES 高能质子</h4>
        {protons ? <>
          <SolarSeriesChart title="近 24 小时 GOES 积分质子通量" unit="pfu" series={[
            { label: "≥10 MeV", color: blue, points: protons.gte10 },
            { label: "≥100 MeV", color: amber, points: protons.gte100 },
          ]} formatTime={clock} formatDetailTime={datetime} maxGap={10 * 60_000} log />
          <p className={chartNote}>{protonEnd && `观测截止 ${datetime(protonEnd[0])} · `}
            <SolarSourceLink href={swpc + "json/goes/primary/integral-protons-1-day.json"}>NOAA SWPC / GOES</SolarSourceLink>
            <CacheStamp cachedAt={protons.cachedAt} date={date} zone={zone} />
          </p>
        </> : <p className={chartNote}>此产品暂无本地数据。</p>}
      </div>
      <div className="min-w-0 lg:col-span-2 lg:max-w-3xl">
        <h4 className={heading}>行星地磁指数 Kp</h4>
        {kp ? <>
          <SolarSeriesChart title="近 72 小时估计 Kp" unit="Kp 指数" series={[{ label: "三小时估计 Kp", color: blue, points: kp.points }]}
            formatTime={datetime} maxGap={3 * hour} zero bars />
          <p className={chartNote}>{kpEnd && `最新时段起点 ${datetime(kpEnd[0])} · `}
            <SolarSourceLink href={swpc + "products/noaa-planetary-k-index.json"}>NOAA SWPC</SolarSourceLink>
            <CacheStamp cachedAt={kp.cachedAt} date={date} zone={zone} />
          </p>
        </> : <p className={chartNote}>此产品暂无本地数据。</p>}
      </div>
    </div>
  </section>;
}
