// Server-only helpers for talking to the active JW deployment's
// on-disk workspace. Shared by the workspace upload/list/read API routes.
//
// The "workspace" is the working directory of the currently running langgraph
// dev (where the agent reads/writes real files via its file tools). It is NOT
// the agent's in-memory `files` state — that lives in thread state and is
// managed through the SDK.

import { promises as fs } from "fs";
import { homedir } from "os";
import { basename, dirname, join, relative, resolve, sep } from "path";
import { createHash, randomUUID } from "crypto";
import type { NextRequest } from "next/server";

export const WORKSPACE_SIDECAR = join(
  homedir(),
  ".config",
  "jw",
  "langgraph_dev.workspace.json"
);

interface WorkspaceSidecar {
  workspace?: unknown;
  pid?: unknown;
}

export interface WorkspaceBinding {
  schema_version: number;
  thread_id: string;
  project_id: string;
  run_id: string;
  base_workspace: string;
  project_root: string;
  project_shared: string;
  workspace: string;
  legacy: boolean;
  created_at: string;
}

export const WORKSPACE_BINDINGS_DIR =
  process.env.agent_WORKSPACE_BINDINGS_DIR ||
  join(
    process.env.XDG_CONFIG_HOME || join(homedir(), ".config"),
    "jw",
    "workspace_bindings"
  );

function bindingPath(threadId: string, baseWorkspace: string): string {
  const digest = createHash("sha256")
    .update(`${baseWorkspace}\0${threadId}`, "utf8")
    .digest("hex");
  return join(WORKSPACE_BINDINGS_DIR, `${digest}.json`);
}

function taskRunId(threadId: string): string {
  const prefix =
    threadId
      .trim()
      .replace(/[^A-Za-z0-9._-]+/g, "-")
      .replace(/^[-._]+|[-._]+$/g, "")
      .slice(0, 18) || "thread";
  const digest = createHash("sha256")
    .update(threadId, "utf8")
    .digest("hex")
    .slice(0, 8);
  return `run_${prefix}_${digest}`;
}

function projectSlug(projectId: string): string {
  return (
    projectId
      .trim()
      .replace(/[^A-Za-z0-9._-]+/g, "-")
      .replace(/^[-._]+|[-._]+$/g, "")
      .slice(0, 48) || "default"
  );
}

