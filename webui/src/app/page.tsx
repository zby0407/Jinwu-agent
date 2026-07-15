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
  Settings,
  SquarePen,
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
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
import { ScheduledTasksPanel } from "@/app/components/ScheduledTasksPanel";
import { ThemeToggle } from "@/app/components/ThemeToggle";
import { HealthIndicator } from "@/app/components/HealthIndicator";
import { InspectorPanel } from "@/app/components/InspectorPanel";
import { RealtimeActivityBridge } from "@/app/components/RealtimeActivityBridge";
import { RealtimeActivityProvider } from "@/providers/RealtimeActivityProvider";
import { setThreadAutoApprove } from "@/lib/autoApprove";
import type { MainChatReporter } from "@/lib/asyncAgents";
import { cn } from "@/lib/utils";

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
      if (!found) throw new Error("No assistant found for this graph.");
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
  const sidebarToggleLabel = view
    ? sidebar
      ? "Hide navigation"
      : "Show navigation"
    : sidebar
    ? "Hide research"
    : "Show research";
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
        <header className="flex h-14 items-center justify-between gap-2 border-b border-border px-2.5 sm:px-4">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <Image
                src="/jinwu-logo.svg"
                alt="金乌"
                width={28}
                height={28}
                className="size-6 shrink-0"
                priority
              />
              {/* Show the wordmark only when the thread sidebar is open (it
                  titles the panel). When collapsed, keep just the logo + the
                  toggle / new-chat icons for a compact header. */}
              {sidebar && (
                <h1 className="truncate text-base font-semibold sm:text-lg">
                  金乌
                </h1>
              )}
            </div>
            <div className="flex items-center gap-0.5">
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleSidebar}
                aria-label={sidebarToggleLabel}
                className="relative size-8"
              >
                {sidebar ? (
                  <PanelLeftClose
                    className="size-5"
                    aria-hidden="true"
                  />
                ) : (
                  <PanelLeft
                    className="size-5"
                    aria-hidden="true"
                  />
                )}
                {interruptCount > 0 && (
                  <span className="absolute right-0 top-0 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">
                    {interruptCount}
                  </span>
                )}
              </Button>
              {!sidebar && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={startNewChat}
                  aria-label="New chat"
                  className="size-8"
                >
                  <SquarePen
                    className="size-5"
                    aria-hidden="true"
                  />
                </Button>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1 sm:gap-2">
            <HealthIndicator
              deploymentUrl={config.deploymentUrl}
              onReconnect={(url) =>
                handleSaveConfig({ ...config, deploymentUrl: url })
              }
            />
            <ThemeToggle />
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleInspector}
              aria-label={inspector ? "Hide inspector" : "Show workspace"}
              title={inspector ? "Hide inspector" : "Show workspace"}
              className="size-8"
            >
              {inspector ? (
                <PanelRightClose
                  className="size-5"
                  aria-hidden="true"
                />
              ) : (
                <PanelRight
                  className="size-5"
                  aria-hidden="true"
                />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setConfigDialogOpen(true)}
              aria-label="Settings"
              title="Settings"
              className="size-8"
            >
              <Settings
                className="size-5"
                aria-hidden="true"
              />
            </Button>
          </div>
        </header>

        <div className="relative flex-1 overflow-hidden">
          {sidebar && isDesktopLayout === false && (
            <div className="absolute inset-0 z-40 flex md:hidden">
              <button
                type="button"
                aria-label="Close research"
                className="absolute inset-0 bg-black/40"
                onClick={closeSidebar}
              />
              <aside
                aria-label={view ? "Navigation" : "Research navigation"}
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
              </aside>
            </div>
          )}
          {inspector && isDesktopLayout === false && (
            <div className="absolute inset-0 z-40 flex justify-end md:hidden">
              <button
                type="button"
                aria-label="Close inspector"
                className="absolute inset-0 bg-black/40"
                onClick={closeInspector}
              />
              <aside
                aria-label="Inspector"
                className="relative z-10 h-full w-[min(22rem,calc(100vw-2.25rem))] bg-background shadow-xl"
              >
                <InspectorPanel
                  onClose={closeInspector}
                  onReportToMainChat={notifyMainChat}
                />
              </aside>
            </div>
          )}
          <RealtimeActivityProvider>
            <ResizablePanelGroup
              direction="horizontal"
              autoSaveId="jinwu-chat"
            >
            {sidebar && isDesktopLayout && (
              <>
                <ResizablePanel
                  id="thread-history"
                  order={1}
                  defaultSize={23}
                  minSize={18}
                  className="relative min-w-[260px]"
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
              {view === "schedule" && <ScheduledTasksPanel />}
            </ResizablePanel>

            {inspector && isDesktopLayout && (
              <>
                <ResizableHandle />
                <ResizablePanel
                  id="inspector"
                  order={3}
                  defaultSize={26}
                  minSize={20}
                  className="relative min-w-[300px]"
                >
                  <InspectorPanel
                    onClose={closeInspector}
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
            <p className="mt-2 text-muted-foreground">
              配置你的部署以开始使用
            </p>
            <Button
              onClick={() => setConfigDialogOpen(true)}
              className="mt-4"
            >
              Open Configuration
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
          <p className="text-muted-foreground">Loading…</p>
        </div>
      }
    >
      <HomePageContent />
    </Suspense>
  );
}
