import { NextRequest, NextResponse } from "next/server";
import { getCatalog } from "@/lib/server/skills";
import {
  bundledSkillsAsCatalog,
  readBundledSkills,
} from "@/lib/server/builtinSkills.js";
import { resolve } from "node:path";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const projectRoots = [resolve(process.cwd(), ".."), resolve(process.cwd())];
  for (const projectRoot of projectRoots) {
    const bundled = await readBundledSkills(projectRoot);
    if (bundled.length > 0) {
      return NextResponse.json({
        skills: bundledSkillsAsCatalog(bundled),
        source: "builtin",
      });
    }
  }
  try {
    const force = request.nextUrl.searchParams.get("refresh") === "1";
    const skills = await getCatalog(force);
    return NextResponse.json({ skills });
  } catch (error) {
    // The historical external JWSkills repository is not available in every
    // deployment. JW's bundled, read-only Skills remain an authoritative
    // catalog and should not disappear merely because GitHub is unavailable.
    for (const projectRoot of projectRoots) {
      const bundled = await readBundledSkills(projectRoot);
      if (bundled.length > 0) {
        return NextResponse.json({
          skills: bundledSkillsAsCatalog(bundled),
          source: "builtin",
        });
      }
    }
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
