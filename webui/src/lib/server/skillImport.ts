// Server-only: install a skill the user brings themselves.
//
// Three input shapes, one pipeline:
//   - a folder picked in the browser  -> importSkillFromUploads(files)
//   - a .zip archive                  -> importSkillFromZip(bytes)
//   - a directory on the server's disk -> importSkillFromDirectory(dir)
//
// Every input is normalized to a list of relative file paths + bytes, then
// validated (layout / name / size / path safety) and written atomically into
// SKILLS_INSTALL_DIR (<DATA_DIR>/skills/<name>) — the same global tier JW reads
// and the official installer writes to. The DIRECTORY NAME is the skill's
// identity (what /api/skills, uninstall and dedup key on); the frontmatter name
// inside SKILL.md is display-only.
//
// Zero npm dependencies: the ZIP reader below is a minimal central-directory
// parser (stored + deflate only, via node:zlib), which is all a skill archive
// realistically needs.

import { randomUUID } from "crypto";
import { promises as fs } from "fs";
import { homedir } from "os";
import { basename, dirname, join, resolve, sep } from "path";
import { inflateRawSync } from "zlib";
import {
  isValidSkillName,
  MAX_SKILL_BYTES,
  MAX_SKILL_FILES,
  SKILLS_INSTALL_DIR,
} from "./skills";

/** The marker file that makes a directory a skill. */
const SKILL_MARKER = "SKILL.md";

/** One entry of a skill tree: POSIX-style path relative to the skill root. */
interface SkillFile {
  rel: string;
  data: Uint8Array;
}

export interface ImportedSkill {
  name: string;
  files: number;
  bytes: number;
  overwritten: boolean;
}

export type SkillImportErrorCode =
  | "invalid"
  | "exists"
  | "too-large"
  | "unsafe";

/** Typed failure so the route can map it to a status code. */
export class SkillImportError extends Error {
  readonly code: SkillImportErrorCode;

  constructor(code: SkillImportErrorCode, message: string) {
    super(message);
    this.name = "SkillImportError";
    this.code = code;
  }
}

export interface SkillImportOptions {
  /** Install identity (directory name). Falls back to the folder name. */
  name?: string;
  /** Replace an existing install of the same name. */
  overwrite?: boolean;
}

// ---------------------------------------------------------------------------
// Path safety
// ---------------------------------------------------------------------------

/** `~/...` is the one bit of shell convenience we accept on a server path. */
function expandHome(value: string): string {
  return value.startsWith("~")
    ? join(homedir(), value.slice(1))
    : resolve(value);
}

/** macOS archivers add these; they're never part of a skill. */
const JUNK_SEGMENTS = new Set(["__MACOSX"]);
const JUNK_BASENAMES = new Set([".DS_Store"]);

function isJunkPath(rel: string): boolean {
  const segments = rel.split("/");
  if (segments.some((segment) => JUNK_SEGMENTS.has(segment))) return true;
  return JUNK_BASENAMES.has(segments[segments.length - 1]);
}

/**
 * Normalize an archive/upload entry path into a relative POSIX path.
 *
 * Rejects absolute paths, drive letters, backslashes (ambiguous separators —
 * a ZIP entry must use "/") and any `..` segment, so a hostile archive can
 * never write outside the skill directory.
 */
function normalizeEntryPath(raw: string): string {
  const value = raw.trim();
  if (!value) {
    throw new SkillImportError("invalid", "技能中包含空文件路径。");
  }
  if (value.includes("\0")) {
    throw new SkillImportError("unsafe", "技能中包含非法文件路径。");
  }
  if (value.includes("\\")) {
    throw new SkillImportError("unsafe", "技能中的文件路径不允许包含反斜杠。");
  }
  if (value.startsWith("/") || /^[A-Za-z]:/.test(value)) {
    throw new SkillImportError("unsafe", "技能中不允许包含绝对路径的文件。");
  }
  const segments = value.split("/").filter((s) => s !== "" && s !== ".");
  if (segments.some((segment) => segment === "..")) {
    throw new SkillImportError(
      "unsafe",
      "技能中不允许包含向上越级的文件路径。"
    );
  }
  if (segments.length === 0) {
    throw new SkillImportError("invalid", "技能中包含空文件路径。");
  }
  return segments.join("/");
}

