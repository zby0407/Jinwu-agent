/**
 * 运行时清单（runtime-manifest.json）解析与校验。
 *
 * 打包团队产出的清单位于 `<app resources>/runtime/runtime-manifest.json`，
 * 所有路径字段都相对于该清单所在的 runtime 根目录：
 *
 * {
 *   "schema_version": 1,
 *   "platform": "darwin" | "win32",
 *   "arch": "arm64" | "x64",
 *   "python": "python/bin/python3" | "python/python.exe",
 *   "node": "node/bin/node" | "node/node.exe",
 *   "langgraph": { "executable": "...", "args": ["-m", "langgraph_cli"] },
 *   "webui": "webui/dist/server.js",
 *   "projectRoot": "project",
 *   "pythonVersion": "...", "nodeVersion": "...",
 *   "jwVersion": "...", "webuiVersion": "..."
 * }
 *
 * 开发时可用环境变量 `JW_DESKTOP_RUNTIME_MANIFEST` 指向任意清单。
 */

import fs from 'node:fs';
import path from 'node:path';

export const RUNTIME_DIRECTORY_NAME = 'runtime';
export const RUNTIME_MANIFEST_FILENAME = 'runtime-manifest.json';
export const MANIFEST_SCHEMA_VERSION = 1;
export const SUPPORTED_MANIFEST_PLATFORMS = Object.freeze(['darwin', 'win32']);
export const SUPPORTED_MANIFEST_ARCHS = Object.freeze(['arm64', 'x64']);
export const RUNTIME_MANIFEST_ENV_VAR = 'JW_DESKTOP_RUNTIME_MANIFEST';

export class RuntimeManifestError extends Error {
  constructor(message, { code = 'RUNTIME_MANIFEST_INVALID', manifestPath, cause } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'RuntimeManifestError';
    this.code = code;
    this.manifestPath = manifestPath ?? null;
  }
}

/**
 * 候选清单位置（按优先级）。
 *
 * 1. `JW_DESKTOP_RUNTIME_MANIFEST`（显式覆盖；设置后不再回退，避免静默用错运行时）
 * 2. 打包后：`<resources>/runtime/runtime-manifest.json`
 * 3. 开发时：`<仓库根>/packaging/dist/runtime/runtime-manifest.json`
 *
 * @returns {Array<{path: string, source: string}>}
 */
export function manifestCandidates({
  env = process.env,
  resourcesPath,
  isPackaged = false,
  appRoot,
} = {}) {
  const override = typeof env?.[RUNTIME_MANIFEST_ENV_VAR] === 'string'
    ? env[RUNTIME_MANIFEST_ENV_VAR].trim()
    : '';
  if (override) {
    return [{ path: path.resolve(override), source: 'env' }];
  }
  const candidates = [];
  if (isPackaged && typeof resourcesPath === 'string' && resourcesPath.length > 0) {
    candidates.push({
      path: path.join(resourcesPath, RUNTIME_DIRECTORY_NAME, RUNTIME_MANIFEST_FILENAME),
      source: 'packaged-resources',
    });
  }
  if (typeof appRoot === 'string' && appRoot.length > 0) {
    candidates.push({
      path: path.resolve(
        appRoot,
        '..',
        'packaging',
        'dist',
        RUNTIME_DIRECTORY_NAME,
        RUNTIME_MANIFEST_FILENAME,
      ),
      source: 'dev-workspace',
    });
  }
  return candidates;
}

/**
 * 找到存在的清单文件；全部候选都不存在时抛出可读错误。
 * @returns {string}
 */
export function resolveManifestPath(options = {}) {
  const candidates = manifestCandidates(options);
  if (candidates.length === 0) {
    throw new RuntimeManifestError(
      '无法确定运行时清单位置：既没有设置 JW_DESKTOP_RUNTIME_MANIFEST，也没有可用的打包资源目录。',
      { code: 'RUNTIME_MANIFEST_NOT_FOUND' },
    );
  }
  for (const candidate of candidates) {
    if (fs.existsSync(candidate.path)) {
      return candidate.path;
    }
    if (candidate.source === 'env') {
      throw new RuntimeManifestError(
        `环境变量 ${RUNTIME_MANIFEST_ENV_VAR} 指向的运行时清单不存在：${candidate.path}`,
        { code: 'RUNTIME_MANIFEST_NOT_FOUND', manifestPath: candidate.path },
      );
    }
  }
  throw new RuntimeManifestError(
    `找不到运行时清单 ${RUNTIME_MANIFEST_FILENAME}。已尝试：\n${candidates
      .map((candidate) => `  - ${candidate.path}（${candidate.source}）`)
      .join('\n')}`,
    { code: 'RUNTIME_MANIFEST_NOT_FOUND' },
  );
}

