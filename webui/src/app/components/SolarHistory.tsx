"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { ChevronLeft, ChevronRight, StepBack, StepForward } from "lucide-react";
import { SolarSourceLink as SourceLink } from "@/app/components/SolarSourceLink";
import {
  fetchSolarHistory,
  formatHistoryTime,
  historyTimeMinutes,
} from "@/lib/solarLive";
import { cn } from "@/lib/utils";

const MIN_DATE = "2010-05-01"; // SDO browse archive starts with the prime mission
const DAY_MS = 86_400_000;

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

function utcYesterday() {
  return new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);
}

function monthOf(date: string) {
  return date.slice(0, 7);
}

function shiftMonth(month: string, delta: number) {
  const [year, index] = month.split("-").map(Number);
  const total = (year * 12 + index - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

function monthDays(month: string) {
  // Monday-first cells; null pads outside the month.
  const first = new Date(Date.parse(`${month}-01T00:00:00Z`));
  const count = new Date(Date.parse(`${shiftMonth(month, 1)}-01T00:00:00Z`) - DAY_MS).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7;
  const cells: (string | null)[] = Array.from({ length: lead }, () => null);
  for (let day = 1; day <= count; day += 1) cells.push(`${month}-${String(day).padStart(2, "0")}`);
  while (cells.length % 7) cells.push(null);
  return cells;
}

function HistoryCalendar({ value, onChange }: { value: string; onChange: (date: string) => void }) {
  const [month, setMonth] = useState(monthOf(value));
  const max = utcToday();
  const cells = useMemo(() => monthDays(month), [month]);
  const canPrev = month > monthOf(MIN_DATE);
  const canNext = month < monthOf(max);
  const move = (date: string, delta: number) => {
    const next = new Date(Date.parse(`${date}T00:00:00Z`) + delta * DAY_MS).toISOString().slice(0, 10);
    if (next < MIN_DATE || next > max) return;
    onChange(next);
    if (monthOf(next) !== month) setMonth(monthOf(next));
  };
  return (
    <div className="select-none">
      <div className="flex items-center justify-between">
        <button type="button" aria-label="上个月" disabled={!canPrev}
          onClick={() => { if (canPrev) setMonth(shiftMonth(month, -1)); }}
          className="grid size-7 place-items-center rounded text-slate-600 hover:bg-slate-100 disabled:opacity-30">
          <ChevronLeft className="size-4" aria-hidden="true" />
        </button>
        <span className="text-sm font-semibold tabular-nums text-slate-800">{month}</span>
        <button type="button" aria-label="下个月" disabled={!canNext}
          onClick={() => { if (canNext) setMonth(shiftMonth(month, 1)); }}
          className="grid size-7 place-items-center rounded text-slate-600 hover:bg-slate-100 disabled:opacity-30">
          <ChevronRight className="size-4" aria-hidden="true" />
        </button>
      </div>
      <div role="grid" aria-label="选择 UTC 日期" className="mt-1 grid grid-cols-7 gap-px text-center">
        {["一", "二", "三", "四", "五", "六", "日"].map((day) => (
          <span key={day} className="py-1 text-xs font-medium text-slate-500">{day}</span>
        ))}
        {cells.map((date, index) => date === null ? (
          <span key={`pad-${index}`} />
        ) : (
          <button key={date} type="button" role="gridcell" aria-selected={date === value}
            disabled={date < MIN_DATE || date > max}
            onClick={() => onChange(date)}
            onKeyDown={(event) => {
              const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
              if (delta) { event.preventDefault(); move(date, delta); }
            }}
            className={cn(
              "rounded py-1 text-sm tabular-nums transition",
              "text-slate-700 hover:bg-sky-50 hover:text-sky-900 disabled:text-slate-300 disabled:hover:bg-transparent",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700",
              date === value && "bg-sky-700 font-semibold text-white hover:bg-sky-700 hover:text-white",
              date === max && date !== value && "underline decoration-slate-300 underline-offset-2",
            )}>
            {Number(date.slice(8))}
          </button>
        ))}
      </div>
    </div>
  );
}

function nearestIndex(times: string[], minutes: number) {
  let best = 0;
  times.forEach((stamp, index) => {
    if (Math.abs(historyTimeMinutes(stamp) - minutes) < Math.abs(historyTimeMinutes(times[best]) - minutes)) best = index;
  });
  return best;
}

export type HistorySelection = { date: string; minutes: number };

export function useHistoryTimes(date: string | null) {
  const { data, error, isLoading } = useSWR(date ? `solar-history:${date}` : null, () => fetchSolarHistory(date as string), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  return { data, error, isLoading };
}

/** Snap minutes-of-day to the nearest archived frame of one channel. */
export function nearestFrame(times: string[], minutes: number): { stamp: string; index: number } | null {
  if (!times.length) return null;
  const index = Math.min(nearestIndex(times, minutes), times.length - 1);
  return { stamp: times[index], index };
}

/** Calendar + time-of-day slider shared by every live-observation panel. */
export function HistoryControls({ selection, onChange, listing, loading, loadError }: {
  selection: HistorySelection;
  onChange: (selection: HistorySelection) => void;
  listing: ReturnType<typeof useHistoryTimes>["data"];
  loading: boolean;
  loadError: unknown;
}) {
  const reference = listing?.channels.aia193.times.length ? listing.channels.aia193.times
    : listing?.channels.hmi.times.length ? listing.channels.hmi.times : [];
  const frame = nearestFrame(reference, selection.minutes);
  const index = frame?.index ?? 0;
  const stamp = frame?.stamp;
  const pick = (next: number) => {
    if (!reference.length) return;
    const clamped = Math.max(0, Math.min(reference.length - 1, next));
    onChange({ ...selection, minutes: historyTimeMinutes(reference[clamped]) });
  };
  return (
    <div className="grid items-start gap-x-8 gap-y-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <HistoryCalendar value={selection.date} onChange={(date) => onChange({ ...selection, date })} />
      <div className="min-w-0 lg:pt-1">
        <div className="flex items-center justify-between text-sm">
          <span className="font-semibold tabular-nums text-slate-800">{stamp ? formatHistoryTime(stamp) : "—"}</span>
          <span className="text-xs tabular-nums text-slate-500">{reference.length ? `第 ${index + 1} / ${reference.length} 帧` : ""}</span>
        </div>
        <div className="mt-1.5 flex max-w-md items-center gap-2">
          <button type="button" aria-label="上一帧" disabled={!reference.length || index === 0} onClick={() => pick(index - 1)}
            className="grid size-7 shrink-0 place-items-center rounded border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-30">
            <StepBack className="size-3.5" aria-hidden="true" />
          </button>
          <input type="range" aria-label="选择时刻" min={0} max={Math.max(reference.length - 1, 0)} value={index}
            disabled={!reference.length} onChange={(event) => pick(Number(event.target.value))}
            className="h-1.5 min-w-0 flex-1 accent-sky-700 disabled:opacity-30" />
          <button type="button" aria-label="下一帧" disabled={!reference.length || index >= reference.length - 1} onClick={() => pick(index + 1)}
            className="grid size-7 shrink-0 place-items-center rounded border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-30">
            <StepForward className="size-3.5" aria-hidden="true" />
          </button>
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          {loading ? `正在读取 ${selection.date} 的档案清单…` : loadError ? `${selection.date} 暂无档案图像` : "档案按 UTC 日期组织 · 各通道自动对齐到相近时刻"}
          {" · 数据 "}
          {listing ? <SourceLink href={listing.source_url}>NASA SDO 快视图档案</SourceLink> : "NASA SDO 快视图档案"}
        </p>
      </div>
    </div>
  );
}
