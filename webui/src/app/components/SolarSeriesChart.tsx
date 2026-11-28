"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
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
  minZoomSpan?: number;
};

const width = 640;
const height = 230;
const left = 49;
const right = 620;
const top = 13;
const bottom = 191;

function lowerBound(points: SolarPoint[], at: number) {
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (points[mid][0] < at) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function closest(points: SolarPoint[], at: number) {
  const index = lowerBound(points, at);
  const before = points[index - 1];
  const after = points[index];
  return !after || (before && at - before[0] < after[0] - at) ? before : after;
}

export function SolarSeriesChart({ title, unit, series, formatTime, formatDetailTime = formatTime, maxGap, log, zero, bars, minZoomSpan }: Props) {
  const [cursor, setCursor] = useState<number | null>(null);
  const [zoom, setZoom] = useState<{ start: number; span: number } | null>(null);
  const plotRef = useRef<SVGSVGElement>(null);
  const clipId = useId();
  const points = series.flatMap((item) => item.points);
  const project = (value: number) => log ? Math.log10(value) : value;
  const values = points.flatMap(([, value]) => value === null || (log && value <= 0) ? [] : [project(value)]);
  const fullStart = points.length ? Math.min(...points.map(([time]) => time)) : 0;
  const fullEnd = points.length ? Math.max(...points.map(([time]) => time)) : 0;
  const fullSpan = fullEnd - fullStart;
  const zoomable = minZoomSpan !== undefined && fullSpan > minZoomSpan;
  const start = zoom?.start ?? fullStart;
  const end = zoom ? zoom.start + zoom.span : fullEnd;
  const spread = Math.max(end - start, 1);

  const zoomBy = useCallback((factor: number, anchor: number) => {
    if (minZoomSpan === undefined) return;
    setZoom(current => {
      const span = current?.span ?? fullSpan;
      const nextSpan = Math.min(fullSpan, Math.max(minZoomSpan, span * factor));
      if (nextSpan === fullSpan) return null;
      const anchoredStart = (current?.start ?? fullStart) + (span - nextSpan) * anchor;
      return {
        start: Math.min(fullEnd - nextSpan, Math.max(fullStart, anchoredStart)),
        span: nextSpan,
      };
    });
  }, [fullEnd, fullSpan, fullStart, minZoomSpan]);

  useEffect(() => {
    const plot = plotRef.current;
    if (!zoomable || !plot) return;
    const handleWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey || event.deltaY === 0) return;
      const bounds = plot.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width * width;
      const y = (event.clientY - bounds.top) / bounds.height * height;
      if (x < left || x > right || y < top || y > bottom) return;
      if ((event.deltaY > 0 && !zoom) || (event.deltaY < 0 && zoom?.span === minZoomSpan)) return;
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1);
      zoomBy(Math.exp(Math.max(-1, Math.min(1, delta / 480))), (x - left) / (right - left));
    };
    plot.addEventListener("wheel", handleWheel, { passive: false });
    return () => plot.removeEventListener("wheel", handleWheel);
  }, [minZoomSpan, zoom, zoomable, zoomBy]);

  if (values.length === 0) return <p className="my-4 text-sm text-slate-600">所选时间段无有效观测。</p>;

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
    return { position: x(stamp), label: (zoom ? formatDetailTime : formatTime)(stamp) };
  });

  return (
    <figure className="mt-2">
      <div className="relative">
      <svg ref={plotRef} className="block w-full overflow-visible text-slate-600" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}
        onPointerLeave={() => setCursor(null)}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const fraction = ((event.clientX - bounds.left) / bounds.width * width - left) / (right - left);
          setCursor(start + Math.max(0, Math.min(1, fraction)) * spread);
        }}>
        {zoomable && <defs><clipPath id={clipId}><rect x={left} y={top} width={right - left} height={bottom - top} /></clipPath></defs>}
        {yTicks.map(({ position }) => <line key={position} x1={left} x2={right} y1={position} y2={position} stroke="#d4dee8" />)}
        {xTicks.map(({ position }) => <line key={position} x1={position} x2={position} y1={top} y2={bottom} stroke="#e0e8ef" />)}
        {series.map((item) => {
          if (bars) return <g key={item.label}>{item.points.map(([time, value]) => value !== null && <rect key={time} fill={item.color} x={x(time) - barWidth / 2} y={y(value)} width={barWidth} height={bottom - y(value)} />)}</g>;
          let previous: number | undefined;
          const visiblePoints = zoom
            ? item.points.slice(Math.max(0, lowerBound(item.points, start) - 1), lowerBound(item.points, end) + 1)
            : item.points;
          const path = visiblePoints.map(([time, value]) => {
            if (value === null || (log && value <= 0)) {
              previous = undefined;
              return "";
            }
            const command = previous === undefined || time - previous > maxGap ? "M" : "L";
            previous = time;
            return `${command}${x(time).toFixed(2)} ${y(project(value)).toFixed(2)}`;
          }).join(" ");
          return <path key={item.label} d={path} fill="none" stroke={item.color} strokeWidth="1.8" strokeDasharray={item.dash} vectorEffect="non-scaling-stroke" clipPath={zoomable ? `url(#${clipId})` : undefined} />;
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
        {zoomable && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span role="status">{zoom ? `${formatDetailTime(start)} — ${formatDetailTime(end)}` : "滚轮缩放时间轴"}</span>
            <div className="flex items-center gap-2">
              <button type="button" className="px-1 text-sky-800 hover:underline disabled:text-slate-500" disabled={zoom?.span === minZoomSpan} onClick={() => zoomBy(0.5, 0.5)}>放大</button>
              <button type="button" className="px-1 text-sky-800 hover:underline disabled:text-slate-500" disabled={!zoom} onClick={() => zoomBy(2, 0.5)}>缩小</button>
              {zoom && <button type="button" className="px-1 text-sky-800 hover:underline" onClick={() => setZoom(null)}>重置范围</button>}
            </div>
          </div>
        )}
      </figcaption>
    </figure>
  );
}
