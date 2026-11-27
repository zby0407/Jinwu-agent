"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import Image from "next/image";
import { useQueryState } from "nuqs";
import { getConfig, saveConfig, DeploymentConfig, DEFAULT_ASSISTANT_ID } from "@/lib/config";
import { ConfigDialog } from "@/app/components/ConfigDialog";
import { Button } from "@/components/ui/button";
import { Assistant } from "@langchain/langgraph-sdk";
import { ClientProvider, useClient, useRuntimeStatus } from "@/providers/ClientProvider";
import {
  Settings,
} from "lucide-react";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ThreadList } from "@/app/components/ThreadList";
import { ChatProvider } from "@/providers/ChatProvider";
import { ChatInterface } from "@/app/components/ChatInterface";
import { SkillsMarketplace } from "@/app/components/SkillsMarketplace";
import { MemoryPanel } from "@/app/components/MemoryPanel";
import { KnowledgePanel } from "@/app/components/KnowledgePanel";
import { ScheduledTasksPanel } from "@/app/components/ScheduledTasksPanel";
import { HealthIndicator } from "@/app/components/HealthIndicator";
import { useMemoryActivity } from "@/app/hooks/useMemoryActivity";
import { RealtimeActivityBridge } from "@/app/components/RealtimeActivityBridge";
import { RealtimeActivityProvider } from "@/providers/RealtimeActivityProvider";
import { setThreadAutoApprove } from "@/lib/autoApprove";
import type { MainChatReporter } from "@/lib/asyncAgents";
import { cn } from "@/lib/utils";
import { useAsyncAgents } from "@/app/hooks/useAsyncAgents";
import { AgentsPanel } from "@/app/components/AgentsPanel";
import { WorkspacePanel } from "@/app/components/WorkspacePanel";
import { ArtifactsPanel } from "@/app/components/ArtifactsPanel";
import { PlatformNavigation, ResearchWorkbench, PlatformPlaceholder } from "@/app/components/ResearchPlatform";
import { ModelSettings } from "@/app/components/ModelSettings";
import { ScientificDataInterfaces } from "@/app/components/ScientificDataInterfaces";
import { Menu } from "lucide-react";

interface HomePageInnerProps {
  config: DeploymentConfig;
  configDialogOpen: boolean;
  setConfigDialogOpen: (open: boolean) => void;
  handleSaveConfig: (config: DeploymentConfig) => void;
}

