"use client";

import React from "react";
import Image from "next/image";
import { BookOpen, Bot, BrainCircuit, CalendarClock, Database, FlaskConical, Home, Library, Network, Orbit, Settings2, Sun, Telescope, Wrench, Blocks } from "lucide-react";

type Destination = { label: string; icon: React.ComponentType<{ className?: string }>; view?: string; action?: "agents" | "settings"; note?: string };
const groups: { title: string; items: Destination[] }[] = [
  { title: "研究", items: [
    { label: "工作台", icon: Home, view: "home" },
    { label: "金乌科研助手", icon: Bot, view: "chat" },
    { label: "观测设施", icon: Telescope, view: "observatories" },
    { label: "太阳活动", icon: Sun, view: "solar" },
    { label: "数据中心", icon: Database, view: "data" },
    { label: "科研工作流", icon: FlaskConical, view: "workflow" },
    { label: "知识库", icon: BookOpen, view: "wiki" },
  ] },
  { title: "协同与工具", items: [
    { label: "智能体协同", icon: Network, action: "agents" },
    { label: "模型管理", icon: Orbit, view: "models" },
    { label: "MCP 工具", icon: Wrench, view: "tools" },
    { label: "技能", icon: Blocks, view: "skills" },
    { label: "金乌记忆", icon: BrainCircuit, view: "memory" },
    { label: "定时任务", icon: CalendarClock, view: "schedule" },
  ] },
  { title: "偏好", items: [{ label: "连接设置", icon: Settings2, action: "settings" }] },
];

export function PlatformNavigation({ view, onView, onAgents, onSettings, onClose }: {
  view: string | null;
  onView: (view: string) => void;
  onAgents: () => void;
  onSettings: () => void;
  onClose?: () => void;
}) {
  return <nav aria-label="平台导航" className="platform-navigation overflow-y-auto border-b border-slate-200 px-3 py-3">
    {groups.map(group => <div key={group.title} className="mb-3">
      <h2 className="px-2 pb-1 text-[11px] font-semibold tracking-widest text-slate-500">{group.title}</h2>
      <div className="grid grid-cols-2 gap-1 md:grid-cols-1">
        {group.items.map(item => <button key={item.label} type="button"
          aria-current={item.view === (view ?? "chat") ? "page" : undefined}
          onClick={() => { if (item.view) onView(item.view); else if (item.action === "agents") onAgents(); else onSettings(); onClose?.(); }}
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-600 aria-[current=page]:bg-sky-100 aria-[current=page]:text-sky-900">
          <item.icon className="size-4 shrink-0" />{item.label}
        </button>)}
      </div>
    </div>)}
  </nav>;
}

const cards = [
  { title: "数据检索", desc: "查看科学数据接口与已配置来源", view: "data", icon: Database },
  { title: "太阳活动分析", desc: "查阅已有太阳活动知识", view: "solar", icon: Sun },
  { title: "文献调研", desc: "从科研助手开启文献任务", view: "chat", icon: Library },
  { title: "研究工作流", desc: "进入科研审查与实验入口", view: "workflow", icon: FlaskConical },
  { title: "报告生成", desc: "从科研助手发起报告任务", view: "chat", icon: BookOpen },
  { title: "智能体协同", desc: "查看真实任务及智能体活动", view: "agents", icon: Network },
];

export function ResearchWorkbench({ onView, onNewResearch, onAgents }: {
  onView: (view: string) => void;
  onNewResearch: () => void;
  onAgents: () => void;
}) {
  return <main className="platform-workbench h-full overflow-y-auto bg-slate-50 px-5 py-8 text-slate-900 sm:px-8">
    <div className="mx-auto max-w-5xl">
      <section className="relative isolate overflow-hidden rounded-2xl bg-slate-800 text-white shadow-sm" aria-labelledby="workbench-title">
        <Image src="/platform/huairou-campus.jpg" alt="怀柔基地湖畔观测设施" fill priority sizes="(max-width: 768px) 100vw, 1024px" className="-z-20 object-cover object-center" />
        <div className="absolute inset-0 -z-10 bg-gradient-to-r from-slate-950/90 via-slate-950/70 to-slate-950/20" aria-hidden="true" />
        <div className="relative px-6 py-9 sm:px-9 sm:py-12">
          <p className="text-xs font-semibold tracking-widest text-sky-100">怀柔基地 · 金乌智能科研平台</p>
          <h2 id="workbench-title" className="mt-2 text-2xl font-semibold tracking-tight text-white sm:text-3xl">科研工作台</h2>
          <p className="mt-2 max-w-lg text-sm leading-6 text-slate-100">从已有研究继续，或向金乌科研助手提出新的科学问题。</p>
          <button type="button" onClick={onNewResearch} className="mt-6 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-sky-900 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">新建研究任务</button>
        </div>
      </section>
      <section aria-labelledby="capabilities-title" className="mt-10">
        <h3 id="capabilities-title" className="text-lg font-semibold">科研能力</h3>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {cards.map(card => <button key={card.title} type="button" onClick={() => card.view === "agents" ? onAgents() : onView(card.view)} className="rounded-xl border border-slate-200 bg-white p-5 text-left shadow-sm transition-colors hover:border-sky-400 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700">
            <card.icon className="size-5 text-sky-700" /><span className="mt-3 block font-semibold">{card.title}</span><span className="mt-1 block text-sm text-slate-600">{card.desc}</span>
          </button>)}
        </div>
      </section>
      <section className="mt-8 grid gap-4 sm:grid-cols-2" aria-label="平台状态">
        <div className="rounded-xl border border-slate-200 bg-white p-5"><h3 className="font-semibold">最近研究</h3><p className="mt-2 text-sm text-slate-600">请从左侧最近记录查看真实会话；尚无记录时可新建研究任务。</p></div>
        <div className="rounded-xl border border-slate-200 bg-white p-5"><h3 className="font-semibold">观测与运行状态</h3><p className="mt-2 text-sm text-slate-600">仪器实时遥测未接入。连接状态请以顶部健康指示为准。</p></div>
      </section>
    </div>
  </main>;
}

export function PlatformPlaceholder({ title, description, onChat, image }: { title: string; description: string; onChat: () => void; image?: string }) {
  return <main className="platform-workbench h-full overflow-y-auto bg-slate-50 p-6 text-slate-900">
    <div className="mx-auto max-w-3xl pt-4 sm:pt-8">
      {image && <div className="relative mb-5 h-44 overflow-hidden rounded-2xl sm:h-60"><Image src={image} alt="怀柔基地观测设施实景" fill sizes="(max-width: 768px) 100vw, 768px" className="object-cover object-center" /></div>}
      <div className="rounded-xl border border-slate-200 bg-white p-6 sm:p-8">
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="mt-3 text-sm leading-6 text-slate-600">{description}</p>
        <button type="button" onClick={onChat} className="mt-5 rounded-lg border border-sky-700 px-3 py-2 text-sm text-sky-800 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700">前往科研助手</button>
      </div>
    </div>
  </main>;
}
