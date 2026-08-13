import { promises as fs } from "fs";
import { extname } from "path";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  safeResolve,
  isHiddenEntry,
  isCrossOrigin,
} from "@/lib/server/workspace";
import {
  artifactCategory,
  artifactSource,
  sortAndDedupeArtifacts,
  type ArtifactCandidate,
} from "@/lib/artifacts";

export const runtime = "nodejs";

const MAX_ARTIFACTS = 1000;
const MAX_DEPTH = 12;
const ROOTS = ["outputs", "artifacts", "reports", "results"];

async function addFile(
  workspaceDir: string,
  path: string,
  output: ArtifactCandidate[]
): Promise<void> {
  if (output.length >= MAX_ARTIFACTS) return;
  const source = artifactSource(path);
  if (!source) return;
  try {
    const target = await safeResolve(workspaceDir, path);
    const stat = await fs.stat(target);
    if (!stat.isFile()) return;
    const ext = extname(path).slice(1).toLowerCase();
    output.push({
      path,
      name: path.split("/").pop() || path,
      ext,
      size: stat.size,
      mtime: stat.mtimeMs,
      category: artifactCategory(ext),
      source,
    });
  } catch {
    // Files can disappear while an agent is replacing its outputs. A later
    // poll will pick up the completed file, so omit transient entries.
  }
}

async function walkTrustedDirectory(
  workspaceDir: string,
  relativeDir: string,
  depth: number,
  output: ArtifactCandidate[]
): Promise<void> {
  if (depth > MAX_DEPTH || output.length >= MAX_ARTIFACTS) return;
  let dir: string;
  try {
    dir = await safeResolve(workspaceDir, relativeDir);
  } catch {
    return;
  }
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (output.length >= MAX_ARTIFACTS) return;
    if (entry.isSymbolicLink() || isHiddenEntry(entry.name)) continue;
    const child = `${relativeDir}/${entry.name}`;
    if (entry.isDirectory()) {
      await walkTrustedDirectory(workspaceDir, child, depth + 1, output);
    } else if (entry.isFile()) {
      await addFile(workspaceDir, child, output);
    }
  }
}

async function collectExperimentArtifacts(
  workspaceDir: string,
  output: ArtifactCandidate[]
): Promise<void> {
  let runsDir: string;
  try {
    runsDir = await safeResolve(workspaceDir, "experiment/runs");
  } catch {
    return;
  }
  let runs;
  try {
    runs = await fs.readdir(runsDir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const run of runs) {
    if (output.length >= MAX_ARTIFACTS) return;
    if (!run.isDirectory() || run.isSymbolicLink() || isHiddenEntry(run.name)) {
      continue;
    }
    const root = `experiment/runs/${run.name}`;
    await addFile(workspaceDir, `${root}/report.md`, output);
    await walkTrustedDirectory(workspaceDir, `${root}/public`, 0, output);
  }
}

export async function GET(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin workspace access is not allowed." },
        { status: 403 }
      );
    }
    const threadId = request.nextUrl.searchParams.get("threadId");
    const workspaceDir = await getWorkspaceDir(threadId);
    const candidates: ArtifactCandidate[] = [];
    for (const root of ROOTS) {
      await walkTrustedDirectory(workspaceDir, root, 0, candidates);
    }
    await collectExperimentArtifacts(workspaceDir, candidates);
    const artifacts = sortAndDedupeArtifacts(candidates, MAX_ARTIFACTS);
    return NextResponse.json({
      artifacts,
      truncated: candidates.length >= MAX_ARTIFACTS,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "无法加载当前会话的产物。",
      },
      { status: 400 }
    );
  }
}
