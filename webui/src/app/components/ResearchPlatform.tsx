"use client";

import React, { useState } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import Link from "next/link";
import { BookOpen, Bot, BrainCircuit, CalendarClock, Database, FlaskConical, Home, Library, Network, Orbit, Settings2, Sun, Telescope, Wrench, Blocks } from "lucide-react";
import { useThreads } from "@/app/hooks/useThreads";
import { formatFullTime } from "@/lib/time";
import { useRuntimeStatus } from "@/providers/ClientProvider";
import { TodaySunBrief } from "@/app/components/TodaySun";

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

export function PlatformNavigation({ view, compact = false, onView, onAgents, onSettings, onClose }: {
  view: string | null;
  compact?: boolean;
  onView: (view: string) => void;
  onAgents: () => void;
  onSettings: () => void;
  onClose?: () => void;
}) {
  const [hint, setHint] = useState<{ label: string; left: number; top: number } | null>(null);
  const showHint = (button: HTMLButtonElement, label: string) => {
    if (!compact) return;
    const bounds = button.getBoundingClientRect();
    setHint({ label, left: bounds.right + 10, top: bounds.top + bounds.height / 2 });
  };

  return (
    <nav aria-label="平台导航" data-compact={compact} className="platform-navigation min-h-0 overflow-y-auto" onScroll={() => setHint(null)}>
      {groups.map(group => (
        <div key={group.title} className="platform-navigation-group">
          <h2 className="platform-navigation-heading" aria-hidden={compact || undefined}>
            <span>{group.title}</span>
          </h2>
          <div className="platform-navigation-items">
            {group.items.map(item => (
              <button key={item.label} type="button"
                aria-label={item.label}
                aria-current={(item.view ?? item.action) === (view ?? "chat") ? "page" : undefined}
                onPointerEnter={event => { if (event.pointerType === "mouse") showHint(event.currentTarget, item.label); }}
                onPointerLeave={() => setHint(null)}
                onFocus={event => { if (event.currentTarget.matches(":focus-visible")) showHint(event.currentTarget, item.label); }}
                onBlur={() => setHint(null)}
                onKeyDown={event => { if (event.key === "Escape") setHint(null); }}
                onClick={() => {
                  setHint(null);
                  if (item.view) onView(item.view);
                  else if (item.action === "agents") onAgents();
                  else onSettings();
                  onClose?.();
                }}
                className="platform-navigation-button platform-blue-hover focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                <item.icon className="size-[18px] shrink-0" aria-hidden="true" />
                <span aria-hidden="true" className="platform-navigation-label">{item.label}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      {compact && hint && createPortal(
        <span role="tooltip" className="platform-navigation-hint" style={{ left: hint.left, top: hint.top }}>
          {hint.label}
        </span>, document.body
      )}
    </nav>
  );
}

const cards = [
  { title: "数据检索", desc: "查看科学数据接口与已配置来源", view: "data", icon: Database },
  { title: "观测与运行状态", desc: "查看望远镜与观测设施 · 遥测未接入", view: "observatories", icon: Telescope },
  { title: "文献调研", desc: "从科研助手开启文献任务", view: "chat", icon: Library },
  { title: "研究工作流", desc: "进入科研审查与实验入口", view: "workflow", icon: FlaskConical },
  { title: "报告生成", desc: "从科研助手发起报告任务", view: "chat", icon: BookOpen },
  { title: "智能体协同", desc: "查看真实任务及智能体活动", view: "agents", icon: Network },
];

const capabilityClass = "platform-blue-hover rounded-xl border border-slate-200 bg-white p-5 text-left shadow-sm hover:border-sky-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700";

export function ResearchWorkbench({ onView, onNewResearch, onOpenThread }: {
  onView: (view: string) => void;
  onNewResearch: () => void;
  onOpenThread: (threadId: string) => void;
}) {
  const { backendReady } = useRuntimeStatus();
  const { data, error } = useThreads({ enabled: backendReady, limit: 3 });
  const recentThreads = data?.[0]?.slice(0, 3) ?? [];
  let recentStatus = "正在加载研究会话…";
  if (!backendReady) recentStatus = "连接后端后显示最近会话。";
  else if (error) recentStatus = "暂时无法加载研究会话。";
  else if (data) recentStatus = "暂无研究会话。";

  return <main className="platform-workbench h-full overflow-y-auto bg-slate-50 px-5 py-8 text-slate-900 sm:px-8">
    <div className="mx-auto max-w-5xl">
      <section className="relative isolate overflow-hidden rounded-2xl bg-slate-800 text-white shadow-sm" aria-labelledby="workbench-title">
        <Image src="/platform/huairou-campus.jpg" alt="怀柔基地湖畔观测设施" fill priority sizes="(max-width: 768px) 100vw, 1024px" className="-z-20 object-cover object-center" />
        <div className="absolute inset-0 -z-10 bg-gradient-to-r from-slate-950/90 via-slate-950/70 to-slate-950/20" aria-hidden="true" />
        <div className="relative px-6 py-9 sm:px-9 sm:py-12">
          <p className="text-xs font-semibold tracking-widest text-sky-100">怀柔基地 · 金乌智能科研平台</p>
          <h2 id="workbench-title" className="mt-2 text-2xl font-semibold tracking-tight text-white sm:text-3xl">科研工作台</h2>
          <p className="mt-2 max-w-lg text-sm leading-6 text-slate-100">从已有研究继续，或向金乌科研助手提出新的科学问题。</p>
          <button type="button" onClick={onNewResearch} className="platform-blue-hover mt-6 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-sky-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">新建研究任务</button>
        </div>
      </section>
      <section aria-labelledby="capabilities-title" className="mt-10">
        <h3 id="capabilities-title" className="text-lg font-semibold">科研能力</h3>
        <div className="mt-4 grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {cards.map(card => (
            <Link key={card.title} href={card.view === "chat" ? "/" : `/?view=${card.view}`}
              className={capabilityClass}
              onNavigate={(event) => {
                event.preventDefault();
                onView(card.view);
              }}>
              <card.icon className="size-5 text-sky-700" aria-hidden="true" />
              <span className="mt-3 block font-semibold">{card.title}</span>
              <span className="mt-1 block text-sm text-slate-600">{card.desc}</span>
            </Link>
          ))}
        </div>
      </section>
      <section className="mt-8 grid items-start gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]" aria-label="平台状态">
        <section aria-labelledby="recent-research-title" className="min-w-0 rounded-xl border border-slate-200 bg-white p-5">
          <h3 id="recent-research-title" className="font-semibold">最近研究</h3>
          {recentThreads.length > 0 ? (
            <ul className="mt-2 divide-y divide-border">
              {recentThreads.map(thread => (
                <li key={thread.id}>
                  <button type="button" onClick={() => onOpenThread(thread.id)} className="platform-blue-hover grid w-full grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 rounded-md px-2 py-2 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                    <span className="text-sm font-medium text-sky-800 [overflow-wrap:anywhere]">{thread.title}</span>
                    <time dateTime={thread.updatedAt.toJSON() ?? undefined} title="最后更新" className="whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground">
                      {formatFullTime(thread.updatedAt)}
                    </time>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p role="status" className="mt-2 text-sm text-muted-foreground">{recentStatus}</p>
          )}
        </section>
        <TodaySunBrief onOpen={() => onView("solar")} />
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
        <button type="button" onClick={onChat} className="platform-blue-hover mt-5 rounded-lg border border-sky-700 px-3 py-2 text-sm text-sky-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-700">前往科研助手</button>
      </div>
    </div>
  </main>;
}
