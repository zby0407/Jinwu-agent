"use client";

import { useState } from "react";
import type { SolarPoint } from "@/lib/todaySun";

type Series = { label: string; color: string; points: SolarPoint[]; dash?: string };
type Props = {
  title: string;
  unit: string;
  series: Series[];
  formatTime: (timestamp: number) => string;
  formatDetailTime?: (timestamp: number) => string;
  maxGap: number;
  log?: boolean;
  zero?: boolean;
  bars?: boolean;
};

const width = 640;
const height = 230;
const left = 49;
const right = 620;
const top = 13;
const bottom = 191;

function closest(points: SolarPoint[], at: number) {
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (points[mid][0] < at) lo = mid + 1;
    else hi = mid;
  }
  const before = points[lo - 1];
  const after = points[lo];
  return !after || (before && at - before[0] < after[0] - at) ? before : after;
}

export function SolarSeriesChart({ title, unit, series, formatTime, formatDetailTime = formatTime, maxGap, log, zero, bars }: Props) {
  const [cursor, setCursor] = useState<number | null>(null);
  const points = series.flatMap((item) => item.points);
  const project = (value: number) => log ? Math.log10(value) : value;
  const values = points.flatMap(([, value]) => value === null || (log && value <= 0) ? [] : [project(value)]);
  if (values.length === 0) return <p className="my-4 text-sm text-slate-600">所选时间段无有效观测。</p>;

  const start = Math.min(...points.map(([time]) => time));
  const end = Math.max(...points.map(([time]) => time));
  const spread = Math.max(end - start, 1);
  const max = Math.max(...values);
  const min = zero ? Math.min(0, ...values) : Math.min(...values);
  const step = 10 ** Math.floor(Math.log10(Math.max((max - min) / 3, 0.001)));
  const gridStep = log ? Math.max(1, Math.ceil((max - min) / 5)) : step * Math.ceil((max - min) / (step * 4) || 1);
  const lower = Math.floor(min / gridStep) * gridStep;
  const upper = Math.max(lower + gridStep, Math.ceil(max / gridStep) * gridStep);
  const x = (time: number) => start === end ? (left + right) / 2 : left + (time - start) / spread * (right - left);
  const y = (value: number) => bottom - (value - lower) / (upper - lower) * (bottom - top);
  const barWidth = Math.min(26, (right - left) / Math.max(1, points.length) * 0.5);
  const shownCursor = cursor === null ? null : Math.min(end, Math.max(start, cursor));
  const number = (value: number) => Number(value.toPrecision(4)).toString();
  const yTicks = Array.from({ length: Math.round((upper - lower) / gridStep) + 1 }, (_, index) => {
    const level = lower + gridStep * index;
    return { position: y(level), label: number(log ? 10 ** level : level) };
  });
  const xTicks = (start === end ? [0] : [0, 1, 2, 3, 4]).map((index) => {
    const stamp = start + spread * index / 4;
    return { position: x(stamp), label: formatTime(stamp) };
  });

  return (
    <figure className="mt-2">
      <div className="relative">
      <svg className="block w-full overflow-visible text-slate-600" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}
        onPointerLeave={() => setCursor(null)}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const fraction = ((event.clientX - bounds.left) / bounds.width * width - left) / (right - left);
          setCursor(start + Math.max(0, Math.min(1, fraction)) * spread);
        }}>
        {yTicks.map(({ position }) => <line key={position} x1={left} x2={right} y1={position} y2={position} stroke="#d4dee8" />)}
        {xTicks.map(({ position }) => <line key={position} x1={position} x2={position} y1={top} y2={bottom} stroke="#e0e8ef" />)}
        {series.map((item) => {
          if (bars) return <g key={item.label}>{item.points.map(([time, value]) => value !== null && <rect key={time} fill={item.color} x={x(time) - barWidth / 2} y={y(value)} width={barWidth} height={bottom - y(value)} />)}</g>;
          let previous: number | undefined;
          const path = item.points.map(([time, value]) => {
            if (value === null || (log && value <= 0)) {
              previous = undefined;
              return "";
            }
            const command = previous === undefined || time - previous > maxGap ? "M" : "L";
            previous = time;
            return `${command}${x(time).toFixed(2)} ${y(project(value)).toFixed(2)}`;
          }).join(" ");
          return <path key={item.label} d={path} fill="none" stroke={item.color} strokeWidth="1.8" strokeDasharray={item.dash} vectorEffect="non-scaling-stroke" />;
        })}
        {shownCursor !== null && <line x1={x(shownCursor)} x2={x(shownCursor)} y1={top} y2={bottom} stroke="#64748b" strokeDasharray="3 3" />}
      </svg>
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 whitespace-nowrap text-[10px] leading-none tabular-nums text-slate-600 sm:text-[12px]">
          {yTicks.map(({ position, label }) => <span key={position}
            className="absolute -translate-x-full -translate-y-1/2 pr-1.5"
            style={{ left: `${left / width * 100}%`, top: `${position / height * 100}%` }}
          >{label}</span>)}
          {xTicks.map(({ position, label }, index) => <span key={position}
            className="absolute"
            style={{
              left: `${position / width * 100}%`,
              top: `${(bottom + 12) / height * 100}%`,
              transform: `translateX(${xTicks.length === 1 ? -50 : index === 0 ? 0 : index === xTicks.length - 1 ? -100 : -50}%)`,
            }}
          >{label}</span>)}
        </div>
      </div>
      <figcaption className="mt-2 grid gap-1.5 text-xs leading-5 text-slate-600">
        <ul aria-label="图例" className="flex flex-wrap gap-x-4 gap-y-1 !m-0 !p-0">
          {series.map((item) => {
            const entry = shownCursor === null ? undefined : closest(item.points, shownCursor);
            const value = entry && shownCursor !== null && Math.abs(entry[0] - shownCursor) <= maxGap ? entry[1] : null;
            return <li key={item.label} className="inline-flex items-center gap-1.5 !mb-0">
              <span className="h-0.5 w-3 shrink-0" style={{ backgroundColor: item.color }} aria-hidden="true" />
              <span>{item.label}{shownCursor !== null && ` ${value === null ? "—" : number(value)}`}</span>
            </li>;
          })}
        </ul>
        <p className="flex flex-wrap justify-between gap-x-3">
          <span>纵轴 · {unit}{log ? " · 对数刻度" : ""}</span>
          {shownCursor !== null && <time>{formatDetailTime(shownCursor)}</time>}
        </p>
      </figcaption>
    </figure>
  );
}
