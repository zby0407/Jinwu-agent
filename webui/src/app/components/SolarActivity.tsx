"use client";

import { useState } from "react";
import { SolarCycle } from "@/app/components/SolarCycle";
import { TodaySun } from "@/app/components/TodaySun";

export type SolarTab = "today" | "cycle";
const tabs = [{ id: "today", text: "今日太阳" }, { id: "cycle", text: "太阳活动周" }] as const;

export function SolarActivity({ tab, onTab }: { tab: SolarTab; onTab: (tab: SolarTab) => void }) {
  const [zone, setZone] = useState("Asia/Shanghai");
  return <main className="h-full overflow-y-auto bg-slate-50 text-slate-900">
    <div className="mx-auto max-w-[92rem] px-4 pb-10 pt-5 sm:px-8 sm:pt-6">
      <h2 className="sr-only">太阳活动</h2>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-300 pb-2">
        <div role="tablist" aria-label="太阳活动" className="flex gap-0.5 rounded-md bg-slate-200/60 p-0.5">
          {tabs.map(({ id, text }) => (
            <button key={id} type="button" role="tab" id={`solar-tab-${id}`} aria-controls={`solar-panel-${id}`} aria-selected={tab === id}
              tabIndex={tab === id ? 0 : -1} onClick={() => onTab(id)}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === "Home" ? "today" : event.key === "End" ? "cycle" : id === "today" ? "cycle" : "today";
                onTab(next);
                document.getElementById(`solar-tab-${next}`)?.focus();
              }}
              className="min-h-8 whitespace-nowrap rounded px-3 py-1 text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-sky-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700 aria-selected:bg-sky-100 aria-selected:font-semibold aria-selected:text-sky-950">
              {text}
            </button>
          ))}
        </div>
        {tab === "today" && <label className="flex items-center gap-2 text-sm text-slate-700">
          显示时区
          <select value={zone} onChange={(event) => setZone(event.target.value)} className="rounded border-slate-300 bg-white py-1.5 pl-2 pr-7 text-sm">
            <option value="Asia/Shanghai">北京时间</option><option value="UTC">UTC</option>
          </select>
        </label>}
      </div>
      <div id="solar-panel-today" role="tabpanel" aria-labelledby="solar-tab-today" hidden={tab !== "today"}>
        {tab === "today" && <TodaySun zone={zone} />}
      </div>
      <div id="solar-panel-cycle" role="tabpanel" aria-labelledby="solar-tab-cycle" hidden={tab !== "cycle"}>
        {tab === "cycle" && <SolarCycle />}
      </div>
    </div>
  </main>;
}
