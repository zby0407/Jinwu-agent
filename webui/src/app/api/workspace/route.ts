import { promises as fs } from "fs";
import { extname } from "path";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  safeResolve,
  isHiddenEntry,
  isCrossOrigin,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

// Cap how many entries a single directory listing returns so a pathological
// directory can't stall the UI or the response.
const MAX_ENTRIES = 2000;
// Bound recursive walks (the "by type" view) by depth too.
const MAX_DEPTH = 12;

export interface WorkspaceEntry {
  name: string;
  /** Path relative to the workspace root, e.g. "artifacts/report.md". */
  path: string;
  type: "dir" | "file";
  size: number;
  /** Last-modified epoch ms. */
  mtime: number;
  /** Lowercase extension without the dot, "" for none/dirs. */
  ext: string;
}

/**
 * Recursively collect *files* (not dirs) under `relDir`, skipping the hidden/
 * noise set and never following symlinks (so it can't loop or escape). Used by
 * the artifacts "by type" view. Bounded by MAX_DEPTH and MAX_ENTRIES.
 */
async function walkFiles(
  workspaceDir: string,
  relDir: string,
  depth: number,
  out: WorkspaceEntry[]
): Promise<void> {
  if (depth > MAX_DEPTH || out.length >= MAX_ENTRIES) return;
  let dir: string;
  try {
    dir = await safeResolve(workspaceDir, relDir || "");
  } catch {
    return;
  }
  let dirents;
  try {
    dirents = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const dirent of dirents) {
    if (out.length >= MAX_ENTRIES) break;
    if (isHiddenEntry(dirent.name)) continue;
    // Don't follow symlinks during the recursive walk — avoids loops and any
    // escape; a symlinked file is simply omitted from the by-type view.
    if (dirent.isSymbolicLink()) continue;
    const childRel = relDir ? `${relDir}/${dirent.name}` : dirent.name;
    if (dirent.isDirectory()) {
      await walkFiles(workspaceDir, childRel, depth + 1, out);
    } else if (dirent.isFile()) {
      try {
        const st = await fs.stat(await safeResolve(workspaceDir, childRel));
        out.push({
          name: dirent.name,
          path: childRel,
          type: "file",
          size: st.size,
          mtime: st.mtimeMs,
          ext: extname(dirent.name).slice(1).toLowerCase(),
        });
      } catch {
        continue;
      }
    }
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

    const relPath = request.nextUrl.searchParams.get("path") ?? "";
    const recursive = request.nextUrl.searchParams.get("recursive") === "1";
    const workspaceDir = await getWorkspaceDir();
    const dir = await safeResolve(workspaceDir, relPath);

    const stat = await fs.stat(dir);
    if (!stat.isDirectory()) {
      return NextResponse.json({ error: "Not a directory." }, { status: 400 });
    }

    // "By type" view: flat list of every file under the workspace.
    if (recursive) {
      const files: WorkspaceEntry[] = [];
      await walkFiles(workspaceDir, relPath, 0, files);
      const truncated = files.length >= MAX_ENTRIES;
      return NextResponse.json({
        path: relPath,
        recursive: true,
        truncated,
        entries: files,
      });
    }

    const dirents = await fs.readdir(dir, { withFileTypes: true });
    const entries: WorkspaceEntry[] = [];
    for (const dirent of dirents) {
      if (entries.length >= MAX_ENTRIES) break;
      // Skip noise (dotfiles, internal dirs, build artifacts) — not research
      // artifacts. See isHiddenEntry for the full policy.
      if (isHiddenEntry(dirent.name)) continue;

      const childRel = relPath
        ? `${relPath.replace(/\/+$/, "")}/${dirent.name}`
        : dirent.name;

      // safeResolve canonicalizes and re-checks containment, so a symlink that
      // escapes the workspace (or points at a hidden entry) is skipped, not
      // listed. Also drops anything that won't stat (broken link, or the agent
      // deleting a file mid-listing).
      let size = 0;
      let mtime = 0;
      let entryIsDir = dirent.isDirectory();
      try {
        const realChild = await safeResolve(workspaceDir, childRel);
        const entryStat = await fs.stat(realChild);
        size = entryStat.size;
        mtime = entryStat.mtimeMs;
        entryIsDir = entryStat.isDirectory();
      } catch {
        continue;
      }

      entries.push({
        name: dirent.name,
        path: childRel,
        type: entryIsDir ? "dir" : "file",
        size,
        mtime,
        ext: entryIsDir ? "" : extname(dirent.name).slice(1).toLowerCase(),
      });
    }

    // Directories first, then files, each alphabetical (case-insensitive).
    entries.sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });

    const parent =
      relPath && relPath !== "."
        ? relPath.replace(/\/+$/, "").split("/").slice(0, -1).join("/")
        : null;

    return NextResponse.json({
      path: relPath,
      parent,
      entries,
      dir: workspaceDir,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to list workspace.",
      },
      { status: 400 }
    );
  }
}
