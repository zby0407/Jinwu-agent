"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleDashed,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { describeResearchTerminal } from "@/lib/researchReviewTerminal";
import {
  FORECAST_ORIGIN_LABELS,
  PORTFOLIO_ROLE_LABELS,
  PORTFOLIO_STATUS_LABELS,
  describePortfolioSummary,
} from "@/lib/researchPortfolio";

type StageIssue = {
  issueId: string;
  severity: string;
  owner: string;
  message: string;
};

type ReviewStage = {
  stage: string;
  status: string;
  artifactVersion: number;
  round: number;
  decision: string;
  issues: StageIssue[];
  limitations: string[];
};

type RankingAssessment = {
  level: string;
  rationale: string;
};

type NextExperiment = {
  objective: string;
  discriminatingPower: string;
  feasibility: string;
};

type PortfolioRankingRow = {
  statement: string;
  supportRank: number;
  researchPriorityRank: number;
  claimType: string;
  scientificSupport: RankingAssessment;
  researchPriority: RankingAssessment;
  strongestNull: string;
  nextExperiment: NextExperiment;
  releaseBoundary: string;
  portfolioRole: string;
  portfolioStatus: string;
  forecastOrigin: string;
  forecastReceiptRef: string | null;
};

type ReviewStatus = {
  active: boolean;
  status?: string;
  currentStage?: string;
  nextAction?: string;
  revisionPolicy?: string;
  actionInvocations?: number;
  maxActionInvocations?: number;
  reviewInvocations?: number;
  maxReviewInvocations?: number;
  stages?: ReviewStage[];
  portfolioRanking?: {
    rankedHypotheses: PortfolioRankingRow[];
    selectedNextExperiment: NextExperiment;
  };
  execution?: {
    status: string;
    stage: string;
    updatedAt: string;
    reason: string;
  };
  terminal?: {
    status: "blocked";
    reasonCode: string;
    stage: string;
    producer?: string;
    failureCount?: number;
    summary?: string;
    recovery: "new_task_after_fix";
  };
  updatedAt?: string;
};

const LABELS: Record<string, string> = {
  planning: "规划",
  data: "数据",
  hypothesis: "假设",
  experiment_design: "实验设计",
  experiment_result: "实验结果",
  integration: "综合审查",
  final_release: "发布审查",
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  produce_stage_artifact: "生成当前阶段产物",
  review_stage_artifact: "核查当前阶段证据",
  revise_stage_artifact: "按审查意见返修",
  advance_research_graph: "进入下一研究阶段",
  continue_current_stage: "继续当前研究阶段",
  deliver_release: "交付已审查研究包",
  report_blocker: "报告阻塞原因和可恢复路径",
};

const LEVEL_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const CLAIM_TYPE_LABELS: Record<string, string> = {
  descriptive_relationship: "描述性关系",
  predictive: "预测性主张",
  mechanism_candidate: "机制候选",
  null_hypothesis: "零假设",
  measurement_explanation: "测量解释",
};

