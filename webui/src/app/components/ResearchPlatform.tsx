"use client";

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, ChevronLeft, ChevronRight, Plus, Search, FileText, BookOpen, Bot, BrainCircuit, CalendarClock, Database, FlaskConical, Home, Library, Orbit, Sun, Telescope, Wrench, Blocks } from "lucide-react";
import { WorkbenchSolarBrief } from "@/app/components/WorkbenchSolarBrief";
import { useThreads } from "@/app/hooks/useThreads";
import { formatFullTime } from "@/lib/time";
import { useRuntimeStatus } from "@/providers/ClientProvider";

type Destination = { label: string; icon: React.ComponentType<{ className?: string }>; view?: string; note?: string };
const groups: { title: string; items: Destination[] }[] = [
  { title: "研究", items: [
    { label: "工作台", icon: Home, view: "home" },
    { label: "金乌科研助手", icon: Bot, view: "chat" },
    { label: "观测设施", icon: Telescope, view: "observatories" },
    { label: "太阳活动", icon: Sun, view: "solar" },
    { label: "数据中心", icon: Database, view: "data" },
    { label: "知识库", icon: BookOpen, view: "wiki" },
  ] },
  { title: "协同与工具", items: [
    { label: "模型管理", icon: Orbit, view: "models" },
    { label: "连接", icon: Wrench, view: "tools" },
    { label: "技能", icon: Blocks, view: "skills" },
    { label: "金乌记忆", icon: BrainCircuit, view: "memory" },
    { label: "定时任务", icon: CalendarClock, view: "schedule" },
  ] },
];

export function PlatformNavigation({ view, compact = false, onView, onClose }: {
  view: string | null;
  compact?: boolean;
  onView: (view: string) => void;
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
                aria-current={item.view === (view ?? "chat") ? "page" : undefined}
                onPointerEnter={event => { if (event.pointerType === "mouse") showHint(event.currentTarget, item.label); }}
                onPointerLeave={() => setHint(null)}
                onFocus={event => { if (event.currentTarget.matches(":focus-visible")) showHint(event.currentTarget, item.label); }}
                onBlur={() => setHint(null)}
                onKeyDown={event => { if (event.key === "Escape") setHint(null); }}
                onClick={() => {
                  setHint(null);
                  if (item.view) onView(item.view);
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

const researchLinks = [
  { title: "知识库", desc: "检索文献、资料与已有知识", view: "wiki", icon: BookOpen },
  { title: "定时任务", desc: "查看已配置的自动研究任务", view: "schedule", icon: CalendarClock },
  { title: "数据接口", desc: "查看来源配置与接入方式", view: "data", icon: Database },
];

const capabilities = [
  { title: "数据检索", desc: "查询科学数据与观测来源", view: "data", icon: Database },
  { title: "观测与运行状态", desc: "查看望远镜与观测设施 · 遥测未接入", view: "observatories", icon: Telescope },
  { title: "文献调研", desc: "从科研助手开启文献任务", view: "chat", icon: Library },
  { title: "科研工作流", desc: "查看和管理定时科研任务", view: "workflow", icon: FlaskConical },
  { title: "报告生成", desc: "从科研助手发起报告任务", view: "chat", icon: BookOpen },
];

const stationPhotos = [
  { src: "/platform/huairou-campus.jpg", alt: "湖畔观测站建筑与圆顶" },
  { src: "/platform/huairou-telescope.jpg", alt: "观测站望远镜与圆顶" },
  { src: "/platform/huairou-sunset.jpg", alt: "夕阳下的观测站圆顶" },
];

function greetingFor(hour: number) {
  if (hour < 6) return "凌晨好";
  if (hour < 9) return "早上好";
  if (hour < 12) return "上午好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function StationBanner() {
  const [slide, setSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  const [greeting, setGreeting] = useState("");
  useEffect(() => {
    setGreeting(greetingFor(new Date().getHours()));
  }, []);
  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motion.matches || paused) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setSlide(current => (current + 1) % stationPhotos.length);
    }, 6000);
    return () => window.clearInterval(timer);
  }, [paused]);
  return <section className="station-banner" aria-label="观测站照片轮播" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocusCapture={() => setPaused(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false); }}>
    {stationPhotos.map((photo, index) => <div key={photo.src} className="station-banner-slide" data-active={index === slide} aria-hidden={index !== slide}><Image src={photo.src} alt={index === slide ? photo.alt : ""} fill priority={index === 0} sizes="(max-width: 768px) 100vw, 1160px" className="object-cover" /></div>)}
    <div className="station-banner-greeting"><div className="station-banner-greeting-inner">{greeting && <p className="station-banner-hello">{greeting}</p>}</div></div>
    <div className="station-banner-controls"><span className="station-banner-index" aria-hidden="true">{String(slide + 1).padStart(2, "0")} / {String(stationPhotos.length).padStart(2, "0")}</span><button type="button" aria-label="上一张观测站照片" onClick={() => setSlide(current => (current - 1 + stationPhotos.length) % stationPhotos.length)}><ChevronLeft className="size-4" /></button><button type="button" aria-label="下一张观测站照片" onClick={() => setSlide(current => (current + 1) % stationPhotos.length)}><ChevronRight className="size-4" /></button></div>
  </section>;
}