/** 读取并解析 JSON。 */
export function readRuntimeManifest(manifestPath) {
  let text;
  try {
    text = fs.readFileSync(manifestPath, 'utf8');
  } catch (cause) {
    throw new RuntimeManifestError(
      `无法读取运行时清单：${manifestPath}（${cause?.code || cause?.message || cause}）`,
      { code: 'RUNTIME_MANIFEST_UNREADABLE', manifestPath, cause },
    );
  }
  try {
    return JSON.parse(text);
  } catch (cause) {
    throw new RuntimeManifestError(
      `运行时清单不是合法 JSON：${manifestPath}`,
      { code: 'RUNTIME_MANIFEST_PARSE_ERROR', manifestPath, cause },
    );
  }
}

/**
 * 校验清单结构与平台/架构（纯函数，便于测试）。
 *
 * 版本字段（pythonVersion 等）允许缺失，缺失时为 null；
 * 其余路径字段严格必填且必须是相对于 runtime 根目录的相对路径。
 *
 * @param {unknown} raw
 * @param {{platform?: string, arch?: string, manifestPath?: string}} [options]
 */
export function validateRuntimeManifest(
  raw,
  { platform = process.platform, arch = process.arch, manifestPath } = {},
) {
  const fail = (message, code = 'RUNTIME_MANIFEST_INVALID') => {
    throw new RuntimeManifestError(message, { code, manifestPath });
  };

  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    fail('运行时清单必须是 JSON 对象');
  }

  if (raw.schema_version !== MANIFEST_SCHEMA_VERSION) {
    fail(
      `运行时清单 schema_version 必须为 ${MANIFEST_SCHEMA_VERSION}，当前为 ${JSON.stringify(
        raw.schema_version,
      )}`,
      'RUNTIME_MANIFEST_SCHEMA_UNSUPPORTED',
    );
  }

  if (!SUPPORTED_MANIFEST_PLATFORMS.includes(raw.platform)) {
    fail(
      `运行时清单 platform 必须是 ${SUPPORTED_MANIFEST_PLATFORMS.join(' / ')}，当前为 ${JSON.stringify(
        raw.platform,
      )}`,
      'RUNTIME_MANIFEST_PLATFORM_UNSUPPORTED',
    );
  }
  if (!SUPPORTED_MANIFEST_PLATFORMS.includes(platform)) {
    fail(
      `当前宿主平台 ${platform} 不受支持（初版仅支持 ${SUPPORTED_MANIFEST_PLATFORMS.join(' / ')}）`,
      'RUNTIME_MANIFEST_HOST_UNSUPPORTED',
    );
  }
  if (raw.platform !== platform) {
    fail(
      `运行时清单平台为 ${raw.platform}，与当前宿主平台 ${platform} 不一致。请使用与系统匹配的安装包。`,
      'RUNTIME_MANIFEST_PLATFORM_MISMATCH',
    );
  }

  if (!SUPPORTED_MANIFEST_ARCHS.includes(raw.arch)) {
    fail(
      `运行时清单 arch 必须是 ${SUPPORTED_MANIFEST_ARCHS.join(' / ')}，当前为 ${JSON.stringify(
        raw.arch,
      )}`,
      'RUNTIME_MANIFEST_ARCH_UNSUPPORTED',
    );
  }
  if (raw.arch !== arch) {
    fail(
      `运行时清单架构为 ${raw.arch}，与当前宿主架构 ${arch} 不一致。请使用与系统匹配的安装包。`,
      'RUNTIME_MANIFEST_ARCH_MISMATCH',
    );
  }

  const langgraph = raw.langgraph;
  if (typeof langgraph !== 'object' || langgraph === null || Array.isArray(langgraph)) {
    fail('运行时清单缺少 langgraph 对象');
  }

  const requiredPaths = {
    python: raw.python,
    node: raw.node,
    'langgraph.executable': langgraph.executable,
    webui: raw.webui,
    projectRoot: raw.projectRoot,
  };
  for (const [field, value] of Object.entries(requiredPaths)) {
    assertRelativeRuntimePath(value, field, fail);
  }

  if (!Array.isArray(langgraph.args) || langgraph.args.length === 0) {
    fail('运行时清单 langgraph.args 必须是非空字符串数组');
  }
  for (const arg of langgraph.args) {
    if (!isNonEmptyString(arg)) {
      fail('运行时清单 langgraph.args 必须是非空字符串数组');
    }
  }

  return {
    schema_version: MANIFEST_SCHEMA_VERSION,
    platform: raw.platform,
    arch: raw.arch,
    python: normalizeRelative(raw.python),
    node: normalizeRelative(raw.node),
    langgraph: {
      executable: normalizeRelative(langgraph.executable),
      args: langgraph.args.map((arg) => arg.trim()),
    },
    webui: normalizeRelative(raw.webui),
    projectRoot: normalizeRelative(raw.projectRoot),
    pythonVersion: normalizeVersion(raw.pythonVersion),
    nodeVersion: normalizeVersion(raw.nodeVersion),
    jwVersion: normalizeVersion(raw.jwVersion),
    webuiVersion: normalizeVersion(raw.webuiVersion),
  };
}

