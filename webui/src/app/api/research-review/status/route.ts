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
  severity?: unknown;
  owner?: unknown;
  message?: unknown;
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
        verdict.policy_version !== "evidence-policy-v2.5"
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

    return NextResponse.json(
      {
        active: true,
        status: safeString(state.status, "active"),
        currentStage: safeString(state.current_stage),
        revisionPolicy: safeString(state.revision_policy),
        actionInvocations: safeNumber(state.action_invocations),
        maxActionInvocations: safeNumber(state.max_action_invocations),
        reviewInvocations: safeNumber(state.review_invocations),
        maxReviewInvocations: safeNumber(state.max_review_invocations),
        updatedAt: safeString(state.updated_at),
        stages,
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
