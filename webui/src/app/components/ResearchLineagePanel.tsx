"use client";

import { useMemo, useState } from "react";
import { useQueryState } from "nuqs";
import {
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  FileText,
  GitBranch,
  Loader2,
  MessageSquareText,
  Route,
  Wrench,
} from "lucide-react";
import { toast } from "sonner";
import { useChatContext } from "@/providers/ChatProvider";
import { cn } from "@/lib/utils";
import {
  buildResearchTurns,
  collectResearchRoutes,
  type ResearchArtifact,
  type ResearchArtifactCategory,
  type ResearchNode,
  type ResearchRoute,
  type ResearchTurn,
} from "@/lib/researchLineage";
import { detectFileLink, dispatchFileLink } from "@/lib/fileLink";
import { dispatchResearchMessageNavigation } from "@/lib/researchNavigation";

interface RouteView extends ResearchRoute {
  turns: ResearchTurn[];
  label: string;
}

const ARTIFACT_GROUPS: Array<{
  category: ResearchArtifactCategory;
  label: string;
}> = [
  { category: "docs", label: "论文与文档" },
  { category: "figures", label: "图表" },
  { category: "data", label: "数据" },
  { category: "code", label: "代码" },
  { category: "other", label: "其他" },
];

function groupCoreArtifacts(artifacts: ResearchArtifact[]) {
  return ARTIFACT_GROUPS.map((group) => ({
    ...group,
    artifacts: artifacts.filter(
      (artifact) =>
        artifact.importance === "core" && artifact.category === group.category
    ),
  })).filter((group) => group.artifacts.length > 0);
}

function turnSignature(turn: ResearchTurn | undefined): string {
  if (!turn) return "";
  return [
    turn.prompt,
    ...turn.nodes.map(
      (node) =>
        `${node.kind}:${node.id}:${(node.detail || node.summary).slice(0, 512)}`
    ),
  ].join("\u0000");
}