/**
 * 把校验通过的清单解析成绝对路径集合（供主进程直接使用）。
 *
 * @param {unknown} raw
 * @param {{manifestPath: string, expectedPlatform?: string, expectedArch?: string}} options
 */
export function resolveRuntimeManifest(raw, options = {}) {
  const { manifestPath } = options;
  if (!isNonEmptyString(manifestPath)) {
    throw new RuntimeManifestError('resolveRuntimeManifest 需要清单的绝对路径', {
      code: 'RUNTIME_MANIFEST_INVALID',
    });
  }
  const manifest = validateRuntimeManifest(raw, {
    platform: options.expectedPlatform ?? process.platform,
    arch: options.expectedArch ?? process.arch,
    manifestPath,
  });
  const runtimeRoot = path.dirname(path.resolve(manifestPath));

  return Object.freeze({
    manifestPath: path.resolve(manifestPath),
    runtimeRoot,
    schemaVersion: manifest.schema_version,
    platform: manifest.platform,
    arch: manifest.arch,
    pythonPath: resolveWithinRoot(runtimeRoot, manifest.python, 'python', manifestPath),
    nodePath: resolveWithinRoot(runtimeRoot, manifest.node, 'node', manifestPath),
    langgraphExecutablePath: resolveWithinRoot(
      runtimeRoot,
      manifest.langgraph.executable,
      'langgraph.executable',
      manifestPath,
    ),
    langgraphArgs: Object.freeze([...manifest.langgraph.args]),
    langgraphCommand: Object.freeze([
      resolveWithinRoot(
        runtimeRoot,
        manifest.langgraph.executable,
        'langgraph.executable',
        manifestPath,
      ),
      ...manifest.langgraph.args,
    ]),
    webuiServerPath: resolveWithinRoot(runtimeRoot, manifest.webui, 'webui', manifestPath),
    projectRootPath: resolveWithinRoot(runtimeRoot, manifest.projectRoot, 'projectRoot', manifestPath),
    versions: Object.freeze({
      python: manifest.pythonVersion,
      node: manifest.nodeVersion,
      jw: manifest.jwVersion,
      webui: manifest.webuiVersion,
    }),
  });
}

/**
 * 一步到位：定位清单 → 读取 → 校验 → 解析绝对路径。
 * @param {object} options 见 manifestCandidates + expectedPlatform/expectedArch
 */
export function loadRuntimeManifest(options = {}) {
  const manifestPath = resolveManifestPath(options);
  const raw = readRuntimeManifest(manifestPath);
  return resolveRuntimeManifest(raw, {
    manifestPath,
    expectedPlatform: options.expectedPlatform,
    expectedArch: options.expectedArch,
  });
}

/**
 * 检查关键运行时文件是否存在（启动前快速失败，给出可读路径）。
 * @param {{pythonPath: string, nodePath: string, webuiServerPath: string}} runtime
 * @param {{exists?: (p: string) => boolean}} [options]
 * @returns {Array<{field: string, path: string}>}
 */
export function findMissingRuntimeFiles(runtime, { exists = fs.existsSync } = {}) {
  const checks = [
    ['python', runtime?.pythonPath],
    ['node', runtime?.nodePath],
    ['webui', runtime?.webuiServerPath],
  ];
  const missing = [];
  for (const [field, target] of checks) {
    if (typeof target !== 'string' || !exists(target)) {
      missing.push({ field, path: target ?? '(未解析)' });
    }
  }
  return missing;
}

function resolveWithinRoot(runtimeRoot, relativePath, field, manifestPath) {
  const resolved = path.resolve(runtimeRoot, relativePath);
  const relativeToRoot = path.relative(runtimeRoot, resolved);
  if (
    relativeToRoot === '' ||
    relativeToRoot.startsWith('..') ||
    path.isAbsolute(relativeToRoot)
  ) {
    throw new RuntimeManifestError(
      `运行时清单字段 ${field} 越出了 runtime 根目录：${relativePath}`,
      { code: 'RUNTIME_MANIFEST_PATH_ESCAPE', manifestPath },
    );
  }
  return resolved;
}

function assertRelativeRuntimePath(value, field, fail) {
  if (!isNonEmptyString(value)) {
    fail(`运行时清单字段 ${field} 必须是非空字符串路径`);
  }
  const trimmed = value.trim();
  if (isAbsoluteLike(trimmed)) {
    fail(
      `运行时清单字段 ${field} 必须是相对于 runtime 根目录的相对路径，当前为绝对路径：${trimmed}`,
    );
  }
  if (trimmed.split(/[\\/]+/).includes('..')) {
    fail(`运行时清单字段 ${field} 不允许包含 ".."：${trimmed}`);
  }
}

function isAbsoluteLike(value) {
  return (
    path.isAbsolute(value) ||
    /^[a-zA-Z]:[\\/]/.test(value) ||
    value.startsWith('\\\\') ||
    value.startsWith('//')
  );
}

function normalizeRelative(value) {
  return value.trim().replace(/\\/g, '/');
}

function normalizeVersion(value) {
  return isNonEmptyString(value) ? value.trim() : null;
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}
