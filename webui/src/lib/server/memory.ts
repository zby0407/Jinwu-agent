// Server-only helpers for EvoScientist's on-disk *memory* — the agent's
// long-lived, cross-session knowledge. Unlike the workspace (per-deployment,
// resolved from a live sidecar) memory is GLOBAL: it lives under the data dir
// EvoScientist's paths.py resolves to, by default `~/.evoscientist/memories`.
//
// Layout written by EvoScientist (all plain markdown):
//   profile/SOUL.md, profile/USER_PROFILE.md, profile/RESEARCH_TASTE.md
//   profile/projects/<project-id>/PROJECT_PROFILE.md
//   ideation-memory.md, experiment-memory.md            (evo-memory skill)
//   evolution-reports/cycle_N_*.md
//
// These are user-facing, editable knowledge files, so this module exposes
// read + write (not just read like the workspace browser). Every path is
// guarded against traversal and symlink escape, mirroring workspace.ts.

import { promises as fs } from "fs";
import { homedir } from "os";
import { join, dirname, relative, resolve, sep } from "path";
import { randomUUID } from "crypto";
import { hasControlChar } from "@/lib/server/workspace";
import type { ObsGraphData, ObsNode, ObsEdge } from "@/lib/observationGraph";

// Re-export so memory API routes can share the workspace cross-origin guard.
export { isCrossOrigin } from "@/lib/server/workspace";

/** EvoScientist's global data dir — `~/.evoscientist` by default (paths.py
 *  DATA_DIR), relocatable via EVOSCIENTIST_DATA_DIR. Mirrors skills.ts so both
 *  resolve the backend's data root the same way. */
function expandHome(p: string): string {
  return p.startsWith("~") ? join(homedir(), p.slice(1)) : resolve(p);
}

function globalDataDir(): string {
  const env = process.env.EVOSCIENTIST_DATA_DIR;
  if (env && env.trim()) return expandHome(env);
  return join(homedir(), ".evoscientist");
}

/**
 * Lexical path of the memory directory, matching EvoScientist's paths.py:
 *   EVOSCIENTIST_MEMORIES_DIR > EVOSCIENTIST_MEMORY_DIR (legacy) > DATA_DIR/memories.
 * Does not touch the filesystem (the dir may not exist yet).
 */
export function memoryDirPath(): string {
  const env =
    process.env.EVOSCIENTIST_MEMORIES_DIR ||
    process.env.EVOSCIENTIST_MEMORY_DIR;
  if (env && env.trim()) return expandHome(env);
  return join(globalDataDir(), "memories");
}

/** Canonical memory dir if it exists on disk, else null (nothing written yet). */
async function canonicalDirIfExists(): Promise<string | null> {
  try {
    const real = await fs.realpath(memoryDirPath());
    const stat = await fs.stat(real);
    return stat.isDirectory() ? real : null;
  } catch {
    return null;
  }
}

/** Canonical memory dir, creating it first. Used by writes. */
async function ensureCanonicalDir(): Promise<string> {
  const lexical = memoryDirPath();
  await fs.mkdir(lexical, { recursive: true });
  return fs.realpath(lexical);
}

// ---------------------------------------------------------------------------
// What's hidden / what's editable
// ---------------------------------------------------------------------------

const IGNORED_NAMES = new Set(["__pycache__", "node_modules", "__MACOSX"]);
const IGNORED_SUFFIXES = [".pyc", ".pyo"];

/** Hide dotfiles and build noise; everything else is a real memory file. */
function isHiddenEntry(name: string): boolean {
  if (name.startsWith(".")) return true;
  if (IGNORED_NAMES.has(name)) return true;
  return IGNORED_SUFFIXES.some((s) => name.endsWith(s));
}

/** Text extensions we render and allow editing. Memory is markdown in practice;
 *  the rest are here so an occasional note/data file is still viewable. */
const TEXT_EXTS = new Set([
  "md",
  "markdown",
  "txt",
  "text",
  "json",
  "yaml",
  "yml",
  "csv",
  "tsv",
  "log",
  "tex",
  "bib",
  "rst",
]);

export function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function isTextFile(name: string): boolean {
  return TEXT_EXTS.has(extOf(name));
}

// Read/write size caps — memory files are small; refuse pathological sizes.
const MAX_READ_BYTES = 2 * 1024 * 1024;
export const MAX_WRITE_BYTES = 1 * 1024 * 1024;
const MAX_DEPTH = 8;
const MAX_ENTRIES = 2000;

