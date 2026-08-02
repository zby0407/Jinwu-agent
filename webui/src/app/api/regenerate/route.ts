import { NextRequest, NextResponse } from "next/server";
import {
  isCrossOrigin,
  resetGeneratedWorkspace,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin workspace access is not allowed." },
        { status: 403 }
      );
    }
    const body = (await request.json().catch(() => null)) as {
      threadId?: unknown;
    } | null;
    if (!body || typeof body.threadId !== "string" || !body.threadId.trim()) {
      return NextResponse.json(
        { error: "A task thread is required." },
        { status: 400 }
      );
    }

    await resetGeneratedWorkspace(body.threadId);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
          : "清除已生成产物失败。",
      },
      { status: 400 }
    );
  }
}