function HomePageInner({
  config,
  configDialogOpen,
  setConfigDialogOpen,
  handleSaveConfig,
}: HomePageInnerProps) {
  const client = useClient();
  const runtime = useRuntimeStatus();
  const [threadId, setThreadId] = useQueryState("threadId");
  const [view, setView] = useQueryState("view");
  const [memoryTab, setMemoryTab] = useQueryState("memoryTab");
  const [memoryObs, setMemoryObs] = useQueryState("memoryObs");
  const [memoryExec, setMemoryExec] = useQueryState("memoryExec");
  const { markSeen: markMemorySeen } = useMemoryActivity();
  const isResearchSection = view !== "skills" && view !== "memory";
  const { tasks: agentTasks } = useAsyncAgents(threadId, { enabled: runtime.backendReady && Boolean(threadId) });
  const hasRunningAgent = agentTasks.some(task => task.liveStatus === "running");
  const showAgentSidebar = hasRunningAgent && isResearchSection && view !== "agents";
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const navigate = useCallback((target: string) => {
    setView(target === "chat" ? null : target);
  }, [setView]);

  const [mutateThreads, setMutateThreads] = useState<(() => void) | null>(null);
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [isDesktopLayout, setIsDesktopLayout] = useState<boolean | null>(null);
  const [chatSessionRevision, setChatSessionRevision] = useState(0);
  // "Submit a message on the main thread" — registered by ChatInterface (only
  // while it's mounted, i.e. on the chat view), used by the Agents board to loop
  // an async result back to the main agent. Null when not on the chat view.
  const [notifyMainChat, setNotifyMainChat] = useState<MainChatReporter | null>(
    null
  );

  const fetchAssistant = useCallback(async () => {
    const isUUID =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        config.assistantId
      );

    const resolve = async (): Promise<Assistant> => {
      // A UUID addresses one assistant directly; otherwise list the graph's
      // assistants and prefer the system default (fall back to the first).
      if (isUUID) {
        return await client.assistants.get(config.assistantId);
      }
      const assistants = await client.assistants.search({
        graphId: config.assistantId,
        limit: 100,
      });
      const found =
        assistants.find((a) => a.metadata?.["created_by"] === "system") ??
        assistants[0];
      if (!found) throw new Error("未找到此图对应的助手。" );
      return found;
    };

    // The langgraph backend may not be ready the instant the page mounts — the
    // request then fails with "Failed to fetch". Retry a few times so a transient
    // startup race self-heals instead of surfacing a scary console error.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        setAssistant(await resolve());
        return;
      } catch (error) {
        if (attempt < 2) {
          await new Promise((r) => setTimeout(r, 700));
          continue;
        }
        console.warn(
          "Couldn't resolve the 金乌 assistant; addressing the graph by id instead. Is the backend running?",
          error
        );
      }
    }

    // Fallback: address the graph directly by id (works on `langgraph dev`).
    setAssistant({
      assistant_id: config.assistantId,
      graph_id: config.assistantId,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      config: {},
      metadata: {},
      version: 1,
      name: config.assistantId,
      context: {},
    });
  }, [client, config.assistantId]);

  useEffect(() => {
    if (runtime.backendReady) fetchAssistant();
  }, [fetchAssistant, runtime.backendReady]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 768px)");
    const updateLayout = () => setIsDesktopLayout(mediaQuery.matches);

    updateLayout();
    mediaQuery.addEventListener("change", updateLayout);
    return () => mediaQuery.removeEventListener("change", updateLayout);
  }, []);

  useEffect(() => {
    if (view === "memory") markMemorySeen();
  }, [markMemorySeen, view]);

  const showAgentsInspector = useCallback(() => setView("agents"), [setView]);
  const showResearch = useCallback(() => setView(null), [setView]);
  const startNewChat = useCallback(() => {
    setThreadAutoApprove(null, false);
    setThreadId(null);
    setView(null);
    setChatSessionRevision((revision) => revision + 1);
  }, [setThreadId, setView]);
  const handleDashboardNav = useCallback(
    (
      target:
        | {
            view: "memory";
            tab: "identity" | "knowledge" | "history";
            obsId?: string;
            execId?: string;
          }
        | { view: "schedule" }
        | { view: "workspace" }
    ) => {
      if (target.view === "memory") {
        setMemoryTab(target.tab);
        setMemoryObs(target.obsId ?? null);
        setMemoryExec(target.execId ?? null);
        setView("memory");
      } else if (target.view === "schedule") {
        setView("schedule");
      } else {
        setView("workspace");
      }
    },
    [setMemoryExec, setMemoryObs, setMemoryTab, setView]
  );
  const selectThread = useCallback(
    async (id: string) => {
      setThreadAutoApprove(null, false);
      setView(null);
      const sameThread = threadId === id;
      await setThreadId(id);
      // Only force a fresh ChatProvider mount when the thread actually
      // changes. Clicking the active thread row (e.g. to return to chat from
      // the Memory view) used to bump the revision unconditionally, which
      // tore down ChatInterface and forced useStream to re-fetch the full
      // thread `/history` — defeating the keep-chat-mounted layout.
      if (!sameThread) {
        setChatSessionRevision((revision) => revision + 1);
      }
    },
    [setThreadId, setView, threadId]
  );

  return (
    <>
      <ConfigDialog
        open={configDialogOpen}
        onOpenChange={setConfigDialogOpen}
        onSave={handleSaveConfig}
        initialConfig={config}
      />
      <div className="flex h-screen flex-col">
        <header className="platform-header flex h-14 items-center justify-between border-b border-slate-200 bg-white px-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" className="rounded-md p-1 text-slate-700 focus-visible:ring-2 focus-visible:ring-sky-600 md:hidden" aria-label="打开平台导航" aria-expanded={mobileNavigationOpen} onClick={() => setMobileNavigationOpen(true)}><Menu className="size-5" /></button>
            <Image src="/jw-logo.jpg" alt="金乌" width={30} height={30} className="size-8 shrink-0 rounded-full object-cover" priority />
            <h1 className="truncate text-sm font-semibold tracking-wide text-slate-900 sm:text-base">怀柔基地 · 金乌智能科研平台</h1>
          </div>
          <div className="flex shrink-0 items-center gap-1 sm:gap-2">
            <HealthIndicator deploymentUrl={config.deploymentUrl} onReconnect={(url) => handleSaveConfig({ ...config, deploymentUrl: url })} />
            <Button variant="ghost" size="icon" onClick={() => setConfigDialogOpen(true)} aria-label="设置" title="设置" className="size-8 text-slate-700 hover:bg-slate-100"><Settings className="size-5" aria-hidden="true" /></Button>
          </div>
        </header>

        <div className="platform-shell relative flex-1 overflow-hidden bg-slate-50">
          {mobileNavigationOpen && <div className="absolute inset-0 z-50 md:hidden"><button type="button" aria-label="关闭平台导航" className="absolute inset-0 bg-black/50" onClick={() => setMobileNavigationOpen(false)} /><aside className="relative h-full w-[min(19rem,90vw)] overflow-y-auto bg-white text-slate-900 shadow-xl"><PlatformNavigation view={view} onView={navigate} onAgents={showAgentsInspector} onSettings={() => setConfigDialogOpen(true)} onClose={() => setMobileNavigationOpen(false)} />{runtime.backendReady && <div className="relative h-[26rem]"><ThreadList onClose={() => setMobileNavigationOpen(false)} onNewChat={startNewChat} onThreadSelect={async id => { await selectThread(id); setMobileNavigationOpen(false); }} /></div>}</aside></div>}
          <RealtimeActivityProvider>
            <div className="relative z-10 flex h-full min-w-0">
              {isDesktopLayout && (
                <aside aria-label="平台导航和最近研究" className="flex h-full w-[220px] shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white text-slate-900 xl:w-[250px]">
                  <PlatformNavigation view={view} onView={navigate} onAgents={showAgentsInspector} onSettings={() => setConfigDialogOpen(true)} />
                  <div className="relative min-h-0 flex-1 bg-white text-slate-900">{runtime.backendReady && <ThreadList onNewChat={startNewChat} onThreadSelect={selectThread} onMutateReady={(fn) => setMutateThreads(() => fn)} />}</div>
                </aside>
              )}
            <ResizablePanelGroup
              direction="horizontal"
              autoSaveId="jw-chat"
              className="relative z-10"
            >
                <ResizablePanel id="chat" className="relative flex flex-col" order={2}>
                {/* Chat stays mounted across view switches. We hide it via
                  `display:none` (rather than unmounting) so flipping to
                  Skills/Memory and back is instant — no thread re-fetch, no
                  message-list rebuild, and any in-flight run keeps streaming
                  in the background. Cost is bounded: only the *current*
                  thread's state is held; no accumulation per switch. */}
                <div
                  className={cn(
                    "flex h-full min-h-0 flex-1 flex-col",
                    view !== null && "hidden"
                  )}
                >
                  <ChatProvider
                    key={chatSessionRevision}
                    activeAssistant={assistant}
                    onHistoryRevalidate={() => mutateThreads?.()}
                  >
                    <ChatInterface
                      assistant={assistant}
                      onShowAgents={showAgentsInspector}
                      onNotifyReady={(fn) => setNotifyMainChat(() => fn)}
                      onNavigate={handleDashboardNav}
                      onOpenThread={selectThread}
                      workspaceOpen={view === "workspace" || view === "artifacts"}
                    />
                    <RealtimeActivityBridge />
                  </ChatProvider>
                </div>
                {view === "home" && <ResearchWorkbench onView={navigate} onNewResearch={startNewChat} onAgents={showAgentsInspector} />}
                {view === "observatories" && <PlatformPlaceholder title="观测设施" description="仪器实时遥测与设施控制尚未接入。可在数据中心查看已配置的科学数据接口。" image="/platform/huairou-telescope.jpg" onChat={showResearch} />}
                {view === "solar" && <PlatformPlaceholder title="太阳活动" description="太阳活动实时指标尚未接入。现有太阳活动周 Wiki 可通过知识库访问；科研助手可处理已有资料。" onChat={showResearch} />}
                {view === "data" && <div className="h-full overflow-y-auto bg-background p-4"><h2 className="mb-3 text-xl font-semibold">数据中心 · 科学数据接口</h2><p className="mb-4 text-sm text-muted-foreground">以下为现有接口配置，不代表实时观测数据；修改接口仅适用于具备管理权限的部署。</p><ScientificDataInterfaces /></div>}
                {view === "workflow" && <PlatformPlaceholder title="科研工作流" description="科研审查与研究仪表盘位于科研助手的会话内；新建研究任务后即可使用。独立工作流编排页面尚未接入。" onChat={showResearch} />}
                {view === "models" && <div className="h-full overflow-y-auto bg-background p-4"><h2 className="mb-4 text-xl font-semibold">模型管理</h2><ModelSettings /></div>}
                {view === "tools" && <PlatformPlaceholder title="MCP 工具" description="独立 MCP 工具目录尚未接入。现有工具调用与审批可在科研助手的会话中查看。" onChat={showResearch} />}
                {view === "skills" && <SkillsMarketplace />}
                {view === "memory" && (
                  <MemoryPanel
                    initialTab={
                      memoryTab as
                        | "identity"
                        | "knowledge"
                        | "history"
                        | null
                        | undefined
                    }
                    initialObsId={memoryObs}
                    initialExecId={memoryExec}
                  />
                )}
                {view === "wiki" && <KnowledgePanel />}
                {view === "schedule" && <ScheduledTasksPanel />}
                {view === "agents" && <main className="h-full overflow-y-auto bg-slate-50 p-5 text-slate-900"><h2 className="mb-4 text-xl font-semibold">智能体协同</h2><AgentsPanel onReportToMainChat={notifyMainChat} /></main>}
                {view === "workspace" && <main className="h-full overflow-y-auto bg-slate-50 p-5 text-slate-900"><h2 className="mb-4 text-xl font-semibold">研究工作区</h2><WorkspacePanel /></main>}
                {view === "artifacts" && <main className="h-full overflow-y-auto bg-slate-50 p-5 text-slate-900"><h2 className="mb-4 text-xl font-semibold">文件与产物</h2><ArtifactsPanel /></main>}
              </ResizablePanel>

              {showAgentSidebar && isDesktopLayout && (
                <>
                  <ResizableHandle />
                  <ResizablePanel id="agents" order={3} defaultSize={26} minSize={20} className="relative min-w-[300px] overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 text-slate-900">
                    <h2 className="mb-3 text-sm font-semibold">智能体协同 · 运行中</h2>
                    <AgentsPanel onReportToMainChat={notifyMainChat} />
                  </ResizablePanel>
                </>
              )}
            </ResizablePanelGroup>
            </div>
          </RealtimeActivityProvider>
        </div>
      </div>
    </>
  );
}