// ---------------------------------------------------------------------------
// Layout: where is the skill root inside the provided tree?
// ---------------------------------------------------------------------------

interface Layout {
  /** Prefix to strip from every path ("" for a root-level SKILL.md). */
  prefix: string;
  /** Directory name of that prefix, usable as the install name. */
  hint?: string;
}

/**
 * Pick the skill root: the root itself when it holds SKILL.md, otherwise a
 * single wrapping directory (`my-skill/SKILL.md` — what zipping a folder gives
 * you). Anything else (no marker, several markers, deeper nesting) is rejected
 * because there's no unambiguous skill here.
 */
function resolveLayout(files: SkillFile[], sourceLabel: string): Layout {
  const markers = files
    .map((file) => file.rel)
    .filter((rel) => rel === SKILL_MARKER || rel.endsWith(`/${SKILL_MARKER}`));
  if (markers.length === 0) {
    throw new SkillImportError(
      "invalid",
      `${sourceLabel} 中未找到 ${SKILL_MARKER}。`
    );
  }
  if (markers.length > 1) {
    throw new SkillImportError(
      "invalid",
      `${sourceLabel} 中找到了多个 ${SKILL_MARKER}，无法确定技能根目录。`
    );
  }
  const marker = markers[0];
  if (marker === SKILL_MARKER) return { prefix: "" };

  const segments = marker.split("/");
  if (segments.length !== 2) {
    throw new SkillImportError(
      "invalid",
      `${SKILL_MARKER} 必须位于技能根目录或唯一的一层子目录中。`
    );
  }
  const hint = segments[0];
  return {
    prefix: `${hint}/`,
    hint: isValidSkillName(hint) ? hint : undefined,
  };
}

function stripPrefix(files: SkillFile[], prefix: string): SkillFile[] {
  if (!prefix) return files;
  return files
    .filter((file) => file.rel.startsWith(prefix))
    .map((file) => ({ rel: file.rel.slice(prefix.length), data: file.data }));
}

function resolveSkillName(opts: SkillImportOptions, hint?: string): string {
  const explicit = opts.name?.trim();
  if (explicit) {
    if (!isValidSkillName(explicit)) {
      throw new SkillImportError(
        "invalid",
        "技能名称不合法：只能包含字母、数字、点、下划线和短横线，且必须以字母或数字开头。"
      );
    }
    return explicit;
  }
  if (hint) return hint;
  throw new SkillImportError(
    "invalid",
    `无法确定技能名称：请把 ${SKILL_MARKER} 放进技能目录，或显式指定 name。`
  );
}

/** Size/count guards — both are enforced on the tree we're about to write. */
function enforceLimits(files: SkillFile[]): number {
  if (files.length > MAX_SKILL_FILES) {
    throw new SkillImportError(
      "too-large",
      `技能包含的文件过多（最多 ${MAX_SKILL_FILES} 个）。`
    );
  }
  let bytes = 0;
  for (const file of files) bytes += file.data.byteLength;
  if (bytes > MAX_SKILL_BYTES) {
    throw new SkillImportError(
      "too-large",
      `技能体积过大（最多 ${Math.round(MAX_SKILL_BYTES / 1024 / 1024)} MB）。`
    );
  }
  return bytes;
}

// ---------------------------------------------------------------------------
// Atomic write into <DATA_DIR>/skills/<name>
// ---------------------------------------------------------------------------

async function pathExists(target: string): Promise<boolean> {
  try {
    await fs.lstat(target);
    return true;
  } catch {
    return false;
  }
}

