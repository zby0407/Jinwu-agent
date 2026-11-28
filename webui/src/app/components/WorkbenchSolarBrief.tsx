"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import useSWR from "swr";
import { SolarSourceLink } from "@/app/components/SolarSourceLink";
import { fetchSolarLive, liveImageUrl, solarLiveKey, solarLiveOptions } from "@/lib/solarLive";
import { observationTime } from "@/lib/todaySun";

const diskChannels = ["hmi", "aia193", "aia131"] as const;

export function WorkbenchSolarBrief({ backendReady, onView }: {
  backendReady: boolean;
  onView: (view: string) => void;
}) {
  const { data, isLoading } = useSWR(backendReady ? solarLiveKey : null, fetchSolarLive, solarLiveOptions);
  const regions = data?.regions.status === "ok" ? data.regions : null;
  const visible = regions?.rows.filter(region => region.disk.visible) ?? [];
  const examples = visible.slice(0, 3).map(region => `AR ${region.number}`).join("、");
  const xray = data?.xray.status === "ok" ? data.xray : null;
  const protons = data?.protons.status === "ok" ? data.protons : null;
  const channel = diskChannels.find(key => data?.images[key]?.status === "ok");
  const image = channel ? data?.images[channel] : undefined;
  const imageMeta = image?.status === "ok" ? image : null;
  const imageTime = imageMeta?.observed_at ? `${observationTime(imageMeta.observed_at, "UTC")} UTC` : "观测时间待公布";
  const protonFlux = protons?.latest_gte10;
  const metrics = [
    { label: "可见活动区", value: regions ? String(visible.length) : "—" },
    { label: "当前 X 射线级别", value: xray?.current.class ?? "—" },
    { label: "≥10 MeV 质子通量", value: protonFlux == null ? "—" : Number(protonFlux.toPrecision(3)).toString(), unit: protonFlux == null ? undefined : "pfu" },
  ];
  const peak = xray?.max_24h.class;

  return (
    <section className="workbench-section" aria-labelledby="workbench-solar-title">
      <div className="workbench-section-header">
        <h3 id="workbench-solar-title">今日太阳</h3>
        <Link href="/?view=solar" className="workbench-action" onNavigate={event => {
          event.preventDefault();
          onView("solar");
        }}>
          查看完整监测 <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      </div>
      <div className="workbench-solar-layout">
        <figure className="min-w-0">
          <div className="workbench-solar-plate">
            {channel && imageMeta ? (
              <>
                <Image src={liveImageUrl(channel, imageMeta)} alt={`${imageMeta.label}太阳全日面观测`} fill unoptimized className="object-contain pb-8" />
                <span aria-hidden="true" className="workbench-solar-stamp">
                  <span>{imageMeta.label}</span>
                  <span>{imageTime}</span>
                </span>
              </>
            ) : <span role="status" className="text-sm text-slate-400">{isLoading ? "正在加载全日面图像…" : "暂无全日面图像"}</span>}
          </div>
          {imageMeta && (
            <figcaption className="workbench-source">
              图像 <SolarSourceLink href={imageMeta.source_url}>{imageMeta.credit}</SolarSourceLink>
              <span className="sr-only"> · {imageMeta.label} · {imageTime}</span>
            </figcaption>
          )}
        </figure>
        <div className="min-w-0">
          <dl className="workbench-solar-metrics">
            {metrics.map(({ label, value, unit }) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd className="font-mono">{value}{unit && <span>{unit}</span>}</dd>
              </div>
            ))}
          </dl>
          <ul className="workbench-solar-notes">
            <li>{peak ? `GOES 过去 24 小时 X 射线峰值为 ${peak} 级。` : "过去 24 小时 X 射线峰值暂无数据。"}</li>
            <li>{regions
              ? `NOAA ${regions.report_date} UTC 报告中，${visible.length} 个活动区位于可见日面${examples ? `（如 ${examples}）` : ""}。`
              : "NOAA 活动区报告暂无数据。"}</li>
            <li><strong>观测建议</strong>用 HMI 连续谱核对黑子位置，再对照 AIA 193 Å 查看周围日冕结构。</li>
          </ul>
          {(xray || regions || protons) && (
            <div className="workbench-source flex flex-wrap gap-x-3 gap-y-1">
              <span>数据来源</span>
              {xray && <SolarSourceLink href={xray.source_url}>NOAA SWPC · GOES X 射线</SolarSourceLink>}
              {regions && <SolarSourceLink href={regions.source_url}>NOAA SWPC · 活动区报告</SolarSourceLink>}
              {protons && <SolarSourceLink href={protons.source_url}>NOAA SWPC · GOES 质子</SolarSourceLink>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