function HomePageContent() {
  const [config, setConfig] = useState<DeploymentConfig | null>(null);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [assistantId, setAssistantId] = useQueryState("assistantId");

  // The desktop shell owns the local endpoint, including alternate ports.
  // Browser deployments retain their saved remote endpoint and setup flow.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    void (async () => {
      let resolved = getConfig();
      try {
        const response = await fetch("/api/jw-config", { signal: controller.signal, cache: "no-store" });
        const detected = response.ok ? await response.json() : null;
        if (detected?.desktopManaged && detected.deploymentUrl) {
          resolved = { ...resolved, deploymentUrl: detected.deploymentUrl, assistantId: DEFAULT_ASSISTANT_ID };
          if (!cancelled) saveConfig(resolved);
        }
      } catch { /* Saved browser configuration remains usable offline. */ }
      finally { clearTimeout(timeout); }
      if (cancelled) return;
      setConfig(resolved);
      setLoadingConfig(false);
      if (resolved) {
        if (!assistantId) setAssistantId(resolved.assistantId);
      } else {
        setConfigDialogOpen(true);
      }
    })();
    return () => { cancelled = true; clearTimeout(timeout); controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If config changes, update the assistantId
  useEffect(() => {
    if (config && !assistantId) {
      setAssistantId(config.assistantId);
    }
  }, [config, assistantId, setAssistantId]);

  const handleSaveConfig = useCallback((newConfig: DeploymentConfig) => {
    saveConfig(newConfig);
    setConfig(newConfig);
  }, []);

  const langsmithApiKey =
    config?.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";

  if (!config) {
    if (loadingConfig) return <div className="flex h-screen items-center justify-center">正在加载连接设置…</div>;
    return (
      <>
        <ConfigDialog
          open={configDialogOpen}
          onOpenChange={setConfigDialogOpen}
          onSave={handleSaveConfig}
        />
        <div className="flex h-screen items-center justify-center">
          <div className="text-center">
            <h1 className="text-2xl font-bold">欢迎来到金乌</h1>
            <p className="mt-2 text-muted-foreground">配置你的部署以开始使用</p>
            <Button
              onClick={() => setConfigDialogOpen(true)}
              className="mt-4"
            >
              打开配置
            </Button>
          </div>
        </div>
      </>
    );
  }

  return (
    <ClientProvider
      deploymentUrl={config.deploymentUrl}
      apiKey={langsmithApiKey}
    >
      <HomePageInner
        config={config}
        configDialogOpen={configDialogOpen}
        setConfigDialogOpen={setConfigDialogOpen}
        handleSaveConfig={handleSaveConfig}
      />
    </ClientProvider>
  );
}

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center">
          <p className="text-muted-foreground">加载中…</p>
        </div>
      }
    >
      <HomePageContent />
    </Suspense>
  );
}