// ---------------------------------------------------------------------------
// Path resolution (traversal + symlink-escape guards, cf. workspace.ts)
// ---------------------------------------------------------------------------

/** Lexical resolve of a caller path inside `root`: no control chars, no leading
 *  slash override, no hidden segments, no `..` escape. Returns absolute path. */
function resolveInside(root: string, relPath: string): string {
  if (hasControlChar(relPath)) throw new Error("Invalid path.");
  const cleaned = relPath.replaceAll("\\", "/").replace(/^\/+/, "");
  if (!cleaned) throw new Error("A file path is required.");
  if (cleaned.split("/").some((seg) => seg !== "" && isHiddenEntry(seg))) {
    throw new Error("Path is not accessible.");
  }
  const target = resolve(root, cleaned);
  if (target !== root && !target.startsWith(root + sep)) {
    throw new Error("Path is outside the memory directory.");
  }
  return target;
}

/** Resolve an EXISTING memory file, defeating symlink escapes (for reads). */
async function safeResolveExisting(
  root: string,
  relPath: string
): Promise<string> {
  const target = resolveInside(root, relPath);
  let real: string;
  try {
    real = await fs.realpath(target);
  } catch {
    throw new Error("Path is not accessible.");
  }
  if (real !== root && !real.startsWith(root + sep)) {
    throw new Error("Path is not accessible.");
  }
  const rel = relative(root, real);
  if (rel && rel.split(sep).some((s) => s !== "" && isHiddenEntry(s))) {
    throw new Error("Path is not accessible.");
  }
  return real;
}

/** Resolve a target for WRITING (the file may not exist yet): the lexical path
 *  must be inside root, and its nearest existing ancestor must canonicalize to
 *  inside root too (so a symlinked parent can't redirect the write out). */
