/**
 * 旧版数据目录（`~/.jw`）到桌面版数据目录（`<userData>/data`）的一次性迁移。
 *
 * 背景：命令行版 JW 把应用状态放在 `~/.jw`（`jw/paths.py` 的 `DATA_DIR`：
 * `JW_DATA_DIR` 未设置时的默认值），而桌面外壳启动子进程时显式把
 * `JW_DATA_DIR` 指向 `<userData>/data`（见 `environment.mjs`）。两者互不相通，
 * 因此从命令行版升级到桌面版的用户，其记忆（`memories/`）、知识库
 * （`knowledge.db`）和会话（`sessions.db`）在桌面版里全部显示为空。
 *
 * 这里做的是**只增不改**的一次性拷贝：
 * - 目标文件已存在时一律跳过，绝不用旧数据覆盖桌面版里更新过的内容；
 * - `memories/` 递归合并，只补齐缺失的文件；
 * - 成功（且没有失败项）后写 `.legacy-imported` 标记，避免重复扫描；
 * - 用户显式设置了 `JW_DATA_DIR` 时完全跳过——此时数据目录由用户自己管理。
 */

import path from 'node:path';
import {
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  writeFileSync,
} from 'node:fs';

export const LEGACY_DATA_MARKER = '.legacy-imported';

/** 旧版数据目录：默认 `~/.jw`，可用 `JW_LEGACY_DATA_DIR` 覆盖（测试/自定义安装）。 */
export function resolveLegacyDataDirectory({ homedir, env = process.env } = {}) {
  const override = typeof env?.JW_LEGACY_DATA_DIR === 'string'
    ? env.JW_LEGACY_DATA_DIR.trim()
    : '';
  if (override) {
    return path.resolve(override);
  }
  if (typeof homedir !== 'string' || !homedir.trim()) {
    throw new TypeError('resolveLegacyDataDirectory 需要显式的 homedir');
  }
  return path.join(homedir, '.jw');
}

/** `<userData>/data` —— 与 `paths.mjs` 的 `resolveDataDirectory` 保持一致。 */
function defaultDataDirectory(userDataDir) {
  return path.join(userDataDir, 'data');
}

/** SQLite 文件连同 WAL/SHM 兄弟文件：三件套一起拷，避免半截事务。 */
const SQLITE_GROUPS = [
  ['knowledge.db', 'knowledge.db-wal', 'knowledge.db-shm'],
  ['sessions.db', 'sessions.db-wal', 'sessions.db-shm'],
];

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

/** 递归合并目录：只补缺失文件，保留目标端已有内容。返回拷贝的文件相对路径。 */
function mergeDirectory(sourceDir, targetDir, relativePrefix = '') {
  const copied = [];
  const failed = [];
  let entries;
  try {
    entries = readdirSync(sourceDir, { withFileTypes: true });
  } catch (error) {
    failed.push(`${relativePrefix || '.'}: ${error?.message ?? error}`);
    return { copied, failed };
  }
  for (const entry of entries) {
    const relative = relativePrefix ? path.join(relativePrefix, entry.name) : entry.name;
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    let stats;
    try {
      stats = lstatSync(sourcePath);
    } catch (error) {
      failed.push(`${relative}: ${error?.message ?? error}`);
      continue;
    }
    // 符号链接/特殊文件不迁移：保留目标端的可预测性。
    if (stats.isSymbolicLink()) {
      continue;
    }
    if (stats.isDirectory()) {
      try {
        mkdirSync(targetPath, { recursive: true });
      } catch (error) {
        failed.push(`${relative}: ${error?.message ?? error}`);
        continue;
      }
      const nested = mergeDirectory(sourcePath, targetPath, relative);
      copied.push(...nested.copied);
      failed.push(...nested.failed);
      continue;
    }
    if (!stats.isFile()) {
      continue;
    }
    if (existsSync(targetPath)) {
      continue; // 桌面版里的同名文件更新，保留它。
    }
    try {
      copyFileSync(sourcePath, targetPath);
      copied.push(relative);
    } catch (error) {
      failed.push(`${relative}: ${error?.message ?? error}`);
    }
  }
  return { copied, failed };
}

/**
 * 把 `~/.jw` 里用户可见的数据迁移到桌面版数据目录。
 *
 * 返回值：
 * - `status: 'skipped' | 'imported' | 'failed'`
 * - `reason`（skipped 时）：`data-dir-overridden` / `no-legacy-directory` /
 *   `already-imported` / `no-legacy-content`
 * - `imported`：实际拷贝的相对路径列表
 * - `failed`：失败的相对路径与原因（非空时不写标记，下次启动重试）
 */
export function importLegacyData({
  userDataDir,
  homedir,
  env = process.env,
  dataDirectory,
} = {}) {
  if (typeof env?.JW_DATA_DIR === 'string' && env.JW_DATA_DIR.trim()) {
    return { status: 'skipped', reason: 'data-dir-overridden', imported: [], failed: [] };
  }
  if (!isNonEmptyString(userDataDir)) {
    throw new TypeError('importLegacyData 需要显式的 userDataDir');
  }
  const legacyDataDir = resolveLegacyDataDirectory({ homedir, env });
  const targetDataDir = dataDirectory ?? defaultDataDirectory(userDataDir);
  const base = { legacyDataDir, dataDirectory: targetDataDir, imported: [], failed: [] };

  if (!existsSync(legacyDataDir)) {
    return { ...base, status: 'skipped', reason: 'no-legacy-directory' };
  }
  if (existsSync(path.join(targetDataDir, LEGACY_DATA_MARKER))) {
    return { ...base, status: 'skipped', reason: 'already-imported' };
  }

  const hasMemories = existsSync(path.join(legacyDataDir, 'memories'));
  const dbGroups = SQLITE_GROUPS.filter((group) =>
    group.some((name) => existsSync(path.join(legacyDataDir, name))),
  );
  if (!hasMemories && dbGroups.length === 0) {
    return { ...base, status: 'skipped', reason: 'no-legacy-content' };
  }

  try {
    mkdirSync(targetDataDir, { recursive: true });
  } catch (error) {
    return {
      ...base,
      status: 'failed',
      failed: [`${targetDataDir}: ${error?.message ?? error}`],
    };
  }

  const imported = [];
  const failed = [];

  for (const group of dbGroups) {
    const primary = group[0];
    const targetPrimary = path.join(targetDataDir, primary);
    if (existsSync(targetPrimary)) {
      continue; // 桌面版已经写过自己的库，绝不覆盖。
    }
    for (const name of group) {
      const sourcePath = path.join(legacyDataDir, name);
      if (!existsSync(sourcePath)) {
        continue;
      }
      try {
        copyFileSync(sourcePath, path.join(targetDataDir, name));
        imported.push(name);
      } catch (error) {
        failed.push(`${name}: ${error?.message ?? error}`);
      }
    }
  }

  if (hasMemories) {
    const nested = mergeDirectory(
      path.join(legacyDataDir, 'memories'),
      path.join(targetDataDir, 'memories'),
      'memories',
    );
    imported.push(...nested.copied);
    failed.push(...nested.failed);
  }

  if (failed.length === 0) {
    try {
      writeFileSync(
        path.join(targetDataDir, LEGACY_DATA_MARKER),
        `${new Date().toISOString()}\n`,
        'utf8',
      );
    } catch (error) {
      failed.push(`${LEGACY_DATA_MARKER}: ${error?.message ?? error}`);
    }
  }

  return {
    ...base,
    status: failed.length === 0 ? 'imported' : 'failed',
    imported,
    failed,
  };
}
