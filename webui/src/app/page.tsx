"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import Image from "next/image";
import { useQueryState } from "nuqs";
import { getConfig, saveConfig, DeploymentConfig } from "@/lib/config";
import { ConfigDialog } from "@/app/components/ConfigDialog";
import { Button } from "@/components/ui/button";
import { Assistant } from "@langchain/langgraph-sdk";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import {
  Blocks,
  BrainCircuit,
  Orbit,
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
import { InspectorPanel } from "@/app/components/InspectorPanel";
import { PanelEdgeToggle } from "@/app/components/PanelEdgeToggle";
import { RealtimeActivityBridge } from "@/app/components/RealtimeActivityBridge";
import { RealtimeActivityProvider } from "@/providers/RealtimeActivityProvider";
import { setThreadAutoApprove } from "@/lib/autoApprove";
import type { MainChatReporter } from "@/lib/asyncAgents";
import { cn } from "@/lib/utils";
import { useMemoryActivity } from "@/app/hooks/useMemoryActivity";

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
  const [threadId, setThreadId] = useQueryState("threadId");
  const [sidebar, setSidebar] = useQueryState("sidebar");
  const [view, setView] = useQueryState("view");
  const [memoryTab, setMemoryTab] = useQueryState("memoryTab");
  const [memoryObs, setMemoryObs] = useQueryState("memoryObs");
  const [memoryExec, setMemoryExec] = useQueryState("memoryExec");
  const [inspector, setInspector] = useQueryState("inspector");
  const [inspectorTab, setInspectorTab] = useQueryState("inspectorTab");
  const { unseenCount: memoryUnseen, markSeen: markMemorySeen } =
    useMemoryActivity();
  const isResearchSection = view !== "skills" && view !== "memory";

  const [mutateThreads, setMutateThreads] = useState<(() => void) | null>(null);
  const [interruptCount, setInterruptCount] = useState(0);
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
    fetchAssistant();
  }, [fetchAssistant]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 768px)");
    const updateLayout = () => setIsDesktopLayout(mediaQuery.matches);

    updateLayout();
    mediaQuery.addEventListener("change", updateLayout);
    return () => mediaQuery.removeEventListener("change", updateLayout);
  }, []);

  useEffect(() => {
    if (isDesktopLayout === false && sidebar && inspector) {
      setInspector(null);
    }
  }, [inspector, isDesktopLayout, setInspector, sidebar]);

  useEffect(() => {
    if (view === "memory") markMemorySeen();
  }, [markMemorySeen, view]);

  const closeSidebar = useCallback(() => setSidebar(null), [setSidebar]);
  const closeInspector = useCallback(() => {
    setInspector(null);
    setInspectorTab(null);
  }, [setInspector, setInspectorTab]);
  const toggleSidebar = useCallback(() => {
    if (sidebar) {
      setSidebar(null);
      return;
    }
    if (isDesktopLayout === false) closeInspector();
    setSidebar("1");
  }, [closeInspector, isDesktopLayout, setSidebar, sidebar]);
  const toggleInspector = useCallback(() => {
    if (inspector) {
      closeInspector();
      return;
    }
    if (isDesktopLayout === false) setSidebar(null);
    setInspectorTab(null);
    setInspector("1");
  }, [
    closeInspector,
    inspector,
    isDesktopLayout,
    setInspector,
    setInspectorTab,
    setSidebar,
  ]);
  // Open the inspector straight on its Agents tab (composer pulse → board).
  const showAgentsInspector = useCallback(() => {
    setInspectorTab("agents");
    if (isDesktopLayout === false) setSidebar(null);
    setInspector("1");
  }, [isDesktopLayout, setInspector, setSidebar, setInspectorTab]);
  const showResearch = useCallback(() => setView(null), [setView]);
  const showSkills = useCallback(() => setView("skills"), [setView]);
  const showMemory = useCallback(() => {
    markMemorySeen();
    setView("memory");
  }, [markMemorySeen, setView]);
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
        if (inspector && inspectorTab !== "agents") {
          closeInspector();
          return;
        }
        if (isDesktopLayout === false) setSidebar(null);
        setInspectorTab(null);
        setInspector("1");
      }
    },
    [
      closeInspector,
      inspector,
      inspectorTab,
      isDesktopLayout,
      setInspector,
      setInspectorTab,
      setMemoryExec,
      setMemoryObs,
      setMemoryTab,
      setSidebar,
      setView,
    ]
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
        <header className="jw-topbar grid h-14 grid-cols-[1fr_auto_1fr] items-center gap-1 px-2 sm:gap-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-2">
            <Image
              src="/jw-logo.jpg"
              alt="金乌"
              width={30}
              height={30}
              className="size-7 shrink-0 rounded-full object-cover ring-1 ring-[var(--brand)]/70 sm:size-[30px]"
              priority
            />
            <h1 className="hidden sm:block">
              <span className="sr-only">金乌</span>
              <Image
                src="/branding/jinwu-bright-gold-calligraphy-transparent-v2.png"
                alt=""
                width={80}
                height={45}
                className="h-[45px] w-20 object-contain"
                aria-hidden="true"
                priority
              />
            </h1>
          </div>

          <nav className="jw-primary-nav" aria-label="一级导航">
            <button
              type="button"
              onClick={showResearch}
              data-active={isResearchSection}
              aria-current={isResearchSection ? "page" : undefined}
              aria-label="科学研究"
              title="科学研究"
              className="jw-primary-nav-button"
            >
              <Orbit className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">科学研究</span>
            </button>
            <button
              type="button"
              onClick={showSkills}
              data-active={view === "skills"}
              aria-current={view === "skills" ? "page" : undefined}
              aria-label="Skills 列表"
              title="Skills 列表"
              className="jw-primary-nav-button"
            >
              <Blocks className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">Skills 列表</span>
            </button>
            <button
              type="button"
              onClick={showMemory}
              data-active={view === "memory"}
              aria-current={view === "memory" ? "page" : undefined}
              aria-label="金乌记忆"
              title="金乌记忆"
              className="jw-primary-nav-button relative"
            >
              <BrainCircuit className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">金乌记忆</span>
              {view !== "memory" && memoryUnseen > 0 && (
                <span
                  className="absolute -right-1 -top-1 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-[var(--brand-solid)] px-1 text-[10px] font-bold text-[var(--brand-foreground)]"
                  aria-label={`${memoryUnseen} 条记忆更新`}
                  title={`${memoryUnseen} 条记忆更新`}
                >
                  {memoryUnseen}
                </span>
              )}
            </button>
          </nav>

          <div className="flex min-w-0 justify-end gap-1 sm:gap-2">
            <HealthIndicator
              deploymentUrl={config.deploymentUrl}
              onReconnect={(url) =>
                handleSaveConfig({ ...config, deploymentUrl: url })
              }
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setConfigDialogOpen(true)}
              aria-label="设置"
              title="设置"
              className="size-8 text-[var(--brand)]"
            >
              <Settings
                className="size-5"
                aria-hidden="true"
              />
            </Button>
          </div>
        </header>

        <div className="relative flex-1 overflow-hidden">
          {view === null && (
            <div
              aria-hidden="true"
              className="chat-workspace-background pointer-events-none absolute inset-0 z-0"
            />
          )}
          {view === "skills" && (
            <div
              aria-hidden="true"
              className="skills-workspace-background pointer-events-none absolute inset-0 z-0"
            />
          )}
          {isResearchSection && !sidebar && (
            <PanelEdgeToggle
              side="left"
              open={false}
              onClick={toggleSidebar}
              label="展开研究导航"
              badge={interruptCount}
              className="absolute left-0 top-1/2 z-30 -translate-y-1/2"
            />
          )}
          {isResearchSection && !inspector && (
            <PanelEdgeToggle
              side="right"
              open={false}
              onClick={toggleInspector}
              label="展开研究工作区"
              className="absolute right-0 top-1/2 z-30 -translate-y-1/2"
            />
          )}
          {isResearchSection && sidebar && isDesktopLayout === false && (
            <div className="absolute inset-0 z-40 flex md:hidden">
              <button
                type="button"
                aria-label="关闭研究导航"
                className="absolute inset-0 bg-black/40"
                onClick={closeSidebar}
              />
              <aside
                aria-label="研究导航"
                className="relative z-10 h-full w-[min(19rem,calc(100vw-2.25rem))] bg-background shadow-xl"
              >
                <ThreadList
                  onClose={closeSidebar}
                  onNewChat={startNewChat}
                  onThreadSelect={async (id) => {
                    await selectThread(id);
                    closeSidebar();
                  }}
                  onMutateReady={(fn) => setMutateThreads(() => fn)}
                  onInterruptCountChange={setInterruptCount}
                />
                <PanelEdgeToggle
                  side="left"
                  open
                  onClick={toggleSidebar}
                  label="收起研究导航"
                  badge={interruptCount}
                  className="absolute right-0 top-1/2 z-20 translate-x-full -translate-y-1/2"
                />
              </aside>
            </div>
          )}
          {isResearchSection && inspector && isDesktopLayout === false && (
            <div className="absolute inset-0 z-40 flex justify-end md:hidden">
              <button
                type="button"
                aria-label="关闭研究工作区"
                className="absolute inset-0 bg-black/40"
                onClick={closeInspector}
              />
              <aside
                aria-label="研究工作区"
                className="relative z-10 h-full w-[min(22rem,calc(100vw-2.25rem))] bg-background shadow-xl"
              >
                <InspectorPanel
                  onReportToMainChat={notifyMainChat}
                />
                <PanelEdgeToggle
                  side="right"
                  open
                  onClick={toggleInspector}
                  label="收起研究工作区"
                  className="absolute left-0 top-1/2 z-20 -translate-x-full -translate-y-1/2"
                />
              </aside>
            </div>
          )}
          <RealtimeActivityProvider>
            <ResizablePanelGroup
              direction="horizontal"
              autoSaveId="jw-chat"
              className="relative z-10"
            >
              {isResearchSection && sidebar && isDesktopLayout && (
                <>
                  <ResizablePanel
                    id="thread-history"
                    order={1}
                    defaultSize={23}
                    minSize={18}
                    className="relative min-w-[260px] bg-background"
                  >
                    <ThreadList
                      onNewChat={startNewChat}
                      onThreadSelect={selectThread}
                      onMutateReady={(fn) => setMutateThreads(() => fn)}
                      onInterruptCountChange={setInterruptCount}
                    />
                  </ResizablePanel>
                  <ResizableHandle />
                </>
              )}

              <ResizablePanel
                id="chat"
                className="relative flex flex-col"
                order={2}
              >
                {isResearchSection && isDesktopLayout && sidebar && (
                  <PanelEdgeToggle
                    side="left"
                    open
                    onClick={toggleSidebar}
                    label="收起研究导航"
                    badge={interruptCount}
                    className="absolute left-0 top-1/2 z-30 -translate-y-1/2"
                  />
                )}
                {isResearchSection && isDesktopLayout && inspector && (
                  <PanelEdgeToggle
                    side="right"
                    open
                    onClick={toggleInspector}
                    label="收起研究工作区"
                    className="absolute right-0 top-1/2 z-30 -translate-y-1/2"
                  />
                )}
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
                      workspaceOpen={Boolean(
                        inspector && inspectorTab !== "agents"
                      )}
                    />
                    <RealtimeActivityBridge />
                  </ChatProvider>
                </div>
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
              </ResizablePanel>

              {isResearchSection && inspector && isDesktopLayout && (
                <>
                  <ResizableHandle />
                  <ResizablePanel
                    id="inspector"
                    order={3}
                    defaultSize={26}
                    minSize={20}
                    className="relative min-w-[300px] bg-background"
                  >
                    <InspectorPanel
                      onReportToMainChat={notifyMainChat}
                    />
                  </ResizablePanel>
                </>
              )}
            </ResizablePanelGroup>
          </RealtimeActivityProvider>
        </div>
      </div>
    </>
  );
}

function HomePageContent() {
  const [config, setConfig] = useState<DeploymentConfig | null>(null);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [assistantId, setAssistantId] = useQueryState("assistantId");

  // On mount, check for saved config, otherwise show config dialog
  useEffect(() => {
    const savedConfig = getConfig();
    if (savedConfig) {
      setConfig(savedConfig);
      if (!assistantId) {
        setAssistantId(savedConfig.assistantId);
      }
    } else {
      setConfigDialogOpen(true);
    }
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