async function safeResolveForWrite(
  root: string,
  relPath: string
): Promise<string> {
  const target = resolveInside(root, relPath);
  // Walk up to the first existing ancestor and verify it stays inside root.
  let probe = dirname(target);
  for (;;) {
    try {
      const realProbe = await fs.realpath(probe);
      if (realProbe !== root && !realProbe.startsWith(root + sep)) {
        throw new Error("Path is outside the memory directory.");
      }
      break;
    } catch (e) {
      if (e instanceof Error && e.message.includes("outside")) throw e;
      const parent = dirname(probe);
      if (parent === probe) break; // reached fs root
      probe = parent;
    }
  }
  return target;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface MemoryEntry {
  /** Path relative to the memory root, POSIX-separated. */
  path: string;
  size: number;
  /** Last-modified epoch ms. */
  mtime: number;
  /** True if we can render/edit it as text. */
  editable: boolean;
}

export interface MemoryListing {
  /** Absolute memory dir (for display); always set even if it doesn't exist. */
  dir: string;
  exists: boolean;
  entries: MemoryEntry[];
  truncated: boolean;
}

/** Recursively list every memory file (depth/count bounded). */
export async function listMemory(): Promise<MemoryListing> {
  const dir = memoryDirPath();
  const maybeRoot = await canonicalDirIfExists();
  if (!maybeRoot) return { dir, exists: false, entries: [], truncated: false };
  // Bind to a non-null local so the nested walk() closure keeps the narrowing.
  const root: string = maybeRoot;

  const out: MemoryEntry[] = [];
  let truncated = false;

  async function walk(absDir: string, depth: number): Promise<void> {
    if (depth > MAX_DEPTH || out.length >= MAX_ENTRIES) {
      if (out.length >= MAX_ENTRIES) truncated = true;
      return;
    }
    let dirents;
    try {
      dirents = await fs.readdir(absDir, { withFileTypes: true });
    } catch {
      return;
    }
    // Stable, predictable order: dirs and files alphabetically.
    dirents.sort((a, b) => a.name.localeCompare(b.name));
    for (const ent of dirents) {
      if (isHiddenEntry(ent.name)) continue;
      if (out.length >= MAX_ENTRIES) {
        truncated = true;
        return;
      }
      const abs = join(absDir, ent.name);
      if (ent.isDirectory()) {
        await walk(abs, depth + 1);
      } else if (ent.isFile()) {
        let size = 0;
        let mtime = 0;
        try {
          const st = await fs.stat(abs);
          size = st.size;
          mtime = st.mtimeMs;
        } catch {
          continue;
        }
        out.push({
          path: relative(root, abs).split(sep).join("/"),
          size,
          mtime,
          editable: isTextFile(ent.name) && size <= MAX_READ_BYTES,
        });
      }
    }
  }

  await walk(root, 0);
  return { dir, exists: true, entries: out, truncated };
}

export interface MemoryFile {
  path: string;
  content: string;
  size: number;
  mtime: number;
}

export interface ExecEntry {
  id: string;
  created_at: string;
  agent: string;
  session_id: string;
  project_id: string;
  summary: string;
  obs_ids: string[];
  path: string;
}

export interface ExecListData {
  entries: ExecEntry[];
  truncated: boolean;
}

/** Read one memory text file. */
export async function readMemory(relPath: string): Promise<MemoryFile> {
  const root = await canonicalDirIfExists();
  if (!root) throw new Error("No memory directory found.");
  if (!isTextFile(relPath)) throw new Error("This file type can't be edited.");
  const abs = await safeResolveExisting(root, relPath);
  const st = await fs.stat(abs);
  if (!st.isFile()) throw new Error("Not a file.");
  if (st.size > MAX_READ_BYTES)
    throw new Error("This file is too large to open.");
  const content = await fs.readFile(abs, "utf-8");
  return {
    path: relPath.replaceAll("\\", "/").replace(/^\/+/, ""),
    content,
    size: st.size,
    mtime: st.mtimeMs,
  };
}

/** Create or overwrite a memory text file (atomic temp + rename). */
export async function writeMemory(
  relPath: string,
  content: string
): Promise<MemoryFile> {
  if (typeof content !== "string") throw new Error("Content must be a string.");
  if (Buffer.byteLength(content, "utf-8") > MAX_WRITE_BYTES) {
    throw new Error("This file is too large to save.");
  }
  if (!isTextFile(relPath)) {
    throw new Error("Only text/markdown memory files can be saved.");
  }
  const root = await ensureCanonicalDir();
  const target = await safeResolveForWrite(root, relPath);
  await fs.mkdir(dirname(target), { recursive: true });
  const tmp = `${target}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(tmp, content, { encoding: "utf-8", mode: 0o600 });
    await fs.rename(tmp, target);
  } catch (e) {
    await fs.rm(tmp, { force: true }).catch(() => {});
    throw e;
  }
  const st = await fs.stat(target);
  return {
    path: relPath.replaceAll("\\", "/").replace(/^\/+/, ""),
    content,
    size: st.size,
    mtime: st.mtimeMs,
  };
}

function extractFm(content: string): string {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  return match ? match[1] : "";
}

function fmScalar(yaml: string, key: string): string {
  const re = new RegExp(`^${key}:\\s*(.+)`, "m");
  const m = yaml.match(re);
  if (!m) return "";
  return m[1]
    .trim()
    .replace(/^'(.*)'$/, "$1")
    .replace(/^"(.*)"$/, "$1");
}

function fmRelated(yaml: string): Array<{ id: string; relation: string }> {
  const start = yaml.indexOf("related_observations:");
  if (start === -1) return [];
  const chunk = yaml.slice(start + "related_observations:".length);
  const results: Array<{ id: string; relation: string }> = [];
  const itemRe = /^- id:\s*(.+)$/gm;
  let m: RegExpExecArray | null;
  while ((m = itemRe.exec(chunk)) !== null) {
    const itemStart = m.index;
    const rest = chunk.slice(itemStart + 1);
    const nextBoundary = rest.match(/^(?:- |[a-z_])/m);
    const itemText = chunk.slice(
      itemStart,
      itemStart + 1 + (nextBoundary?.index ?? rest.length)
    );
    const id = m[1].trim().replace(/^['"]|['"]$/g, "");
    const relMatch = itemText.match(/^\s+relation:\s*(.+)$/m);
    const relation = relMatch?.[1]?.trim() ?? "";
    if (id && relation) results.push({ id, relation });
  }
  return results;
}

function fmNestedScalar(yaml: string, key: string): string {
  const re = new RegExp(`\\b${key}:\\s*(.+)`, "m");
  const m = yaml.match(re);
  if (!m) return "";
  return m[1]
    .trim()
    .replace(/^'(.*)'$/, "$1")
    .replace(/^"(.*)"$/, "$1");
}

export async function listObservations(): Promise<ObsGraphData> {
  const maybeRoot = await canonicalDirIfExists();
  if (!maybeRoot) return { nodes: [], edges: [] };
  const root = maybeRoot;
  const obsDir = join(root, "observations");
  try {
    await fs.access(obsDir);
  } catch {
    return { nodes: [], edges: [] };
  }

  const nodeMap = new Map<string, ObsNode>();
  const rawEdges: ObsEdge[] = [];

  async function walk(dir: string): Promise<void> {
    let dirents: import("fs").Dirent[];
    try {
      dirents = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    dirents.sort((a, b) => a.name.localeCompare(b.name));
    for (const ent of dirents) {
      if (isHiddenEntry(ent.name)) continue;
      const abs = join(dir, ent.name);
      if (ent.isDirectory()) {
        await walk(abs);
      } else if (ent.isFile() && ent.name.endsWith(".md")) {
        const content = await fs.readFile(abs, "utf-8").catch(() => null);
        if (!content) continue;
        const yaml = extractFm(content);
        const id = fmScalar(yaml, "id") || ent.name.replace(/\.md$/, "");
        const summary = fmScalar(yaml, "summary") || id;
        const memory_type = fmScalar(yaml, "memory_type") || "semantic";
        const scope = fmScalar(yaml, "scope") || "global";
        const created_at = fmScalar(yaml, "created_at") || "";
        const related = fmRelated(yaml);
        const path = relative(root, abs).split(sep).join("/");
        nodeMap.set(id, {
          id,
          path,
          summary,
          memory_type,
          scope,
          created_at,
          degree: 0,
        });
        for (const rel of related) {
          rawEdges.push({ source: id, target: rel.id, relation: rel.relation });
        }
      }
    }
  }

  await walk(obsDir);

  const edges = rawEdges.filter(
    (e) => nodeMap.has(e.source) && nodeMap.has(e.target)
  );
  for (const e of edges) {
    nodeMap.get(e.source)!.degree++;
    nodeMap.get(e.target)!.degree++;
  }

  return { nodes: Array.from(nodeMap.values()), edges };
}

export async function listExecutions(): Promise<ExecListData> {
  const maybeRoot = await canonicalDirIfExists();
  if (!maybeRoot) return { entries: [], truncated: false };
  const root = maybeRoot;
  const execDir = join(root, "executions");
  try {
    await fs.access(execDir);
  } catch {
    return { entries: [], truncated: false };
  }

  const entries: ExecEntry[] = [];
  let truncated = false;

  async function walk(dir: string): Promise<void> {
    if (entries.length >= MAX_ENTRIES) {
      truncated = true;
      return;
    }
    let dirents: import("fs").Dirent[];
    try {
      dirents = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    dirents.sort((a, b) => a.name.localeCompare(b.name));
    for (const ent of dirents) {
      if (isHiddenEntry(ent.name)) continue;
      const abs = join(dir, ent.name);
      if (ent.isDirectory()) {
        await walk(abs);
      } else if (ent.isFile() && ent.name.endsWith(".md")) {
        const content = await fs.readFile(abs, "utf-8").catch(() => null);
        if (!content) continue;
        const yaml = extractFm(content);
        const id = fmScalar(yaml, "id") || ent.name.replace(/\.md$/, "");
        const created_at = fmScalar(yaml, "created_at") || "";
        const project_id = fmScalar(yaml, "project_id") || "";
        const agent = fmNestedScalar(yaml, "agent");
        const session_id = fmNestedScalar(yaml, "session_id");
        const body = content
          .replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "")
          .trim();
        const summaryMatch = body.match(
          /^##\s+Summary\s*\r?\n([\s\S]*?)(?=\n##\s|\s*$)/m
        );
        const summary = summaryMatch
          ? summaryMatch[1].trim()
          : body.slice(0, 500);
        const obs_ids = [...new Set(body.match(/\bO-[0-9a-f]{16}\b/g) ?? [])];
        entries.push({
          id,
          created_at,
          agent,
          session_id,
          project_id,
          summary,
          obs_ids,
          path: relative(root, abs).split(sep).join("/"),
        });
      }
    }
  }

  await walk(execDir);
  entries.sort((a, b) => b.created_at.localeCompare(a.created_at));
  return { entries, truncated };
}

/** Permanently delete a memory file. */
export async function deleteMemory(relPath: string): Promise<void> {
  const root = await canonicalDirIfExists();
  if (!root) throw new Error("No memory directory found.");
  const abs = await safeResolveExisting(root, relPath);
  const st = await fs.stat(abs);
  if (!st.isFile()) throw new Error("Only files can be deleted.");
  await fs.rm(abs, { force: true });
}
