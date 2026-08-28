import { promises as fs } from "fs";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  isCrossOrigin,
  resolveInside,
  safeResolve,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

type RunState = {
  schema_version?: unknown;
  task_id?: unknown;
  status?: unknown;
  current_stage?: unknown;
  revision_policy?: unknown;
  action_invocations?: unknown;
  max_action_invocations?: unknown;
  review_invocations?: unknown;
  max_review_invocations?: unknown;
  stage_status?: unknown;
  artifacts?: unknown;
  verdicts?: unknown;
  updated_at?: unknown;
};

type Artifact = {
  schema_version?: unknown;
  task_id?: unknown;
  artifact_id?: unknown;
  stage?: unknown;
  version?: unknown;
  artifact_sha256?: unknown;
};

type ReviewIssue = {
  issue_id?: unknown;
  rule_id?: unknown;
  severity?: unknown;
  owner?: unknown;
  message?: unknown;
};

type ToolFailureReceipt = {
  schema_version?: unknown;
  task_id?: unknown;
  stage?: unknown;
  producer?: unknown;
  reason_code?: unknown;
  failure_count?: unknown;
  failure_summaries?: unknown;
  recovery?: unknown;
  created_at?: unknown;
};

type Verdict = {
  task_id?: unknown;
  review_mode?: unknown;
  policy_version?: unknown;
  artifact_refs?: unknown;
  round?: unknown;
  decision?: unknown;
  issues?: unknown;
  carry_forward_limits?: unknown;
  created_at?: unknown;
};

type PortfolioRankingRow = {
  hypothesis_id?: unknown;
  support_rank?: unknown;
  research_priority_rank?: unknown;
  claim_type?: unknown;
  scientific_support?: unknown;
  research_priority?: unknown;
  strongest_null_hypothesis?: unknown;
  next_experiment?: unknown;
  release_boundary?: unknown;
  portfolio_role?: unknown;
  portfolio_status?: unknown;
  forecast_origin?: unknown;
  forecast_receipt_ref?: unknown;
};

function boundedForecastReceiptRef(value: unknown): string | null {
  const receipt = safeString(value);
  return /^experiment\/runs\/[^/]+\/forecast_experiment_receipt\.json$/.test(
    receipt
  )
    ? receipt
    : null;
}

function boundedAssessment(value: unknown) {
  const row = value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
  return {
    level: safeString(row.level, "low"),
    rationale: safeString(row.rationale).slice(0, 1_000),
  };
}

function boundedNextExperiment(value: unknown) {
  const row = value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
  return {
    objective: safeString(row.objective).slice(0, 1_000),
    discriminatingPower: safeString(row.discriminating_power).slice(0, 1_000),
    feasibility: safeString(row.feasibility),
  };
}

async function readPortfolioRanking(workspace: string) {
  const state = await readJson(
    resolveInside(workspace, "work/scientific_hypothesis_state.json")
  );
  const ranking =
    state?.portfolio_ranking && typeof state.portfolio_ranking === "object"
      ? (state.portfolio_ranking as Record<string, unknown>)
      : null;
  const tailReview =
    state?.tail_review && typeof state.tail_review === "object"
      ? (state.tail_review as Record<string, unknown>)
      : null;
  const rankingPool = safeString(
    state?.portfolio_ranking_candidate_pool_sha256
  );
  const rankingEvidence = safeString(state?.portfolio_ranking_evidence_sha256);
  if (
    !ranking ||
    ranking.schema_version !== "scientific-hypothesis-portfolio-ranking-v2" ||
    !Array.isArray(ranking.ranked_hypotheses) ||
    !tailReview ||
    !rankingPool ||
    rankingPool !== safeString(tailReview.selected_candidate_pool_sha256) ||
    !rankingEvidence ||
    rankingEvidence !== safeString(tailReview.evidence_sha256)
  ) {
    return undefined;
  }
  const groups = new Map<string, string>();
  if (Array.isArray(ranking.hypothesis_groups)) {
    for (const value of ranking.hypothesis_groups) {
      if (!value || typeof value !== "object") continue;
      const group = value as Record<string, unknown>;
      const id = safeString(group.hypothesis_id);
      const statement = safeString(group.normalized_statement);
      if (id && statement) groups.set(id, statement.slice(0, 1_000));
    }
  }
  const rankedHypotheses = ranking.ranked_hypotheses
    .filter(
      (value): value is PortfolioRankingRow =>
        Boolean(value && typeof value === "object")
    )
    .map((row) => {
      const hypothesisId = safeString(row.hypothesis_id);
      return {
        statement: groups.get(hypothesisId) ?? "未命名假设",
        supportRank: safeNumber(row.support_rank),
        researchPriorityRank: safeNumber(row.research_priority_rank),
        claimType: safeString(row.claim_type),
        scientificSupport: boundedAssessment(row.scientific_support),
        researchPriority: boundedAssessment(row.research_priority),
        strongestNull: safeString(row.strongest_null_hypothesis).slice(0, 1_000),
        nextExperiment: boundedNextExperiment(row.next_experiment),
        releaseBoundary: safeString(row.release_boundary).slice(0, 1_000),
        portfolioRole: safeString(row.portfolio_role, "challenger"),
        portfolioStatus: safeString(row.portfolio_status, "challenger_pool"),
        forecastOrigin: safeString(row.forecast_origin, "not_applicable"),
        forecastReceiptRef: boundedForecastReceiptRef(row.forecast_receipt_ref),
      };
    })
    .sort((left, right) => left.supportRank - right.supportRank)
    .slice(0, 8);
  if (rankedHypotheses.length === 0) return undefined;
  return {
    rankedHypotheses,
    selectedNextExperiment: boundedNextExperiment(
      ranking.selected_next_experiment
    ),
  };
}