/**
 * Write the tree next to its destination, then swap it in. A failure at any
 * point leaves no `.importing-*` temp dir behind and (when overwriting) the
 * previous install intact.
 */
async function writeSkillTree(
  name: string,
  files: SkillFile[],
  overwrite: boolean
): Promise<boolean> {
  const installRoot = resolve(SKILLS_INSTALL_DIR);
  const destRoot = join(installRoot, name);
  await fs.mkdir(installRoot, { recursive: true });

  const existed = await pathExists(destRoot);
  if (existed && !overwrite) {
    throw new SkillImportError(
      "exists",
      `技能 ${name} 已存在，如需覆盖请确认覆盖。`
    );
  }

  // A dotfile dir, so a half-written import never shows up as a skill.
  const tmpRoot = join(installRoot, `.importing-${name}-${randomUUID()}`);
  let backup: string | null = null;
  try {
    await fs.mkdir(tmpRoot);
    for (const file of files) {
      const target = resolve(tmpRoot, file.rel);
      if (target !== tmpRoot && !target.startsWith(tmpRoot + sep)) {
        throw new SkillImportError("unsafe", "技能中包含越级文件路径。");
      }
      await fs.mkdir(dirname(target), { recursive: true });
      await fs.writeFile(target, file.data);
    }

    if (existed) {
      backup = join(installRoot, `.replaced-${name}-${randomUUID()}`);
      await fs.rename(destRoot, backup);
    }
    try {
      await fs.rename(tmpRoot, destRoot);
    } catch (error) {
      if (backup) await fs.rename(backup, destRoot).catch(() => {});
      throw error;
    }
    if (backup) {
      await fs.rm(backup, { recursive: true, force: true }).catch(() => {});
      backup = null;
    }
  } catch (error) {
    await fs.rm(tmpRoot, { recursive: true, force: true }).catch(() => {});
    if (error instanceof SkillImportError) throw error;
    const code = (error as NodeJS.ErrnoException).code;
    throw new SkillImportError(
      "invalid",
      code ? `写入技能文件失败（${code}）。` : "写入技能文件失败。"
    );
  } finally {
    if (backup) {
      await fs.rm(backup, { recursive: true, force: true }).catch(() => {});
    }
  }
  return existed;
}

/** Shared tail for every input shape: validate the tree, then install it. */
async function installFiles(
  files: SkillFile[],
  opts: SkillImportOptions,
  sourceLabel: string,
  nameHint?: string
): Promise<ImportedSkill> {
  const layout = resolveLayout(files, sourceLabel);
  const stripped = stripPrefix(files, layout.prefix);
  if (stripped.length === 0 || !stripped.some((f) => f.rel === SKILL_MARKER)) {
    throw new SkillImportError(
      "invalid",
      `${sourceLabel} 中未找到 ${SKILL_MARKER}。`
    );
  }
  const name = resolveSkillName(opts, layout.hint ?? nameHint);
  const bytes = enforceLimits(stripped);
  const overwritten = await writeSkillTree(
    name,
    stripped,
    opts.overwrite === true
  );
  return { name, files: stripped.length, bytes, overwritten };
}

// ---------------------------------------------------------------------------
// Input 1: files uploaded from the browser (folder picker)
// ---------------------------------------------------------------------------

/**
 * Install from browser-supplied files. `path` is the file's relative path —
 * the frontend should send `webkitRelativePath` (via
 * `formData.append("files", file, file.webkitRelativePath)`, since multipart
 * itself only carries the basename).
 */
