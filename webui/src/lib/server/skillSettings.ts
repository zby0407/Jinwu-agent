// Skill 开关（启用/停用）的持久化状态。
//
// 这份状态与 Python 侧的 `jw/skill_settings.py` 共享：文件固定位于 JW
// DATA_DIR（`jwDataDir()`，默认 `~/.jw`）下的 `skill_settings.json`，
// 桌面端 WebUI 与 Python runtime 必须读到同一份文件——WebUI 里关掉的
// skill，agent 运行时也必须跳过；反之 Python 侧写入后 WebUI 要立即反映。
//
// 约定格式：`{"version": 1, "disabled": ["<skill-dir-name>", ...]}`。
// 文件不存在 = 全部启用。写入只改 `disabled`，其它未知字段原样保留。

import { mkdir, readFile, rename, unlink, writeFile } from "fs/promises";
import { randomUUID } from "crypto";
import { dirname, basename, join } from "path";
import { jwDataDir } from "./jwPaths";

/** Shared state file name — mirrors Python's `SKILL_SETTINGS_FILENAME`. */
export const SKILL_SETTINGS_FILENAME = "skill_settings.json";

/**
 * Absolute path of the shared state file. Resolved on every call (never cached
 * at import time) so `JW_DATA_DIR` overrides — tests, the desktop supervisor —
 * take effect.
 */
export function skillSettingsPath(): string {
  return join(jwDataDir(), SKILL_SETTINGS_FILENAME);
}

/** Loose view of the file: known keys are validated, unknown ones preserved. */
type SkillSettingsFile = Record<string, unknown>;

/**
 * Read + parse the state file. Never throws: a missing file, an unreadable
 * file, broken JSON, or a non-object root all mean "no settings yet".
 */
async function readSettingsFile(path: string): Promise<SkillSettingsFile> {
  try {
    const parsed: unknown = JSON.parse(await readFile(path, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return parsed as SkillSettingsFile;
  } catch {
    return {};
  }
}

/**
 * Normalize a `disabled` value to sorted, de-duplicated skill names.
 *
 * Mirrors Python's `_disabled_from_payload`: a non-list is treated as absent and
 * individual non-string entries are dropped (keeping the valid names) rather
 * than discarding the whole array — otherwise the UI would show a skill as
 * enabled while the runtime skips it.
 */
function normalizeDisabled(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const names = new Set<string>();
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const name = entry.trim();
    if (name) names.add(name);
  }
  return [...names].sort();
}

/**
 * Serialize exactly like Python's writer
 * (`json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"`),
 * so either side produces the same bytes for the same content.
 */
function serializeSettings(settings: SkillSettingsFile): string {
  const ordered: SkillSettingsFile = {};
  for (const key of Object.keys(settings).sort()) ordered[key] = settings[key];
  return `${JSON.stringify(ordered, null, 2)}\n`;
}

/**
 * Names of the disabled skills. Returns an empty set when the file is missing,
 * corrupt, or has a malformed `disabled` field — never throws, so the API can
 * always fall back to "everything enabled".
 */
export async function readDisabledSkills(): Promise<Set<string>> {
  const settings = await readSettingsFile(skillSettingsPath());
  return new Set(normalizeDisabled(settings.disabled));
}

/**
 * Enable or disable one skill and persist the shared state file.
 *
 * Writes atomically (same-directory temp file + `rename`) so the Python
 * runtime never observes a half-written JSON document, creates DATA_DIR when
 * missing, keeps unknown fields, pins `version: 1`, and stores `disabled`
 * sorted/de-duplicated (matching Python's writer byte for byte).
 */
const writeQueues = new Map<string, Promise<void>>();

export async function setSkillEnabled(name: string, enabled: boolean): Promise<void> {
  const path = skillSettingsPath();
  const previous = writeQueues.get(path) ?? Promise.resolve();
  const pending = previous.catch(() => {}).then(() => writeSkillEnabled(path, name, enabled));
  writeQueues.set(path, pending);
  try {
    await pending;
  } finally {
    if (writeQueues.get(path) === pending) writeQueues.delete(path);
  }
}

async function writeSkillEnabled(
  path: string,
  name: string,
  enabled: boolean
): Promise<void> {
  const settings = await readSettingsFile(path);
  const disabled = new Set(normalizeDisabled(settings.disabled));
  if (enabled) disabled.delete(name);
  else disabled.add(name);

  // Only `disabled` (and the pinned version) is owned here; every other key is
  // carried over untouched.
  const next: SkillSettingsFile = { ...settings, version: 1 };
  next.disabled = [...disabled].sort();

  await mkdir(dirname(path), { recursive: true });
  const tmp = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`);
  try {
    await writeFile(tmp, serializeSettings(next), "utf8");
    for (let attempt = 0; ; attempt++) {
      try {
        await rename(tmp, path);
        break;
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (process.platform !== "win32" || !["EPERM", "EACCES", "EBUSY"].includes(code ?? "") || attempt >= 4) throw error;
        await new Promise((resolve) => setTimeout(resolve, 10 * 2 ** attempt));
      }
    }
  } catch (error) {
    await unlink(tmp).catch(() => {});
    throw error;
  }
}
