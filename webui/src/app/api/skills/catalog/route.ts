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
            : "Failed to load the skills catalog.",
      },
      { status: 502 }
    );
  }
}
