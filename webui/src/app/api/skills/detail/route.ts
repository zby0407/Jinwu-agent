import { NextRequest, NextResponse } from "next/server";
import { getSkillDetail } from "@/lib/server/skills";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    const name = request.nextUrl.searchParams.get("name");
    if (!name) {
      return NextResponse.json(
        { error: "缺少 Skill 名称。" },
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
            : "加载 Skill 详情失败。",
      },
      { status: 400 }
    );
  }
}
