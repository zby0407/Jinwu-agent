import { NextRequest, NextResponse } from "next/server";
import { getCatalog } from "@/lib/server/skills";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    const force = request.nextUrl.searchParams.get("refresh") === "1";
    const skills = await getCatalog(force);
    return NextResponse.json({ skills });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "加载 Skills 目录失败。",
      },
      { status: 502 }
    );
  }
}
