import { NextRequest, NextResponse } from "next/server";
import {
  ensureThreadWorkspace,
  hasControlChar,
  isCrossOrigin,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

interface BindWorkspaceRequest {
  threadId?: unknown;
  projectId?: unknown;
}

/**
 * Create the persistent thread-to-workspace binding before an upload or the
 * first model run. The Python backend consumes the same binding registry when
 * it scopes file tools to this task.
 */
export async function POST(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin workspace binding is not allowed." },
        { status: 403 }
      );
    }

    let body: BindWorkspaceRequest;
    try {
      body = (await request.json()) as BindWorkspaceRequest;
    } catch {
      return NextResponse.json(
        { error: "A JSON request body is required." },
        { status: 400 }
      );
    }

    const threadId =
      typeof body.threadId === "string" ? body.threadId.trim() : "";
    const projectId =
      typeof body.projectId === "string" ? body.projectId.trim() : "default";
    if (!threadId || hasControlChar(threadId)) {
      return NextResponse.json(
        { error: "A valid task thread is required." },
        { status: 400 }
      );
    }
    if (!projectId || hasControlChar(projectId)) {
      return NextResponse.json(
        { error: "A valid project is required." },
        { status: 400 }
      );
    }

    const binding = await ensureThreadWorkspace(threadId, projectId);
    return NextResponse.json({ binding });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to bind the task workspace.",
      },
      { status: 400 }
    );
  }
}
