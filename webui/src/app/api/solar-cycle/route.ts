import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { NextResponse } from "next/server";
import type { CycleArchive } from "@/lib/todaySun";

export const dynamic = "force-dynamic";

const headers = { "Cache-Control": "no-store" };

function cachePath() {
  const cwd = process.cwd();
  const webui = basename(cwd) === "dist" ? dirname(cwd) : cwd;
  const checkout = basename(webui) === "webui" ? dirname(webui) : cwd;
  const workspace = process.env.JW_WORKSPACE_DIR || join(checkout, "workspace");
  return join(workspace, "solar-cycle", "cycle.json");
}

export async function GET() {
  try {
    const archive: CycleArchive = JSON.parse(await readFile(cachePath(), "utf8"));
    if (archive.version !== 1 || !Array.isArray(archive.cycles)) throw new Error("Invalid cycle archive");
    return NextResponse.json(archive, { headers });
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT"
      ? NextResponse.json(null, { headers })
      : NextResponse.json({ error: "Cycle archive unavailable" }, { status: 503, headers });
  }
}