async function readJson(path: string): Promise<Record<string, unknown> | null> {
  try {
    const value = JSON.parse(await fs.readFile(path, "utf-8")) as unknown;
    return value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function safeString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function safeNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function nextActionCode(
  runStatus: string,
  currentStage: string,
  stageStatus: Record<string, unknown>
): string {
  if (runStatus === "blocked") return "report_blocker";
  if (runStatus === "release_ready" || runStatus === "released") {
    return "deliver_release";
  }
  const current = safeString(stageStatus[currentStage], "pending");
  if (current === "pending") return "produce_stage_artifact";
  if (current === "produced") return "review_stage_artifact";
  if (current === "revise") return "revise_stage_artifact";
  if (current === "accepted" || current === "accepted_with_limits") {
    return "advance_research_graph";
  }
  return "continue_current_stage";
}

async function latestToolFailure(
  workspace: string,
  taskId: string,
  stage: string
): Promise<ToolFailureReceipt | null> {
  if (!stage) return null;
  const directory = resolveInside(
    workspace,
    `research_review/failures/${stage}`
  );
  let names: string[];
  try {
    names = await fs.readdir(directory);
  } catch {
    return null;
  }
  const receipts: ToolFailureReceipt[] = [];
  for (const name of names.filter((value) => value.endsWith(".json"))) {
    const payload = (await readJson(
      resolveInside(directory, name)
    )) as ToolFailureReceipt | null;
    if (
      payload &&
      safeString(payload.task_id) === taskId &&
      safeString(payload.stage) === stage
    ) {
      receipts.push(payload);
    }
  }
  receipts.sort((left, right) =>
    safeString(left.created_at).localeCompare(safeString(right.created_at))
  );
  return receipts.at(-1) ?? null;
}

export async function GET(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin review access is not allowed." },
        { status: 403 }
      );
    }
    const threadId = request.nextUrl.searchParams.get("threadId")?.trim();
    if (!threadId) {
      return NextResponse.json(
        { error: "threadId is required." },
        { status: 400 }
      );
    }
    const workspace = await getWorkspaceDir(threadId);
    const statePath = resolveInside(
      workspace,
      "research_review/run_state.json"
    );
    const state = (await readJson(statePath)) as RunState | null;
    if (!state || state.schema_version !== "research-run-state-v2") {
      return NextResponse.json(
        { active: false },
        { headers: { "Cache-Control": "no-store" } }
      );
    }

    const taskId = safeString(state.task_id);
    const latestArtifactByStage = new Map<string, Artifact>();
    const artifactPaths = Array.isArray(state.artifacts) ? state.artifacts : [];
    for (const relative of artifactPaths) {
      if (typeof relative !== "string") continue;
      const path = await safeResolve(workspace, relative);
      const artifact = (await readJson(path)) as Artifact | null;
      if (
        !artifact ||
        artifact.schema_version !== "research-artifact-v2" ||
        safeString(artifact.task_id) !== taskId
      ) {
        continue;
      }
      const stage = safeString(artifact.stage);
      if (!stage) continue;
      const existing = latestArtifactByStage.get(stage);
      if (
        !existing ||
        safeNumber(artifact.version) > safeNumber(existing.version)
      ) {
        latestArtifactByStage.set(stage, artifact);
      }
    }

    const latestByMode = new Map<string, Verdict>();
    const verdictPaths = Array.isArray(state.verdicts) ? state.verdicts : [];
    for (const relative of verdictPaths) {
      if (typeof relative !== "string") continue;
      const path = await safeResolve(workspace, relative);
      const verdict = (await readJson(path)) as Verdict | null;
      if (
        !verdict ||
        safeString(verdict.task_id) !== taskId ||
        !safeString(verdict.policy_version).startsWith("evidence-policy-v")
      )
        continue;
      const mode = safeString(verdict.review_mode);
      if (!mode) continue;
      const artifact = latestArtifactByStage.get(mode);
      if (!artifact) continue;
      const refs = Array.isArray(verdict.artifact_refs)
        ? verdict.artifact_refs
        : [];
      const matchesLatest = refs.some(
        (value) =>
          Boolean(value && typeof value === "object") &&
          safeString((value as Record<string, unknown>).artifact_id) ===
            safeString(artifact.artifact_id) &&
          safeNumber((value as Record<string, unknown>).version) ===
            safeNumber(artifact.version) &&
          safeString((value as Record<string, unknown>).artifact_sha256) ===
            safeString(artifact.artifact_sha256)
      );
      if (!matchesLatest) continue;
      const existing = latestByMode.get(mode);
      if (!existing || safeNumber(verdict.round) > safeNumber(existing.round)) {
        latestByMode.set(mode, verdict);
      }
    }

    const stageStatus =
      state.stage_status && typeof state.stage_status === "object"
        ? (state.stage_status as Record<string, unknown>)
        : {};
    const stages = Object.entries(stageStatus).map(([stage, status]) => {
      const verdict = latestByMode.get(stage);
      const artifact = latestArtifactByStage.get(stage);
      const rawIssues = Array.isArray(verdict?.issues) ? verdict.issues : [];
      const issues = rawIssues
        .filter((value): value is ReviewIssue =>
          Boolean(value && typeof value === "object")
        )
        .filter(
          (issue) => issue.severity === "critical" || issue.severity === "major"
        )
        .slice(0, 5)
        .map((issue) => ({
          issueId: safeString(issue.issue_id),
          severity: safeString(issue.severity),
          owner: safeString(issue.owner),
          message: safeString(issue.message).slice(0, 500),
        }));
      return {
        stage,
        status: safeString(status, "pending"),
        artifactVersion: safeNumber(artifact?.version),
        round: safeNumber(verdict?.round),
        decision: safeString(verdict?.decision),
        issues,
        limitations: Array.isArray(verdict?.carry_forward_limits)
          ? verdict.carry_forward_limits
              .filter((value): value is string => typeof value === "string")
              .slice(0, 5)
          : [],
      };
    });

    const runStatus = safeString(state.status, "active");
    const currentStage = safeString(state.current_stage);
    let terminal:
      | {
          status: "blocked";
          reasonCode: string;
          stage: string;
          producer?: string;
          failureCount?: number;
          summary?: string;
          recovery: "new_task_after_fix";
        }
      | undefined;
    if (runStatus === "blocked") {
      const failure = await latestToolFailure(workspace, taskId, currentStage);
      const summaries = Array.isArray(failure?.failure_summaries)
        ? failure.failure_summaries.filter(
            (value): value is string => typeof value === "string"
          )
        : [];
      terminal = {
        status: "blocked",
        reasonCode: safeString(failure?.reason_code, "UNRESOLVED_REVIEW_GATE"),
        stage: currentStage,
        producer: safeString(failure?.producer) || undefined,
        failureCount:
          safeNumber(failure?.failure_count) > 0
            ? safeNumber(failure?.failure_count)
            : undefined,
        summary: summaries[0]?.slice(0, 500),
        recovery: "new_task_after_fix",
      };
    }
    const portfolioRanking = await readPortfolioRanking(workspace);

    return NextResponse.json(
      {
        active: true,
        status: runStatus,
        currentStage,
        nextAction: nextActionCode(runStatus, currentStage, stageStatus),
        revisionPolicy: safeString(state.revision_policy),
        actionInvocations: safeNumber(state.action_invocations),
        maxActionInvocations: safeNumber(state.max_action_invocations),
        reviewInvocations: safeNumber(state.review_invocations),
        maxReviewInvocations: safeNumber(state.max_review_invocations),
        updatedAt: safeString(state.updated_at),
        stages,
        portfolioRanking,
        terminal,
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Unable to read review status.",
      },
      { status: 404 }
    );
  }
}
