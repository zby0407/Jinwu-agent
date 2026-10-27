/**
 * 平台路径解析：用户数据目录与默认工作区目录。
 *
 * 契约（初版仅支持 macOS arm64 与 Windows x64）：
 * - macOS 用户数据：`~/Library/Application Support/Jinwu`
 * - Windows 用户数据：`%LOCALAPPDATA%\Jinwu`
 * - 默认工作区：`~/Documents/Jinwu` / `%USERPROFILE%\Documents\Jinwu`
 *
 * 这些函数保持纯函数（显式传入 platform / homedir / env），便于在
 * Linux 或 CI 上直接做单元测试。
 */

import path from 'node:path';

export const APP_NAME = 'Jinwu';
export const SUPPORTED_PLATFORMS = Object.freeze(['darwin', 'win32']);

export class UnsupportedPlatformError extends Error {
  constructor(platform) {
    super(
      `不支持的平台：${platform}。金乌桌面外壳初版仅支持 macOS arm64（darwin）与 Windows x64（win32）。`,
    );
    this.name = 'UnsupportedPlatformError';
    this.code = 'UNSUPPORTED_PLATFORM';
    this.platform = platform;
  }
}

/**
 * 解析用户数据目录。必须在 Electron `app.ready` 之前调用
 * `app.setPath('userData', <result>)`，否则单实例锁与日志会落在 Electron 默认目录。
 *
 * @param {object} options
 * @param {string} [options.platform]
 * @param {string} options.homedir 用户主目录
 * @param {NodeJS.ProcessEnv} [options.env]
 * @returns {string}
 */
export function resolveUserDataDirectory({
  platform = process.platform,
  homedir,
  env = process.env,
} = {}) {
  if (!isNonEmptyString(homedir)) {
    throw new TypeError('resolveUserDataDirectory 需要显式的 homedir');
  }
  if (platform === 'darwin') {
    return path.join(homedir, 'Library', 'Application Support', APP_NAME);
  }
  if (platform === 'win32') {
    const localAppData =
      isNonEmptyString(env?.LOCALAPPDATA) && env.LOCALAPPDATA.trim().length > 0
        ? env.LOCALAPPDATA
        : path.join(homedir, 'AppData', 'Local');
    return path.join(localAppData, APP_NAME);
  }
  throw new UnsupportedPlatformError(platform);
}

/**
 * 解析默认工作区目录（`~/Documents/Jinwu` 或 `%USERPROFILE%\Documents\Jinwu`）。
 *
 * @param {object} options
 * @param {string} options.homedir
 * @param {string} [options.documentsDir] 显式覆盖 Documents 目录（测试或系统特殊布局）
 * @returns {string}
 */
export function resolveWorkspaceDirectory({ homedir, documentsDir } = {}) {
  if (!isNonEmptyString(homedir)) {
    throw new TypeError('resolveWorkspaceDirectory 需要显式的 homedir');
  }
  const documents = isNonEmptyString(documentsDir)
    ? documentsDir
    : path.join(homedir, 'Documents');
  return path.join(documents, APP_NAME);
}

/** `<userData>/data` —— 传给后端的 `JW_DATA_DIR`。 */
export function resolveDataDirectory(userDataDir) {
  return path.join(userDataDir, 'data');
}

/** `<userData>/config` —— 传给前后端的 `XDG_CONFIG_HOME`。 */
export function resolveConfigDirectory(userDataDir) {
  return path.join(userDataDir, 'config');
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}