function boundedDetail(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(
    0,
    limit
  )}\n\n…内容过长，已截断。可点击节点标题定位到聊天原文。`;
}

function firstDifferentTurn(
  active: ResearchTurn[],
  candidate: ResearchTurn[]
): number {
  const length = Math.max(active.length, candidate.length);
  for (let index = 0; index < length; index += 1) {
    if (turnSignature(active[index]) !== turnSignature(candidate[index])) {
      return Math.min(index, Math.max(active.length - 1, 0));
    }
  }
  return Math.max(active.length - 1, 0);
}

function statusCopy(status: ResearchTurn["status"]): string {
  if (status === "running") return "进行中";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "已停止";
  return "已完成";
}

function statusClass(status: ResearchTurn["status"]): string {
  if (status === "running") return "text-[var(--brand)]";
  if (status === "failed") return "text-[var(--color-error)]";
  if (status === "cancelled") return "text-[var(--color-warning)]";
  return "text-[var(--color-success)]";
}

function NodeIcon({ node }: { node: ResearchNode }) {
  const className = "size-3.5";
  if (node.kind === "agent") return <Bot className={className} />;
  if (node.kind === "tool") return <Wrench className={className} />;
  return <MessageSquareText className={className} />;
}

function openLineageFile(path: string) {
  const detected = detectFileLink(path);
  if (detected) {
    dispatchFileLink(detected);
    return;
  }
  if (path.startsWith("/memories/")) {
    dispatchFileLink({
      kind: "memory",
      display: path,
      path: path.replace(/^\/memories\/+/, ""),
    });
    return;
  }
  dispatchFileLink({
    kind: "workspace",
    display: path,
    path: path.replace(/^\/+/, "").replace(/^\.\/+/, ""),
  });
}

export function ResearchLineagePanel() {
  const [threadId] = useQueryState("threadId");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [turnOpenState, setTurnOpenState] = useState<Record<string, boolean>>(
    {}
  );
  const {
    messages,
    files,
    stream,
    isLoading,
    isStopping,
    interrupt,
    subAgentActivity,
    asyncTasks,
    loadOlderBranchHistory,
    isOlderBranchHistoryLoading,
    hasMoreBranchHistory,
    isBranchHistoryExhausted,
  } = useChatContext();

  const turns = useMemo(
    () => buildResearchTurns(messages, files),
    [files, messages]
  );
  const activeBranch = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const metadata = stream.getMessagesMetadata(messages[index], index);
      if (metadata?.branch) return metadata.branch;
    }
    return stream.branch;
  }, [messages, stream]);
  const routes = useMemo<RouteView[]>(() => {
    const collected = collectResearchRoutes(stream.experimental_branchTree);
    return collected.map((route, index) => ({
      ...route,
      turns: buildResearchTurns(route.messages),
      label: `路线 ${index + 1}`,
    }));
  }, [stream.experimental_branchTree]);
  const selectedBranch = useMemo(() => {
    if (activeBranch) return activeBranch;
    const activeSignature = turns.map(turnSignature).join("\u0001");
    return (
      routes.find(
        (route) =>
          route.turns.map(turnSignature).join("\u0001") === activeSignature
      )?.path ?? ""
    );
  }, [activeBranch, routes, turns]);
  const routeOptionsByTurn = useMemo(() => {
    const grouped = new Map<number, RouteView[]>();
    if (routes.length < 2) return grouped;
    const selectedRoute = routes.find((route) => route.path === selectedBranch);
    const currentRoute: RouteView = selectedRoute ?? {
      path: selectedBranch,
      checkpointId: null,
      createdAt: null,
      messages,
      turns,
      label: "当前路线",
    };
    for (const route of routes) {
      if (route.path === currentRoute.path) continue;
      const index = firstDifferentTurn(currentRoute.turns, route.turns);
      const options = grouped.get(index) ?? [currentRoute];
      if (!options.some((option) => option.path === route.path)) {
        options.push(route);
      }
      grouped.set(index, options);
    }
    return grouped;
  }, [messages, routes, selectedBranch, turns]);
  const branchSwitchBlocked = Boolean(isLoading || isStopping || interrupt);
  const runningAsyncAgents = Object.values(asyncTasks ?? {}).filter((task) => {
    const status = (task as { status?: unknown })?.status;
    return status === "pending" || status === "running";
  }).length;

  if (!threadId) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-6 text-center">
        <Route className="text-[var(--brand)]/70 mb-3 size-9" />
        <p className="text-sm font-medium text-foreground">暂无科研脉络</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          打开一条研究会话后，这里会按每轮提问展示研究目标、主要结论与核心产物。
        </p>
      </div>
    );
  }

  if (turns.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
        此会话还没有可展示的研究步骤。
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-foreground">科研脉络</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {turns.length} 轮研究 · {routes.length || 1} 条已加载路线
            </p>
          </div>
          {runningAsyncAgents > 0 && (
            <span className="border-[var(--brand)]/30 bg-[var(--brand)]/10 flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] text-[var(--brand)]">
              <Loader2 className="size-3 animate-spin" />
              {runningAsyncAgents} 个 Agent
            </span>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <ol className="border-[var(--brand)]/30 relative ml-2 border-l pl-4">
          {turns.map((turn, turnIndex) => {
            const routeOptions = routeOptionsByTurn.get(turnIndex) ?? [];
            const coreArtifactGroups = groupCoreArtifacts(turn.artifacts);
            const coreArtifactCount = coreArtifactGroups.reduce(
              (total, group) => total + group.artifacts.length,
              0
            );
            const detailArtifacts = turn.artifacts.filter(
              (artifact) => artifact.importance === "detail"
            );
            const isTurnOpen =
              turnOpenState[turn.id] ?? turnIndex === turns.length - 1;
            return (
              <li
                key={turn.id}
                className="relative pb-4 last:pb-1"
              >
                <span
                  className={cn(
                    "absolute -left-[21px] top-3 flex size-3 items-center justify-center rounded-full border bg-sidebar",
                    turn.status === "running"
                      ? "border-[var(--brand)] shadow-[0_0_8px_var(--brand)]"
                      : "border-[var(--brand)]/60"
                  )}
                >
                  <span className="size-1 rounded-full bg-[var(--brand)]" />
                </span>
                <details
                  open={isTurnOpen}
                  onToggle={(event) => {
                    const isOpen = event.currentTarget.open;
                    setTurnOpenState((current) =>
                      current[turn.id] === isOpen
                        ? current
                        : { ...current, [turn.id]: isOpen }
                    );
                  }}
                  className="group rounded-lg border border-border bg-background/80 shadow-sm"
                >
                  <summary className="cursor-pointer list-none px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                    <div className="flex items-start gap-2">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.preventDefault();
                          dispatchResearchMessageNavigation({
                            messageId: turn.messageId,
                          });
                        }}
                        className="min-w-0 flex-1 text-left"
                        title="定位到这条用户消息"
                      >
                        <span className="block text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--brand)]">
                          第 {turnIndex + 1} 轮
                        </span>
                        <span className="mt-1 line-clamp-2 block text-xs font-medium leading-5 text-foreground">
                          {turn.title}
                        </span>
                      </button>
                      <ChevronDown className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                      <span
                        className={cn(
                          "flex items-center gap-1",
                          statusClass(turn.status)
                        )}
                      >
                        {turn.status === "running" ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : turn.status === "failed" ? (
                          <CircleAlert className="size-3" />
                        ) : (
                          <Check className="size-3" />
                        )}
                        {statusCopy(turn.status)}
                      </span>
                      <span>{coreArtifactCount} 个核心产物</span>
                      <span>{turn.nodes.length} 个执行步骤</span>
                    </div>
                  </summary>

                  <div className="border-t border-border px-3 pb-3 pt-2.5">
                    {routeOptions.length > 1 && (
                      <div className="border-[var(--brand)]/20 bg-[var(--brand)]/5 mb-3 rounded-md border p-2">
                        <div className="mb-1.5 flex items-center gap-1 text-[10px] font-medium text-[var(--brand)]">
                          <GitBranch className="size-3" />
                          研究路线
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {routeOptions.map((route) => {
                            const isActive = route.path === selectedBranch;
                            const targetTurn = route.turns[turnIndex];
                            return (
                              <button
                                key={
                                  route.path ||
                                  route.checkpointId ||
                                  route.label
                                }
                                type="button"
                                disabled={branchSwitchBlocked || isActive}
                                onClick={() => {
                                  const messageId =
                                    targetTurn?.messageId ??
                                    route.turns.at(-1)?.messageId;
                                  if (!messageId) return;
                                  dispatchResearchMessageNavigation({
                                    messageId,
                                    branch: route.path,
                                  });
                                }}
                                title={
                                  branchSwitchBlocked
                                    ? "当前会话正在运行或等待处理，暂不能切换路线"
                                    : targetTurn?.title
                                }
                                className={cn(
                                  "max-w-full rounded-full border px-2 py-1 text-[10px] transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                                  isActive
                                    ? "bg-[var(--brand)]/15 border-[var(--brand)] text-[var(--brand)]"
                                    : "hover:border-[var(--brand)]/50 border-border bg-background text-muted-foreground hover:text-foreground"
                                )}
                              >
                                {route.label}
                                {isActive ? " · 当前" : ""}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    <div className="space-y-3">
                      <section>
                        <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                          研究目标
                        </p>
                        <button
                          type="button"
                          onClick={() =>
                            dispatchResearchMessageNavigation({
                              messageId: turn.messageId,
                            })
                          }
                          className="hover:border-[var(--brand)]/50 w-full rounded-md border border-border bg-sidebar/60 px-2.5 py-2 text-left text-xs leading-5 text-foreground transition-colors"
                          title="定位到这条用户消息"
                        >
                          {boundedDetail(turn.prompt, 480)}
                        </button>
                      </section>

                      <section>
                        <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                          主要结论
                        </p>
                        {turn.finalAnswer ? (
                          <button
                            type="button"
                            onClick={() =>
                              dispatchResearchMessageNavigation({
                                messageId: turn.finalAnswer!.messageId,
                              })
                            }
                            className="border-[var(--brand)]/25 bg-[var(--brand)]/5 hover:border-[var(--brand)]/60 w-full rounded-md border px-2.5 py-2 text-left text-[11px] leading-5 text-foreground transition-colors"
                            title="定位到完整回答"
                          >
                            <span className="whitespace-pre-wrap">
                              {boundedDetail(turn.finalAnswer.detail, 520)}
                            </span>
                          </button>
                        ) : (
                          <p className="rounded-md border border-dashed border-border px-2.5 py-2 text-[10px] text-muted-foreground">
                            本轮尚未形成最终回答
                          </p>
                        )}
                      </section>

                      {turn.keyNodes.length > 0 && (
                        <section>
                          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                            关键过程
                          </p>
                          <div className="space-y-1.5">
                            {turn.keyNodes.map((node) => (
                              <button
                                key={node.id}
                                type="button"
                                onClick={() =>
                                  dispatchResearchMessageNavigation({
                                    messageId: node.messageId,
                                  })
                                }
                                className="hover:border-[var(--brand)]/50 flex w-full items-start gap-2 rounded-md border border-border bg-sidebar/60 px-2.5 py-2 text-left transition-colors"
                                title="定位到对应消息"
                              >
                                <span className="mt-0.5 text-[var(--brand)]">
                                  <NodeIcon node={node} />
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-xs font-medium text-foreground">
                                    {node.title}
                                  </span>
                                  {node.summary && (
                                    <span className="mt-0.5 line-clamp-2 block text-[10px] leading-4 text-muted-foreground">
                                      {node.summary}
                                    </span>
                                  )}
                                </span>
                                <span
                                  className={cn(
                                    "shrink-0 text-[9px]",
                                    statusClass(node.status)
                                  )}
                                >
                                  {statusCopy(node.status)}
                                </span>
                              </button>
                            ))}
                          </div>
                        </section>
                      )}

                      <section>
                        <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                          核心产物
                        </p>
                        {coreArtifactGroups.length > 0 ? (
                          <div className="space-y-2 rounded-md border border-border bg-sidebar/40 p-2">
                            {coreArtifactGroups.map((group) => (
                              <div key={group.category}>
                                <p className="mb-1 text-[9px] text-muted-foreground">
                                  {group.label}
                                </p>
                                <div className="flex flex-wrap gap-1">
                                  {group.artifacts.map((artifact) => (
                                    <button
                                      key={artifact.path}
                                      type="button"
                                      onClick={() =>
                                        openLineageFile(artifact.path)
                                      }
                                      className="hover:border-[var(--brand)]/50 flex max-w-full items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-left text-[10px] text-[var(--brand)]"
                                    >
                                      <FileText className="size-3 shrink-0" />
                                      <span className="truncate">
                                        {artifact.path}
                                      </span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="rounded-md border border-dashed border-border px-2.5 py-2 text-[10px] text-muted-foreground">
                            暂无可识别的核心产物
                          </p>
                        )}
                      </section>

                      {(turn.nodes.length > 0 ||
                        detailArtifacts.length > 0) && (
                        <details className="group/detail rounded-md border border-border/80 bg-sidebar/30">
                          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[10px] font-medium text-muted-foreground">
                            <span>
                              执行细节 · {turn.nodes.length} 个步骤
                              {detailArtifacts.length > 0
                                ? ` · ${detailArtifacts.length} 个其他文件`
                                : ""}
                            </span>
                            <ChevronDown className="size-3.5 shrink-0 transition-transform group-open/detail:rotate-180" />
                          </summary>
                          <div className="space-y-2 border-t border-border/70 p-2">
                            {turn.nodes.map((node) => {
                              const liveSteps = node.toolCallId
                                ? subAgentActivity[node.toolCallId] ??
                                  Object.entries(subAgentActivity).find(
                                    ([key]) => key.includes(node.toolCallId!)
                                  )?.[1]
                                : undefined;
                              return (
                                <details
                                  key={node.id}
                                  className="group/node rounded-md border border-border/80 bg-background/80"
                                >
                                  <summary className="cursor-pointer list-none px-2.5 py-2">
                                    <div className="flex items-start gap-2">
                                      <span className="mt-0.5 text-[var(--brand)]">
                                        <NodeIcon node={node} />
                                      </span>
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.preventDefault();
                                          dispatchResearchMessageNavigation({
                                            messageId: node.messageId,
                                          });
                                        }}
                                        className="min-w-0 flex-1 text-left"
                                        title="定位到对应消息"
                                      >
                                        <span className="block truncate text-xs font-medium text-foreground">
                                          {node.title}
                                        </span>
                                        {node.summary && (
                                          <span className="mt-0.5 line-clamp-2 block text-[10px] leading-4 text-muted-foreground">
                                            {node.summary}
                                          </span>
                                        )}
                                      </button>
                                      <ChevronDown className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open/node:rotate-180" />
                                    </div>
                                  </summary>
                                  <div className="space-y-2 border-t border-border/70 px-2.5 py-2 text-[10px] leading-4">
                                    {node.args &&
                                      Object.keys(node.args).length > 0 && (
                                        <div>
                                          <p className="mb-1 font-medium text-muted-foreground">
                                            参数
                                          </p>
                                          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-foreground">
                                            {boundedDetail(
                                              JSON.stringify(
                                                node.args,
                                                null,
                                                2
                                              ),
                                              8000
                                            )}
                                          </pre>
                                        </div>
                                      )}
                                    {node.detail && (
                                      <div>
                                        <p className="mb-1 font-medium text-muted-foreground">
                                          输出
                                        </p>
                                        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-background p-2 font-sans text-foreground">
                                          {boundedDetail(node.detail, 12000)}
                                        </pre>
                                      </div>
                                    )}
                                    {liveSteps && liveSteps.length > 0 && (
                                      <p className="text-[var(--brand)]">
                                        当前已捕获 {liveSteps.length}{" "}
                                        个实时子步骤
                                      </p>
                                    )}
                                    {node.files.length > 0 && (
                                      <div className="flex flex-wrap gap-1">
                                        {node.files.map((path) => (
                                          <button
                                            key={path}
                                            type="button"
                                            onClick={() =>
                                              openLineageFile(path)
                                            }
                                            className="hover:border-[var(--brand)]/50 flex max-w-full items-center gap-1 rounded border border-border bg-background px-1.5 py-1 text-left text-[var(--brand)]"
                                          >
                                            <FileText className="size-3 shrink-0" />
                                            <span className="truncate">
                                              {path}
                                            </span>
                                          </button>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                </details>
                              );
                            })}

                            {detailArtifacts.length > 0 && (
                              <div className="border-t border-border/70 pt-2">
                                <p className="mb-1.5 text-[10px] font-medium text-muted-foreground">
                                  其他关联文件
                                </p>
                                <div className="flex flex-wrap gap-1">
                                  {detailArtifacts.map((artifact) => (
                                    <button
                                      key={artifact.path}
                                      type="button"
                                      onClick={() =>
                                        openLineageFile(artifact.path)
                                      }
                                      className="hover:border-[var(--brand)]/50 flex max-w-full items-center gap-1 rounded border border-border bg-background px-1.5 py-1 text-left text-[var(--brand)]"
                                    >
                                      <FileText className="size-3 shrink-0" />
                                      <span className="truncate">
                                        {artifact.path}
                                      </span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </details>
                      )}
                    </div>
                  </div>
                </details>
              </li>
            );
          })}
        </ol>

        {(hasMoreBranchHistory ||
          isOlderBranchHistoryLoading ||
          historyError ||
          isBranchHistoryExhausted) && (
          <div className="mt-3 px-2 pb-2 text-center">
            {historyError && (
              <p className="mb-2 text-[10px] text-[var(--color-error)]">
                {historyError}
              </p>
            )}
            {isBranchHistoryExhausted && !historyError ? (
              <div className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-muted-foreground">
                <Check className="size-3.5" />
                {routes.length > 1 ? "已加载全部分支" : "无更早分支"}
              </div>
            ) : (
              <button
                type="button"
                disabled={isOlderBranchHistoryLoading}
                onClick={async () => {
                  setHistoryError(null);
                  try {
                    await loadOlderBranchHistory();
                  } catch (error) {
                    const message =
                      error instanceof Error
                        ? error.message
                        : "无法加载更早的研究路线。";
                    setHistoryError(message);
                    toast.error(message);
                  }
                }}
                className="hover:border-[var(--brand)]/50 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:cursor-wait disabled:opacity-60"
              >
                {isOlderBranchHistoryLoading ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <GitBranch className="size-3.5" />
                )}
                {isOlderBranchHistoryLoading
                  ? "正在加载更早路线…"
                  : historyError
                  ? "重试加载更早分支"
                  : "加载更早分支"}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