export function ResearchWorkbench({ onView, onNewResearch, onOpenThread }: {
  onView: (view: string) => void;
  onNewResearch: () => void;
  onOpenThread: (threadId: string) => void;
}) {
  const { backendReady } = useRuntimeStatus();
  const { data, error, mutate, isValidating } = useThreads({ enabled: backendReady, limit: 8 });
  const [query, setQuery] = useState("");
  const recentThreads = data?.[0] ?? [];
  const filtered = recentThreads.filter(thread => thread.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  let recentStatus = "正在加载研究会话…";
  if (!backendReady) recentStatus = "后端未连接";
  else if (error) recentStatus = "无法加载研究会话";
  else if (data) recentStatus = query ? "没有匹配的研究" : "暂无研究会话";

  return <main className="research-page h-full overflow-y-auto" aria-labelledby="workbench-title">
    <h2 id="workbench-title" className="sr-only">科研工作台</h2>
    <StationBanner />
    <div className="research-page-inner research-page-inner-overlap workbench-content">
      <section className="workbench-section" aria-labelledby="capabilities-title">
        <div className="workbench-section-header"><h3 id="capabilities-title">科研能力</h3></div>
        <div className="workbench-capabilities">
          {capabilities.map(item => (
            <Link key={item.title} href={item.view === "chat" ? "/" : `/?view=${item.view}`}
              className="workbench-entry workbench-capability"
              onNavigate={(event) => {
                event.preventDefault();
                onView(item.view);
              }}>
              <item.icon className="size-[18px] shrink-0 text-sky-800" aria-hidden="true" />
              <span className="workbench-entry-text">
                <strong>{item.title}</strong><span>{item.desc}</span>
              </span>
              <ArrowRight className="workbench-entry-arrow" aria-hidden="true" />
            </Link>
          ))}
        </div>
      </section>
      <section className="workbench-layout" aria-label="平台状态">
        <section className="workbench-section min-w-0" aria-labelledby="recent-research-title">
          <div className="workbench-section-header">
            <h3 id="recent-research-title">最近研究</h3>
            <button type="button" aria-label="新建研究任务" onClick={onNewResearch} className="workbench-action">
              <Plus className="size-4" aria-hidden="true" /> 新建研究
            </button>
          </div>
          <label className="workbench-search">
            <Search className="size-4 shrink-0" aria-hidden="true" />
            <input aria-label="筛选最近研究" placeholder="搜索最近研究" value={query} onChange={event => setQuery(event.target.value)} />
          </label>
          {filtered.length > 0 ? <ul className="workbench-list">{filtered.map(thread => <li key={thread.id}>
            <button type="button" onClick={() => onOpenThread(thread.id)} className="workbench-entry research-thread-row">
              <FileText className="size-4 shrink-0 text-slate-500" aria-hidden="true" />
              <span className="workbench-entry-text"><strong className="truncate">{thread.title}</strong>
                <time dateTime={thread.updatedAt.toJSON() ?? undefined}>{formatFullTime(thread.updatedAt)}</time>
              </span>
              <ArrowRight className="workbench-entry-arrow" aria-hidden="true" />
            </button>
          </li>)}</ul> : <div role="status" className="workbench-list-status">{recentStatus}{backendReady && error && <button type="button" className="workbench-action ml-3" disabled={isValidating} onClick={() => void mutate()}>重试</button>}</div>}
        </section>
        <aside className="workbench-section min-w-0" aria-labelledby="research-tools-title">
          <div className="workbench-section-header"><h3 id="research-tools-title">研究资源</h3></div>
          <ul className="workbench-list">
            {researchLinks.map(link => (
              <li key={link.view}>
                <button type="button" className="workbench-entry" onClick={() => onView(link.view)}>
                  <link.icon className="size-4 shrink-0 text-slate-500" aria-hidden="true" />
                  <span className="workbench-entry-text"><strong>{link.title}</strong><span>{link.desc}</span></span>
                  <ArrowRight className="workbench-entry-arrow" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </section>
      <WorkbenchSolarBrief backendReady={backendReady} onView={onView} />
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