function statusTone(status: string): string {
  if (status === "accepted" || status === "accepted_with_limits") {
    return "border-[var(--color-success)]/35 bg-[var(--color-success)]/10 text-[var(--color-success)]";
  }
  if (status === "blocked") {
    return "border-destructive/35 bg-destructive/10 text-destructive";
  }
  if (status === "revise") {
    return "border-amber-500/35 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  }
  return "border-border bg-muted/40 text-muted-foreground";
}

export function ResearchReviewPanel({
  threadId,
  isLoading,
}: {
  threadId: string;
  isLoading: boolean;
}) {
  const [data, setData] = useState<ReviewStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setData(null);
    const load = async () => {
      try {
        const response = await fetch(
          `/api/research-review/status?${new URLSearchParams({ threadId })}`,
          { cache: "no-store" }
        );
        const payload = (await response
          .json()
          .catch(() => null)) as ReviewStatus | null;
        if (!cancelled && response.ok) setData(payload);
      } catch {
        if (!cancelled) setData(null);
      } finally {
        if (!cancelled) timer = setTimeout(load, isLoading ? 2000 : 5000);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isLoading, threadId]);

  const majorIssues = useMemo(
    () => (data?.stages ?? []).flatMap((stage) => stage.issues),
    [data]
  );
  const limitations = useMemo(
    () =>
      Array.from(
        new Set((data?.stages ?? []).flatMap((stage) => stage.limitations))
      ),
    [data]
  );
  const portfolioSummary = useMemo(
    () =>
      describePortfolioSummary(
        data?.portfolioRanking?.rankedHypotheses ?? []
      ),
    [data]
  );
  if (!data?.active) return null;

  const terminal = data.status === "blocked";
  const ready = data.status === "release_ready" || data.status === "released";
  const HeaderIcon = terminal
    ? ShieldAlert
    : ready
    ? ShieldCheck
    : CircleDashed;
  const terminalCopy = data.terminal
    ? describeResearchTerminal(data.terminal)
    : null;

  return (
    <section
      aria-label="科研证据审查状态"
      className="mb-4 rounded-lg border border-border bg-card/70 p-3 shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <HeaderIcon
            className={cn(
              "size-4",
              terminal
                ? "text-destructive"
                : ready
                ? "text-[var(--color-success)]"
                : "text-[var(--brand)]"
            )}
            aria-hidden="true"
          />
          <span className="text-sm font-semibold">科研证据审查</span>
          <span className="text-xs text-muted-foreground">
            {data.revisionPolicy === "adaptive" ? "动态返修" : "固定返修"} ·{" "}
            {data.actionInvocations ?? 0}/{data.maxActionInvocations ?? 0}{" "}
            次动作 · {data.reviewInvocations ?? 0}/
            {data.maxReviewInvocations ?? 0} 次审查
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {(data.stages ?? []).map((stage) => (
          <span
            key={stage.stage}
            title={stage.decision || stage.status}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px]",
              statusTone(stage.status)
            )}
          >
            {stage.status === "accepted" ||
            stage.status === "accepted_with_limits" ? (
              <CheckCircle2
                className="size-3"
                aria-hidden="true"
              />
            ) : (
              <CircleDashed
                className="size-3"
                aria-hidden="true"
              />
            )}
            {LABELS[stage.stage] ?? stage.stage}
            {stage.artifactVersion > 0 ? ` · v${stage.artifactVersion}` : ""}
            {stage.round > 0 ? ` / R${stage.round}` : ""}
          </span>
        ))}
      </div>

      <div className="mt-3 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">当前阶段：</span>
        {LABELS[data.currentStage ?? ""] ?? data.currentStage ?? "准备中"}
        <span
          className="mx-2"
          aria-hidden="true"
        >
          ·
        </span>
        <span className="font-medium text-foreground">下一步：</span>
        {NEXT_ACTION_LABELS[data.nextAction ?? ""] ?? "读取运行状态"}
        {data.execution && (
          <>
            <span
              className="mx-2"
              aria-hidden="true"
            >
              ·
            </span>
            <span className="font-medium text-foreground">执行状态：</span>
            {data.execution.reason}
          </>
        )}
      </div>

      {(data.portfolioRanking?.rankedHypotheses.length ?? 0) > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-semibold text-foreground">
              假设组合判断
            </p>
            <p className="text-[11px] text-muted-foreground">
              {portfolioSummary.label} · 科学支持度与研究优先级分别排序
            </p>
          </div>
          <div className="mt-2 grid gap-2 lg:grid-cols-2">
            {data.portfolioRanking?.rankedHypotheses.map((row) => (
              <article
                key={`${row.statement}-${row.supportRank}-${row.researchPriorityRank}`}
                className="rounded-md border border-border bg-background/60 p-3"
              >
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="rounded-full border border-sky-500/35 bg-sky-500/10 px-2 py-0.5 text-foreground">
                    {(PORTFOLIO_ROLE_LABELS as Record<string, string>)[
                      row.portfolioRole
                    ] ?? "挑战者"}{" "}
                    /{" "}
                    {(PORTFOLIO_STATUS_LABELS as Record<string, string>)[
                      row.portfolioStatus
                    ] ?? "挑战池"}
                  </span>
                  {row.forecastOrigin !== "not_applicable" && (
                    <span className="rounded-full border border-violet-500/35 bg-violet-500/10 px-2 py-0.5 text-foreground">
                      预测起点：
                      {(FORECAST_ORIGIN_LABELS as Record<string, string>)[
                        row.forecastOrigin
                      ] ?? row.forecastOrigin}
                    </span>
                  )}
                  <span className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">
                    {CLAIM_TYPE_LABELS[row.claimType] ?? "研究假设"}
                  </span>
                  <span className="rounded-full border border-[var(--brand)]/30 bg-[var(--brand)]/10 px-2 py-0.5 text-foreground">
                    科学支持度 #{row.supportRank} · {LEVEL_LABELS[row.scientificSupport.level] ?? row.scientificSupport.level}
                  </span>
                  <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-foreground">
                    研究优先级 #{row.researchPriorityRank} · {LEVEL_LABELS[row.researchPriority.level] ?? row.researchPriority.level}
                  </span>
                </div>
                <p className="mt-2 text-xs font-medium leading-relaxed text-foreground">
                  {row.statement}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">支持度依据：</span>
                  {row.scientificSupport.rationale}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">优先级依据：</span>
                  {row.researchPriority.rationale}
                </p>
                {row.strongestNull && (
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                    <span className="font-medium text-foreground">最强零假设：</span>
                    {row.strongestNull}
                  </p>
                )}
              </article>
            ))}
          </div>
          {data.portfolioRanking?.selectedNextExperiment.objective && (
            <div className="mt-2 rounded-md border border-[var(--brand)]/30 bg-[var(--brand)]/5 px-3 py-2">
              <p className="text-xs font-semibold text-foreground">
                选中的下一项区分性实验
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {data.portfolioRanking.selectedNextExperiment.objective}
              </p>
              {data.portfolioRanking.selectedNextExperiment.discriminatingPower && (
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">区分作用：</span>
                  {data.portfolioRanking.selectedNextExperiment.discriminatingPower}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {terminalCopy && (
        <div
          className="mt-3 rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2"
        >
          <p className="text-xs font-semibold text-foreground">
            {terminalCopy.title}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {terminalCopy.description}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {terminalCopy.action}
          </p>
        </div>
      )}

      {majorIssues.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="text-xs font-medium text-foreground">
            尚未解决的重大问题
          </p>
          <ul className="mt-1 space-y-1 pl-4 text-xs text-muted-foreground">
            {majorIssues.slice(0, 5).map((issue) => (
              <li
                key={issue.issueId}
                className="list-disc"
              >
                <span className="font-medium text-foreground">
                  {issue.owner}
                </span>
                {"："}
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {limitations.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="text-xs font-medium text-foreground">结论限制</p>
          <ul className="mt-1 space-y-1 pl-4 text-xs text-muted-foreground">
            {limitations.slice(0, 5).map((limitation) => (
              <li
                key={limitation}
                className="list-disc"
              >
                {limitation}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