async function writeJsonIfMissing(path: string, value: unknown): Promise<void> {
  try {
    await fs.writeFile(path, JSON.stringify(value, null, 2) + "\n", {
      encoding: "utf-8",
      flag: "wx",
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
}

/** True if the name contains any C0/C1 control char, DEL, or a line/paragraph
 *  separator. A newline in a filename would otherwise be spliced into the
 *  prompt sent to the agent (instruction injection); control chars have no
 *  place in a filename. */
export function hasControlChar(name: string): boolean {
  for (let i = 0; i < name.length; i += 1) {
    const code = name.charCodeAt(i);
    if (
      code < 0x20 || // C0 controls (incl. NUL, tab, newline)
      code === 0x7f || // DEL
      (code >= 0x80 && code <= 0x9f) || // C1 controls
      code === 0x2028 || // line separator
      code === 0x2029 // paragraph separator
    ) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// What the workspace browser hides
//
// Single source of truth for "noise" the file tree, direct file access, and the
// download-all zip all agree on. Edit these lists to change what's shown.
// Matched case-sensitively against each path segment.
// ---------------------------------------------------------------------------

/** Exact entry names treated as internal/noise — never research artifacts. */
export const IGNORED_NAMES = new Set([
  "large_tool_results", // JW: large tool outputs spilled to disk, keyed by call id
  "conversation_history", // JW: internal conversation transcripts
  "__pycache__", // Python bytecode cache
  "node_modules", // JS deps
  "__MACOSX", // macOS archive cruft
]);

/** Filename suffixes hidden the same way (build artifacts). */
export const IGNORED_SUFFIXES = [".pyc", ".pyo"];

/**
 * True if a single entry name should be hidden from the workspace browser:
 * dotfiles (incl. `.langgraph_api`, `.DS_Store`), known-internal directories,
 * and build artifacts. Used for both listings and direct-access blocking.
 */
export function isHiddenEntry(name: string): boolean {
  if (name.startsWith(".")) return true;
  if (IGNORED_NAMES.has(name)) return true;
  return IGNORED_SUFFIXES.some((suffix) => name.endsWith(suffix));
}

/** `zip -x` exclude args derived from the same ignore lists, so the download
 *  archive contains exactly what the tree shows. */
export function zipExcludeArgs(): string[] {
  const patterns = [".*", "*/.*"]; // dotfiles at root and nested
  for (const name of IGNORED_NAMES) {
    patterns.push(`${name}/*`, `*/${name}/*`, name, `*/${name}`);
  }
  for (const suffix of IGNORED_SUFFIXES) {
    patterns.push(`*${suffix}`);
  }
  return patterns.flatMap((pattern) => ["-x", pattern]);
}

/** True if a process with `pid` is currently running (signal 0 = existence probe). */
export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM means the process exists but we may not signal it — still alive.
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

/**
 * Resolve the workspace of the *currently running* JW deployment.
 *
 * The sidecar records `{ workspace, pid }` of the langgraph dev that owns this
 * workspace. We only trust it when that pid is still alive — a stale sidecar
 * from a crashed/previous session must not silently redirect file access to a
 * directory the live deployment no longer uses. Falls back to the launcher env.
 */
export async function getBaseWorkspaceDir(): Promise<string> {
  let workspace: string | undefined;
  try {
    const sidecar = JSON.parse(
      await fs.readFile(WORKSPACE_SIDECAR, "utf-8")
    ) as WorkspaceSidecar;
    const ws = sidecar.workspace;
    const pid = sidecar.pid;
    if (typeof ws === "string" && ws.trim()) {
      const hasPid = typeof pid === "number" && pid > 0;
      // With a recorded backend pid, only trust the sidecar while that process
      // is alive; older sidecars without one fall back to trusting it as before.
      if (!hasPid || isProcessAlive(pid as number)) workspace = ws;
    }
  } catch {
    // Older/manual setups may not have a sidecar. Fall back to the launcher env.
  }

  workspace ||= process.env.agent_WORKSPACE_DIR;
  if (!workspace) {
    throw new Error(
      "No active 金乌 workspace found. Start the backend with `jw deploy` first."
    );
  }

  const resolved = resolve(workspace);
  const stat = await fs.stat(resolved);
  if (!stat.isDirectory()) {
    throw new Error("The active 金乌 workspace is not a directory.");
  }
  // Canonicalize so every containment check compares against the *real* root —
  // the workspace (or a parent) may itself live under a symlink (e.g. macOS
  // /tmp -> /private/tmp), which would otherwise break startsWith() checks.
  return fs.realpath(resolved);
}

/** Read and validate the persistent thread → task-workspace binding. */
export async function getWorkspaceBinding(
  threadId: string,
  baseWorkspace: string
): Promise<WorkspaceBinding | null> {
  if (!threadId.trim() || hasControlChar(threadId)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(
      await fs.readFile(bindingPath(threadId, baseWorkspace), "utf-8")
    );
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Partial<WorkspaceBinding>;
  if (
    row.thread_id !== threadId ||
    row.base_workspace !== baseWorkspace ||
    typeof row.workspace !== "string" ||
    typeof row.base_workspace !== "string" ||
    typeof row.project_root !== "string" ||
    typeof row.project_shared !== "string" ||
    typeof row.project_id !== "string" ||
    typeof row.run_id !== "string" ||
    typeof row.created_at !== "string"
  ) {
    return null;
  }
  return {
    schema_version:
      typeof row.schema_version === "number" ? row.schema_version : 2,
    thread_id: row.thread_id,
    project_id: row.project_id,
    run_id: row.run_id,
    base_workspace: row.base_workspace,
    project_root: row.project_root,
    project_shared: row.project_shared,
    workspace: row.workspace,
    legacy: row.legacy === true,
    created_at: row.created_at,
  };
}

/**
 * Create a clean task binding before first model submission (used by uploads).
 * Python uses the same deterministic layout and treats this binding as
 * authoritative when the run starts.
 */
export async function ensureThreadWorkspace(
  threadId: string,
  projectId = "default"
): Promise<WorkspaceBinding> {
  if (!threadId.trim() || hasControlChar(threadId)) {
    throw new Error("A valid task thread is required.");
  }
  const base = await getBaseWorkspaceDir();
  const existing = await getWorkspaceBinding(threadId, base);
  if (existing) return existing;
  const project = projectSlug(projectId);
  const projectRoot = join(base, "projects", project);
  const shared = join(projectRoot, "shared");
  const runId = taskRunId(threadId);
  const workspace = join(projectRoot, "runs", runId);
  const createdAt = new Date().toISOString();
  await Promise.all(
    [
      join(shared, "assets"),
      join(shared, "knowledge"),
      join(shared, "decisions"),
      join(workspace, "inputs"),
      join(workspace, "work"),
      join(workspace, "outputs"),
      join(workspace, "receipts"),
      WORKSPACE_BINDINGS_DIR,
    ].map((directory) => fs.mkdir(directory, { recursive: true }))
  );
  const binding: WorkspaceBinding = {
    schema_version: 2,
    thread_id: threadId,
    project_id: project,
    run_id: runId,
    base_workspace: base,
    project_root: projectRoot,
    project_shared: shared,
    workspace,
    legacy: false,
    created_at: createdAt,
  };
  await Promise.all([
    writeJsonIfMissing(join(projectRoot, "project.json"), {
      schema_version: 2,
      project_id: project,
      created_at: createdAt,
      continuity_policy: {
        shared_root: "shared",
        task_runs_are_isolated: true,
        prior_runs_are_not_implicitly_loaded: true,
      },
    }),
    writeJsonIfMissing(join(workspace, "task.json"), {
      schema_version: 2,
      thread_id: threadId,
      project_id: project,
      run_id: runId,
      research_question: "",
      status: "active",
      created_at: createdAt,
    }),
    writeJsonIfMissing(join(workspace, "input_manifest.json"), {
      schema_version: 2,
      thread_id: threadId,
      inputs: [],
    }),
    writeJsonIfMissing(join(workspace, "context_snapshot.json"), {
      schema_version: 2,
      thread_id: threadId,
      project_id: project,
      project_shared_virtual_path: "/project/",
      imported_context: [],
      prior_runs_implicitly_loaded: false,
    }),
  ]);

  // The filename is content-addressed by thread id. A concurrent Python writer
  // can only choose the same deterministic paths; atomic rename avoids partial
  // JSON while keeping either complete equivalent binding valid.
  const target = bindingPath(threadId, base);
  const temp = `${target}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temp, JSON.stringify(binding, null, 2) + "\n", "utf-8");
  await fs.rename(temp, target);
  return binding;
}

/** Resolve the workspace for one task thread; global fallback is forbidden. */
export async function getWorkspaceDir(
  threadId?: string | null
): Promise<string> {
  if (!threadId?.trim()) {
    throw new Error("No task selected. Start or open a research task first.");
  }
  const activeBase = await getBaseWorkspaceDir();
  const binding = await getWorkspaceBinding(threadId, activeBase);
  if (!binding) {
    throw new Error(
      "This task workspace is not bound yet. Send the first message or upload an input first."
    );
  }
  const boundBase = await fs.realpath(resolve(binding.base_workspace));
  if (boundBase !== activeBase) {
    throw new Error(
      "This task belongs to a different active project workspace."
    );
  }
  const workspace = await fs.realpath(resolve(binding.workspace));
  if (workspace !== activeBase && !workspace.startsWith(activeBase + sep)) {
    throw new Error(
      "The task workspace binding is outside the active project."
    );
  }
  const stat = await fs.stat(workspace);
  if (!stat.isDirectory()) {
    throw new Error("The task workspace is not a directory.");
  }
  return workspace;
}

/**
 * Remove every generated artifact from one task workspace before regenerating
 * a response. Uploaded inputs and the binding metadata are retained so the
 * replacement run can use the same source material; everything else is
 * deleted and the standard output directories are recreated empty.
 */
export async function resetGeneratedWorkspace(
  threadId: string
): Promise<void> {
  const workspace = await getWorkspaceDir(threadId);
  const protectedEntries = new Set([
    "inputs",
    "task.json",
    "input_manifest.json",
    "context_snapshot.json",
  ]);
  const entries = await fs.readdir(workspace, { withFileTypes: true });

  for (const entry of entries) {
    if (protectedEntries.has(entry.name)) continue;
    const target = resolve(workspace, entry.name);
    if (target === workspace || !target.startsWith(workspace + sep)) {
      throw new Error("Refusing to reset a path outside the task workspace.");
    }
    // rm removes a symlink itself rather than following it. The workspace path
    // above is already canonical and containment-checked by getWorkspaceDir.
    await fs.rm(target, { recursive: true, force: true });
  }

  await Promise.all(
    ["work", "outputs", "receipts"].map((name) =>
      fs.mkdir(join(workspace, name), { recursive: true })
    )
  );
}

/**
 * Resolve a caller-supplied relative path against `workspaceDir` and guarantee
 * the result stays inside it (no `..` escape, no absolute-path override, no
 * control chars). Returns the absolute, normalized path.
 *
 * `relPath` is treated as relative even if it starts with `/` — a leading slash
 * is stripped so an absolute path can never replace the workspace root.
 */
export function resolveInside(workspaceDir: string, relPath: string): string {
  if (hasControlChar(relPath)) {
    throw new Error("Invalid path.");
  }
  // Normalize separators and strip any leading slashes so the path is always
  // interpreted relative to the workspace root.
  const cleaned = relPath.replaceAll("\\", "/").replace(/^\/+/, "");
  // Hidden entries (dotfiles like `.langgraph_api`, internal dirs like
  // `large_tool_results`, build noise) are blocked from direct access too — not
  // just unlisted — so a crafted `?path=.langgraph_api` or `?path=large_tool_results`
  // can't read them. `..` is also caught here (and by the boundary check below).
  if (cleaned.split("/").some((seg) => seg !== "" && isHiddenEntry(seg))) {
    throw new Error("Path is not accessible.");
  }
  const target = resolve(workspaceDir, cleaned);
  // Must be the workspace dir itself or strictly within it.
  if (target !== workspaceDir && !target.startsWith(workspaceDir + sep)) {
    throw new Error("Path is outside the workspace.");
  }
  return target;
}

/**
 * Like `resolveInside`, but ALSO defeats symlink escapes: it canonicalizes the
 * target with `fs.realpath` and re-checks containment + the hidden-entry policy
 * against the real path. A symlink such as `out -> /etc` (so `out/passwd` is
 * lexically "inside") or `link -> .langgraph_api` is rejected here, even though
 * the lexical check in `resolveInside` would pass.
 *
 * `workspaceDir` MUST already be canonical (it is — `getWorkspaceDir` realpaths
 * it). Throws if the target doesn't exist or escapes. Use this for any access
 * that will then follow the path on disk (stat/read/list).
 */
export async function safeResolve(
  workspaceDir: string,
  relPath: string
): Promise<string> {
  const target = resolveInside(workspaceDir, relPath);
  let realTarget: string;
  try {
    realTarget = await fs.realpath(target);
  } catch {
    // Missing file or a broken/looping symlink — treat as inaccessible.
    throw new Error("Path is not accessible.");
  }
  if (
    realTarget !== workspaceDir &&
    !realTarget.startsWith(workspaceDir + sep)
  ) {
    throw new Error("Path is not accessible.");
  }
  // A symlink could point at a hidden/internal entry that lives inside the
  // workspace (e.g. `foo -> .langgraph_api`); re-check the canonical segments.
  const realRel = relative(workspaceDir, realTarget);
  if (
    realRel &&
    realRel.split(sep).some((seg) => seg !== "" && isHiddenEntry(seg))
  ) {
    throw new Error("Path is not accessible.");
  }
  return realTarget;
}

/**
 * Reject cross-site requests to the workspace APIs. Browsers omit `Origin` on
 * same-origin GETs and on direct navigations (open-in-tab / downloads), so we
 * lean on `Sec-Fetch-Site` when present and fall back to an Origin check.
 */
export function isCrossOrigin(request: NextRequest): boolean {
  const site = request.headers.get("sec-fetch-site");
  if (
    site &&
    site !== "same-origin" &&
    site !== "same-site" &&
    site !== "none"
  ) {
    return true; // explicit cross-site request
  }
  const origin = request.headers.get("origin");
  return !!origin && origin !== request.nextUrl.origin;
}

// ---------------------------------------------------------------------------
// In-place editing (save / delete) of workspace text files
//
// Mirrors the write/delete guards in lib/server/memory.ts. Editing is
// overwrite-only on text/code files: safeResolve requires the target to exist,
// so this path never creates new files (uploads handle creation) and never
// touches a binary. Same-origin enforcement lives in the route.
// ---------------------------------------------------------------------------

/** Text/code extensions editable in place. Superset of the previewable text
 *  types so anything the viewer renders as text can also be saved. Binary
 *  (images/pdf/etc.) is intentionally excluded. */
const EDITABLE_EXTS = new Set([
  "txt",
  "text",
  "md",
  "markdown",
  "log",
  "csv",
  "tsv",
  "json",
  "jsonl",
  "yaml",
  "yml",
  "toml",
  "xml",
  "ini",
  "cfg",
  "conf",
  "env",
  "py",
  "js",
  "jsx",
  "ts",
  "tsx",
  "sh",
  "bash",
  "zsh",
  "r",
  "jl",
  "cpp",
  "cc",
  "c",
  "h",
  "hpp",
  "java",
  "go",
  "rs",
  "rb",
  "php",
  "swift",
  "kt",
  "cs",
  "sql",
  "tex",
  "bib",
  "rst",
  "css",
  "scss",
  "html",
]);

export const MAX_WORKSPACE_WRITE_BYTES = 5 * 1024 * 1024;

/**
 * Re-verify, immediately before a mutating fs op, that the target's parent
 * directory still canonically resolves inside the workspace. `safeResolve`
 * canonicalizes once up front, but a parent component could be swapped for a
 * symlink in the window before the write/rename/rm (TOCTOU). This narrows that
 * window; it cannot fully close it without openat-style fds (not in Node's fs),
 * so a determined local race remains theoretically possible — acceptable for a
 * single-user local dev tool.
 */
async function assertParentInside(
  workspaceDir: string,
  target: string
): Promise<void> {
  let realParent: string;
  try {
    realParent = await fs.realpath(dirname(target));
  } catch {
    throw new Error("Path is not accessible.");
  }
  if (
    realParent !== workspaceDir &&
    !realParent.startsWith(workspaceDir + sep)
  ) {
    throw new Error("Path is not accessible.");
  }
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

/** True if a file may be edited (and thus saved) as text in the workspace. */
export function isEditableTextFile(name: string): boolean {
  return EDITABLE_EXTS.has(extOf(name));
}

/**
 * Overwrite an existing workspace text file (atomic temp + rename). Preserves
 * the original file's permission bits so the agent's files keep their mode.
 * Rejects binaries, oversized content, and anything that isn't already a file.
 */
export async function writeWorkspaceFile(
  workspaceDir: string,
  relPath: string,
  content: string
): Promise<{ path: string; size: number; mtime: number }> {
  if (typeof content !== "string") throw new Error("Content must be a string.");
  if (Buffer.byteLength(content, "utf-8") > MAX_WORKSPACE_WRITE_BYTES) {
    throw new Error("This file is too large to save.");
  }
  const name = relPath.replaceAll("\\", "/").split("/").pop() || relPath;
  if (!isEditableTextFile(name)) {
    throw new Error("Only text/code files can be edited.");
  }
  // Never edit *through* a final-component symlink: a planted `note.py ->
  // secret.bin` would otherwise let an editable-looking name overwrite a
  // non-editable/binary target inside the workspace. lstat the lexical path so
  // we inspect the link itself, not what it points at.
  const lexical = resolveInside(workspaceDir, relPath);
  let linkStat;
  try {
    linkStat = await fs.lstat(lexical);
  } catch {
    throw new Error("Only files can be edited.");
  }
  if (linkStat.isSymbolicLink()) {
    throw new Error("Editing symlinks is not allowed.");
  }
  // safeResolve canonicalizes + requires existence, so editing is overwrite-only
  // and a symlink can't redirect the write outside the workspace.
  const target = await safeResolve(workspaceDir, relPath);
  // Validate the RESOLVED target's extension too (defends against a parent
  // symlink redirecting the basename), not just the requested name.
  if (!isEditableTextFile(basename(target))) {
    throw new Error("Only text/code files can be edited.");
  }
  const st = await fs.stat(target);
  if (!st.isFile()) throw new Error("Only files can be edited.");

  const tmp = `${target}.${randomUUID()}.tmp`;
  try {
    await assertParentInside(workspaceDir, target);
    await fs.writeFile(tmp, content, {
      encoding: "utf-8",
      mode: st.mode & 0o777, // keep the original file's permissions
    });
    await assertParentInside(workspaceDir, target);
    await fs.rename(tmp, target);
  } catch (e) {
    await fs.rm(tmp, { force: true }).catch(() => {});
    throw e;
  }

  const after = await fs.stat(target);
  return {
    path: relPath.replaceAll("\\", "/").replace(/^\/+/, ""),
    size: after.size,
    mtime: after.mtimeMs,
  };
}

/** Permanently delete a workspace file (never a directory). */
export async function deleteWorkspaceFile(
  workspaceDir: string,
  relPath: string
): Promise<void> {
  // safeResolve enforces containment (and rejects a final symlink whose target
  // escapes the workspace). We then act on the LEXICAL path so deleting a
  // symlink unlinks the link itself, not its target — lstat/rm don't follow the
  // final-component symlink.
  await safeResolve(workspaceDir, relPath);
  const lexical = resolveInside(workspaceDir, relPath);
  const st = await fs.lstat(lexical);
  if (st.isDirectory()) throw new Error("Only files can be deleted.");
  // Re-verify the parent is still inside the workspace just before unlinking, to
  // narrow the TOCTOU window after safeResolve (see assertParentInside).
  await assertParentInside(workspaceDir, lexical);
  await fs.rm(lexical, { force: true });
}
