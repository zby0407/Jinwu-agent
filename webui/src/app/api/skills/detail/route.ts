import { NextRequest, NextResponse } from "next/server";
import { getSkillDetail } from "@/lib/server/skills";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    const name = request.nextUrl.searchParams.get("name");
    if (!name) {
      return NextResponse.json(
        { error: "Missing skill name." },
        { status: 400 }
      );
    }
    const detail = await getSkillDetail(name);
    return NextResponse.json(detail);
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to load skill detail.",
      },
      { status: 400 }
    );
  }
}
