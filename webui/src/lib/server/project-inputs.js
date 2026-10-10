import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import { basename, join, resolve, sep } from "node:path";

const MAX_INPUT_BYTES = 64 * 1024 * 1024;
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
// Both sources are emitted by jw.workspaces._project_input_records and carry
// the same task-local path/size/digest contract.
const PROJECT_INPUT_SOURCES = new Set([
  "registered_project_data",
  "project_shared_data",
]);

function inside(root, candidate) {
  return candidate.startsWith(root + sep);
}

function projectRelativePath(path) {
  if (typeof path !== "string" || !path.startsWith("/project/")) {
    throw new Error("请使用完整的 /project/ 输入路径。");
  }
  const relative = path.slice("/project/".length);
  const parts = relative.split("/");
  if (
    /[\\\x00-\x1f\x7f-\x9f\u2028\u2029:]/.test(relative) ||
    parts.some((part) => !part || part.startsWith("."))
  ) {
    throw new Error("项目输入路径无效。");
  }
  return parts;
}

/** Read only the current thread's explicitly registered project input.
 * Serve the exact bytes whose digest was checked, rather than reopening the
 * path as an unverified stream after validation. Never searches by basename. */
export async function readRegisteredProjectInput({
  threadId,
  baseWorkspace,
  binding,
  path,
}) {
  const parts = projectRelativePath(path);
  if (!threadId || binding.thread_id !== threadId || binding.legacy) {
    throw new Error("项目输入需要当前任务的独立工作区绑定。");
  }
  const base = await fs.realpath(baseWorkspace);
  const boundBase = await fs.realpath(binding.base_workspace);
  const projectRoot = await fs.realpath(binding.project_root);
  const workspace = await fs.realpath(binding.workspace);
  const shared = await fs.realpath(binding.project_shared);
  if (
    boundBase !== base ||
    !inside(base, projectRoot) ||
    !inside(join(projectRoot, "runs"), workspace) ||
    shared !== join(projectRoot, "shared") ||
    (await fs.lstat(binding.project_shared)).isSymbolicLink()
  ) {
    throw new Error("项目输入绑定不属于当前任务工作区。");
  }

  const manifestPath = join(workspace, "input_manifest.json");
  const manifestStat = await fs.lstat(manifestPath);
  if (!manifestStat.isFile() || manifestStat.size > MAX_MANIFEST_BYTES) {
    throw new Error("任务输入清单无效。");
  }
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  if (
    manifest.thread_id !== threadId ||
    !Array.isArray(manifest.project_inputs)
  ) {
    throw new Error("任务输入清单与当前会话不匹配。");
  }
  const matches = manifest.project_inputs.filter(
    (row) => row?.path === path && PROJECT_INPUT_SOURCES.has(row.source)
  );
  const entry = matches.length === 1 ? matches[0] : null;
  if (
    !entry ||
    typeof entry.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/i.test(entry.sha256) ||
    !Number.isSafeInteger(entry.bytes) ||
    entry.bytes < 0
  ) {
    throw new Error("该项目文件未登记为当前任务的输入。");
  }
  if (entry.bytes > MAX_INPUT_BYTES) {
    throw new Error("项目输入超过 64 MiB 的预览与下载上限。");
  }
  let target = shared;
  for (const part of parts) {
    target = resolve(target, part);
    if (!inside(shared, target) || (await fs.lstat(target)).isSymbolicLink()) {
      throw new Error("项目输入不能经过符号链接或位于共享目录之外。");
    }
  }
  if (!inside(shared, await fs.realpath(target))) {
    throw new Error("项目输入位于共享目录之外。");
  }
  const handle = await fs.open(target, "r");
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size !== entry.bytes) {
      throw new Error("项目输入大小与登记记录不一致。");
    }
    // One extra byte detects growth while keeping allocation bounded.
    const buffer = Buffer.alloc(entry.bytes + 1);
    let length = 0;
    while (length < buffer.length) {
      const { bytesRead } = await handle.read(
        buffer,
        length,
        buffer.length - length,
        null
      );
      if (!bytesRead) break;
      length += bytesRead;
    }
    const content = buffer.subarray(0, length);
    if (
      length !== entry.bytes ||
      createHash("sha256").update(content).digest("hex") !==
        entry.sha256.toLowerCase()
    ) {
      throw new Error("项目输入 SHA256 与登记记录不一致。");
    }
    return { content, fileName: basename(target), bytes: length };
  } finally {
    await handle.close();
  }
}