export async function importSkillFromUploads(
  files: Array<{ path: string; data: Uint8Array }>,
  opts: SkillImportOptions = {}
): Promise<ImportedSkill> {
  if (!Array.isArray(files) || files.length === 0) {
    throw new SkillImportError("invalid", "请至少选择一个要导入的文件。");
  }
  const seen = new Set<string>();
  const normalized: SkillFile[] = [];
  for (const file of files) {
    if (!file || typeof file.path !== "string") {
      throw new SkillImportError("invalid", "上传的文件缺少相对路径。");
    }
    if (!(file.data instanceof Uint8Array)) {
      throw new SkillImportError("invalid", "上传的文件内容无效。");
    }
    const rel = normalizeEntryPath(file.path);
    if (isJunkPath(rel)) continue;
    if (seen.has(rel)) {
      throw new SkillImportError(
        "invalid",
        `技能中存在重复的文件路径（${rel}）。`
      );
    }
    seen.add(rel);
    normalized.push({ rel, data: file.data });
  }
  return installFiles(normalized, opts, "上传的文件");
}

// ---------------------------------------------------------------------------
// Input 2: a directory that already exists on the server
// ---------------------------------------------------------------------------

async function walkSourceDir(
  absDir: string,
  relDir: string,
  out: SkillFile[]
): Promise<void> {
  let entries;
  try {
    entries = await fs.readdir(absDir, { withFileTypes: true });
  } catch {
    throw new SkillImportError("invalid", "技能目录读取失败。");
  }
  for (const entry of entries) {
    const rel = relDir ? `${relDir}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) {
      throw new SkillImportError("unsafe", "技能目录中不允许包含符号链接。");
    }
    if (entry.isDirectory()) {
      if (JUNK_SEGMENTS.has(entry.name)) continue;
      await walkSourceDir(join(absDir, entry.name), rel, out);
      continue;
    }
    if (!entry.isFile() || isJunkPath(rel)) continue;
    // Stop as soon as the count guard is blown — no point reading more bytes.
    if (out.length >= MAX_SKILL_FILES) {
      throw new SkillImportError(
        "too-large",
        `技能包含的文件过多（最多 ${MAX_SKILL_FILES} 个）。`
      );
    }
    let data: Buffer;
    try {
      data = await fs.readFile(join(absDir, entry.name));
    } catch {
      throw new SkillImportError("invalid", "技能中的文件读取失败。");
    }
    out.push({ rel, data: new Uint8Array(data) });
  }
}

/** Install from a directory on the server's filesystem (absolute or `~/...`). */
export async function importSkillFromDirectory(
  sourceDir: string,
  opts: SkillImportOptions = {}
): Promise<ImportedSkill> {
  const value = typeof sourceDir === "string" ? sourceDir.trim() : "";
  if (!value) {
    throw new SkillImportError("invalid", "缺少技能目录路径。");
  }
  const root = expandHome(value);
  let stat;
  try {
    stat = await fs.stat(root);
  } catch {
    throw new SkillImportError("invalid", "该路径不存在。");
  }
  if (!stat.isDirectory()) {
    throw new SkillImportError("invalid", "该路径不是一个目录。");
  }

  const files: SkillFile[] = [];
  await walkSourceDir(root, "", files);
  if (files.length === 0) {
    throw new SkillImportError("invalid", "该目录是空的。");
  }
  const base = basename(root);
  return installFiles(
    files,
    opts,
    "该目录",
    isValidSkillName(base) ? base : undefined
  );
}

// ---------------------------------------------------------------------------
// Input 3: a .zip archive (minimal reader — stored + deflate, no zip64)
// ---------------------------------------------------------------------------

const SIG_EOCD = 0x06054b50;
const SIG_CENTRAL = 0x02014b50;
const SIG_LOCAL = 0x04034b50;
const EOCD_MIN_BYTES = 22;
/** EOCD is at most 22 bytes + a 64 KB comment. */
const EOCD_MAX_SEARCH = EOCD_MIN_BYTES + 0xffff;
const ZIP64_MARKER = 0xffffffff;
const ZIP64_ENTRY = 0x0001;
/** Unix file-type mask + symlink type, from the high 16 bits of external attrs. */
const UNIX_TYPE_MASK = 0o170000;
const UNIX_SYMLINK = 0o120000;

function requireRange(bytes: Uint8Array, offset: number, length: number): void {
  if (offset < 0 || length < 0 || offset + length > bytes.length) {
    throw new SkillImportError("invalid", "压缩包数据不完整或已损坏。");
  }
}

function u16(bytes: Uint8Array, offset: number): number {
  requireRange(bytes, offset, 2);
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function u32(bytes: Uint8Array, offset: number): number {
  requireRange(bytes, offset, 4);
  return (
    (bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24)) >>>
    0
  );
}

let crcTable: Uint32Array | null = null;

function crc32(data: Uint8Array): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let c = i;
      for (let k = 0; k < 8; k += 1) {
        c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      }
      crcTable[i] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i += 1) {
    crc = crcTable[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** True when the central-directory extra block asks for zip64 sizes/offsets. */
function hasZip64Extra(extra: Uint8Array): boolean {
  let offset = 0;
  while (offset + 4 <= extra.length) {
    const id = u16(extra, offset);
    const size = u16(extra, offset + 2);
    if (id === ZIP64_ENTRY) return true;
    offset += 4 + size;
  }
  return false;
}

/**
 * Minimal ZIP reader: returns every regular file (directories and macOS junk are
 * dropped) with its raw entry path. Only method 0 (stored) and method 8
 * (deflate) are supported; encrypted and zip64 archives are rejected.
 *
 * Pure and synchronous, so it's easy to test without touching the disk.
 */
export function readZipEntries(
  bytes: Uint8Array
): Array<{ path: string; data: Uint8Array }> {
  if (!(bytes instanceof Uint8Array) || bytes.length === 0) {
    throw new SkillImportError("invalid", "压缩包为空。");
  }

  // Locate the end-of-central-directory record (it's the last thing in the file).
  let eocd = -1;
  const searchStart = Math.max(0, bytes.length - EOCD_MAX_SEARCH);
  for (let i = bytes.length - EOCD_MIN_BYTES; i >= searchStart; i -= 1) {
    if (u32(bytes, i) === SIG_EOCD) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) {
    throw new SkillImportError(
      "invalid",
      "无法解析该压缩包（未找到 ZIP 目录）。"
    );
  }

  const diskNumber = u16(bytes, eocd + 4);
  const centralDisk = u16(bytes, eocd + 6);
  const entriesOnDisk = u16(bytes, eocd + 8);
  const totalEntries = u16(bytes, eocd + 10);
  const centralSize = u32(bytes, eocd + 12);
  const centralOffset = u32(bytes, eocd + 16);
  if (diskNumber !== 0 || centralDisk !== 0 || entriesOnDisk !== totalEntries) {
    throw new SkillImportError("invalid", "不支持分卷压缩包。");
  }
  if (
    totalEntries === 0xffff ||
    centralSize === ZIP64_MARKER ||
    centralOffset === ZIP64_MARKER
  ) {
    throw new SkillImportError("invalid", "不支持 zip64 格式的压缩包。");
  }
  if (totalEntries === 0) return [];

  // Normally the offsets are absolute; tolerate data prepended to the archive
  // (self-extracting stubs, some streaming writers) by falling back to a
  // relative position measured from the EOCD.
  let cursor = centralOffset;
  if (u32(bytes, cursor) !== SIG_CENTRAL) {
    cursor = eocd - centralSize;
    if (cursor < 0) {
      throw new SkillImportError("invalid", "压缩包目录已损坏。");
    }
  }

  const entries: Array<{ path: string; data: Uint8Array }> = [];
  const decoder = new TextDecoder("utf-8");
  for (let index = 0; index < totalEntries; index += 1) {
    if (u32(bytes, cursor) !== SIG_CENTRAL) {
      throw new SkillImportError("invalid", "压缩包目录已损坏。");
    }
    const flags = u16(bytes, cursor + 8);
    const method = u16(bytes, cursor + 10);
    const crc = u32(bytes, cursor + 16);
    const compressedSize = u32(bytes, cursor + 20);
    const uncompressedSize = u32(bytes, cursor + 24);
    const nameLength = u16(bytes, cursor + 28);
    const extraLength = u16(bytes, cursor + 30);
    const commentLength = u16(bytes, cursor + 32);
    const externalAttrs = u32(bytes, cursor + 38);
    const localOffset = u32(bytes, cursor + 42);
    requireRange(bytes, cursor + 46, nameLength + extraLength + commentLength);
    const name = decoder.decode(
      bytes.subarray(cursor + 46, cursor + 46 + nameLength)
    );
    const extra = bytes.subarray(
      cursor + 46 + nameLength,
      cursor + 46 + nameLength + extraLength
    );
    cursor += 46 + nameLength + extraLength + commentLength;

    if ((flags & 0x01) !== 0) {
      throw new SkillImportError("invalid", "不支持加密的压缩包。");
    }
    if (
      compressedSize === ZIP64_MARKER ||
      uncompressedSize === ZIP64_MARKER ||
      hasZip64Extra(extra)
    ) {
      throw new SkillImportError("invalid", "不支持 zip64 格式的压缩包。");
    }
    if (method !== 0 && method !== 8) {
      throw new SkillImportError(
        "invalid",
        `不支持该压缩方式（method ${method}）。`
      );
    }
    // A symlink entry would make the written tree point at arbitrary paths.
    if (((externalAttrs >>> 16) & UNIX_TYPE_MASK) === UNIX_SYMLINK) {
      throw new SkillImportError("unsafe", "压缩包中不允许包含符号链接。");
    }
    if (name.endsWith("/")) continue; // directory entry

    // The local header repeats the name/extra lengths; data follows both.
    requireRange(bytes, localOffset, 30);
    if (u32(bytes, localOffset) !== SIG_LOCAL) {
      throw new SkillImportError("invalid", "压缩包中的文件头已损坏。");
    }
    const localNameLength = u16(bytes, localOffset + 26);
    const localExtraLength = u16(bytes, localOffset + 28);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    requireRange(bytes, dataStart, compressedSize);
    const raw = bytes.subarray(dataStart, dataStart + compressedSize);

    let data: Uint8Array;
    if (method === 0) {
      data = raw.slice();
    } else {
      try {
        data = new Uint8Array(inflateRawSync(raw));
      } catch {
        throw new SkillImportError("invalid", "压缩包中的数据已损坏。");
      }
    }
    if (data.length !== uncompressedSize) {
      throw new SkillImportError("invalid", "压缩包中的数据长度不符。");
    }
    if (data.length > 0 && crc32(data) !== crc) {
      throw new SkillImportError("invalid", "压缩包中的数据校验失败。");
    }
    entries.push({ path: name, data });
  }
  return entries;
}

/** Install from a .zip archive (a skill folder, zipped). */
export async function importSkillFromZip(
  bytes: Uint8Array,
  opts: SkillImportOptions = {}
): Promise<ImportedSkill> {
  if (!(bytes instanceof Uint8Array) || bytes.length === 0) {
    throw new SkillImportError("invalid", "压缩包为空。");
  }
  const seen = new Set<string>();
  const files: SkillFile[] = [];
  for (const entry of readZipEntries(bytes)) {
    const rel = normalizeEntryPath(entry.path);
    if (isJunkPath(rel)) continue;
    if (seen.has(rel)) {
      throw new SkillImportError(
        "invalid",
        `压缩包中存在重复的文件路径（${rel}）。`
      );
    }
    seen.add(rel);
    files.push({ rel, data: entry.data });
  }
  if (files.length === 0) {
    throw new SkillImportError("invalid", "压缩包中没有文件。");
  }
  return installFiles(files, opts, "压缩包");
}
