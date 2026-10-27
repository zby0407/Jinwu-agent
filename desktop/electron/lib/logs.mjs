/**
 * 日志路径与桌面日志写入器。
 *
 * 日志契约（全部位于 `<userData>/logs/`）：
 * - backend.log：随包 Python 后端（`python -m jw deploy`）的 stdout + stderr
 * - webui.log：随包 Node WebUI（Next.js standalone server）的 stdout + stderr
 * - desktop.log：桌面外壳自身的启动/端口/清理记录
 *
 * 密钥永远不写入日志：桌面日志只记录环境变量名，不记录值。
 */

import fs from 'node:fs';
import path from 'node:path';

export const LOG_DIRECTORY_NAME = 'logs';

export const LOG_SERVICES = Object.freeze(['desktop', 'backend', 'webui']);

const LOG_FILE_NAMES = Object.freeze({
  desktop: 'desktop.log',
  backend: 'backend.log',
  webui: 'webui.log',
});

/** `<userData>/logs` */
export function logDirectory(userDataDir) {
  if (typeof userDataDir !== 'string' || userDataDir.trim().length === 0) {
    throw new TypeError('logDirectory 需要显式的 userDataDir');
  }
  return path.join(userDataDir, LOG_DIRECTORY_NAME);
}

/**
 * 解析某个服务的日志文件路径。
 * @param {string} userDataDir
 * @param {'desktop'|'backend'|'webui'} service
 */
export function logFilePath(userDataDir, service) {
  const fileName = LOG_FILE_NAMES[service];
  if (!fileName) {
    throw new RangeError(
      `未知的日志类型：${service}。可选值：${LOG_SERVICES.join(', ')}`,
    );
  }
  return path.join(logDirectory(userDataDir), fileName);
}

/**
 * 一次性解析全部日志路径。
 * @param {string} userDataDir
 * @returns {{directory: string, desktop: string, backend: string, webui: string}}
 */
export function resolveLogPaths(userDataDir) {
  return {
    directory: logDirectory(userDataDir),
    desktop: logFilePath(userDataDir, 'desktop'),
    backend: logFilePath(userDataDir, 'backend'),
    webui: logFilePath(userDataDir, 'webui'),
  };
}

/** 创建日志目录（幂等）。 */
export function ensureLogDirectory(userDataDir) {
  const directory = logDirectory(userDataDir);
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

/**
 * 以追加模式打开日志写入流。返回的流可直接 pipe 子进程 stdout/stderr。
 * @param {string} filePath
 */
export function createLogStream(filePath) {
  return fs.createWriteStream(filePath, { flags: 'a', encoding: 'utf8' });
}

/** 纯函数：格式化一行桌面临时日志（便于测试）。 */
export function formatLogLine({ time = new Date(), level = 'info', message = '' } = {}) {
  const timestamp = time instanceof Date ? time.toISOString() : String(time);
  return `${timestamp} [${String(level).toUpperCase()}] ${String(message)}`;
}

/**
 * 桌面外壳日志写入器：写入 `<userData>/logs/desktop.log`，同时可回显到终端。
 */
export class DesktopLogger {
  #stream = null;
  #console;

  constructor({ logPath, echoToConsole = true, now = () => new Date() } = {}) {
    this.logPath = logPath ?? null;
    this.now = now;
    this.#console = echoToConsole;
    if (this.logPath) {
      this.#stream = createLogStream(this.logPath);
      // 日志写入失败不应影响主流程。
      this.#stream.on('error', () => {
        this.#stream = null;
      });
    }
  }

  info(message) {
    this.#write('info', message);
  }

  warn(message) {
    this.#write('warn', message);
  }

  error(message) {
    this.#write('error', message);
  }

  /** 记录环境变量时只记录变量名，绝不记录值（避免密钥落盘）。 */
  describeEnvironment(env, { label = '环境变量' } = {}) {
    const names = Object.keys(env ?? {}).sort();
    this.info(`${label}（仅名称）：${names.join(', ')}`);
  }

  #write(level, message) {
    const line = formatLogLine({ time: this.now(), level, message });
    if (this.#stream) {
      this.#stream.write(`${line}\n`);
    }
    if (this.#console) {
      const sink = level === 'error' || level === 'warn' ? console.error : console.log;
      sink(line);
    }
  }

  /** 幂等关闭底层流。 */
  close() {
    return new Promise((resolve) => {
      const stream = this.#stream;
      this.#stream = null;
      if (!stream) {
        resolve();
        return;
      }
      stream.end(() => resolve());
    });
  }
}
