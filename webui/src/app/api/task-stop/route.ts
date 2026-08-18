import { promises as fs } from "fs";
import { join } from "path";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  isCrossOrigin,
  safeResolve,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

type ExperimentState = {
  phase?: unknown;
  current_attempt?: unknown;
  cancel_requested?: unknown;
  last_error?: unknown;
};

async function stopExperimentRun(runDir: string): Promise<boolean> {
  const statePath = join(runDir, "state.json");
  let state: ExperimentState;
  try {
    state = JSON.parse(
      await fs.readFile(statePath, "utf-8")
    ) as ExperimentState;
  } catch {
    return false;
  }
  if (state.phase === "report_finalized") return false;

  await fs.writeFile(
    join(runDir, "cancel.requested"),
    `${new Date().toISOString()}\n`
  );
  state.cancel_requested = true;
  state.last_error = "用户停止了父任务；禁止继续或创建新的实验尝试。";
  const temporary = `${statePath}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(state, null, 2) + "\n", "utf-8");
  await fs.rename(temporary, statePath);

  if (typeof state.current_attempt === "string") {
    try {
      const rawPid = await fs.readFile(
        join(runDir, "attempts", state.current_attempt, "sandbox.pid"),
        "ascii"
      );
      const pid = Number.parseInt(rawPid.trim(), 10);
      if (
        Number.isSafeInteger(pid) &&
        pid > 1 &&
        process.platform !== "win32"
      ) {
        // The trusted experiment supervisor starts a dedicated process group.
        process.kill(-pid, "SIGTERM");
      }
    } catch {
      // No active sandbox (or it already exited). The persisted marker still
      // prevents the executor from continuing.
    }
  }
  return true;
}

export async function POST(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin stop is not allowed." },
        { status: 403 }
      );
    }
    const body = (await request.json()) as { threadId?: unknown };
    if (typeof body.threadId !== "string" || !body.threadId.trim()) {
      return NextResponse.json(
        { error: "必须提供 threadId。" },
        { status: 400 }
      );
    }
    const workspace = await getWorkspaceDir(body.threadId);
    const receipts = await safeResolve(workspace, "receipts");
    await fs.mkdir(receipts, { recursive: true });
    await fs.writeFile(
      join(receipts, "task_cancelled.json"),
      JSON.stringify(
        {
          schema_version: 1,
          thread_id: body.threadId,
          cancelled_at: new Date().toISOString(),
        },
        null,
        2
      ) + "\n",
      "utf-8"
    );

    let entries: string[] = [];
    let runsRoot: string;
    try {
      runsRoot = await safeResolve(workspace, "experiment/runs");
      entries = await fs.readdir(runsRoot);
    } catch {
      return NextResponse.json({ status: "cancelled", experimentsStopped: 0 });
    }
    let experimentsStopped = 0;
    for (const entry of entries) {
      const runDir = join(runsRoot, entry);
      try {
        const stat = await fs.stat(runDir);
        if (stat.isDirectory() && (await stopExperimentRun(runDir))) {
          experimentsStopped += 1;
        }
      } catch {
        // A run may finish or disappear while cancellation is being applied.
      }
    }
    return NextResponse.json({ status: "cancelled", experimentsStopped });
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "无法停止任务。",
      },
      { status: 500 }
    );
  }
}
