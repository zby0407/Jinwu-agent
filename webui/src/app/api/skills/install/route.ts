import { NextRequest, NextResponse } from "next/server";
import { installSkill } from "@/lib/server/skills";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    // Same-origin guard (browsers always send Origin on POST).
    const origin = request.headers.get("origin");
    if (origin && origin !== request.nextUrl.origin) {
      return NextResponse.json(
        { error: "Cross-origin installs are not allowed." },
        { status: 403 }
      );
    }

    const body = (await request.json().catch(() => null)) as {
      name?: unknown;
    } | null;
    const name = body?.name;
    if (typeof name !== "string" || !name) {
      return NextResponse.json(
        { error: "Missing skill name." },
        { status: 400 }
      );
    }

    const result = await installSkill(name);
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to install skill.",
      },
      { status: 400 }
    );
  }
}
