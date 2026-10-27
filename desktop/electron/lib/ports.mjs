/**
 * 端口选择。
 *
 * 默认端口：后端（langgraph dev）6174，前端（Next.js standalone）4716。
 * 若默认端口被占用，各选一个相邻的可用端口向上扫描，绝不杀掉占用者。
 * 前后端端口必须不同：前端扫描时排除已选定的后端端口。
 */

import net from 'node:net';

export const DEFAULT_BACKEND_PORT = 6174;
export const DEFAULT_WEBUI_PORT = 4716;
export const PORT_SCAN_LIMIT = 50;
export const MIN_PORT = 1;
export const MAX_PORT = 65535;

export class PortUnavailableError extends Error {
  constructor(message, { port, limit } = {}) {
    super(message);
    this.name = 'PortUnavailableError';
    this.code = 'PORT_UNAVAILABLE';
    this.port = port ?? null;
    this.limit = limit ?? null;
  }
}

/**
 * 探测 127.0.0.1 上的端口是否可绑定。
 * 只监听回环地址（桌面外壳绝不对外暴露）。
 */
export function isPortAvailable(port, { host = '127.0.0.1', timeoutMs = 1500 } = {}) {
  return new Promise((resolve) => {
    const server = net.createServer();
    let settled = false;
    const finish = (available) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      try {
        server.close();
      } catch {
        // 已关闭或从未开始监听，忽略。
      }
      resolve(available);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    server.once('error', () => finish(false));
    server.once('listening', () => finish(true));
    try {
      server.listen({ port, host, exclusive: true });
    } catch {
      finish(false);
    }
  });
}

/**
 * 从 preferred 开始向上找一个可用端口。
 *
 * @param {number} preferred
 * @param {object} [options]
 * @param {(port: number) => Promise<boolean>|boolean} [options.isAvailable]
 * @param {Set<number>} [options.exclude] 额外排除的端口（例如已选定的后端端口）
 * @param {number} [options.limit]
 * @returns {Promise<number>}
 */
export async function findAvailablePort(
  preferred,
  { isAvailable = isPortAvailable, exclude = new Set(), limit = PORT_SCAN_LIMIT, host } = {},
) {
  assertPort(preferred, 'preferred');
  for (let offset = 0; offset <= limit; offset += 1) {
    const port = preferred + offset;
    if (port > MAX_PORT) {
      break;
    }
    if (exclude.has(port)) {
      continue;
    }
    if (await isAvailable(port, { host })) {
      return port;
    }
  }
  throw new PortUnavailableError(
    `端口 ${preferred} 起的 ${limit + 1} 个候选端口均不可用，请关闭占用端口的程序后重试。`,
    { port: preferred, limit },
  );
}

/**
 * 同时选择后端与前端端口，保证两者互不相同。
 *
 * @returns {Promise<{backendPort: number, webuiPort: number, backendIsDefault: boolean, webuiIsDefault: boolean}>}
 */
export async function chooseServicePorts({
  backendPreferred = DEFAULT_BACKEND_PORT,
  webuiPreferred = DEFAULT_WEBUI_PORT,
  isAvailable = isPortAvailable,
  limit = PORT_SCAN_LIMIT,
  host,
} = {}) {
  const backendPort = await findAvailablePort(backendPreferred, {
    isAvailable,
    limit,
    host,
  });
  const webuiPort = await findAvailablePort(webuiPreferred, {
    isAvailable,
    limit,
    host,
    exclude: new Set([backendPort]),
  });
  if (backendPort === webuiPort) {
    // 理论上不可能发生（exclude 已保证），保留防御式检查。
    throw new PortUnavailableError('后端与前端选中了同一个端口', { port: backendPort });
  }
  return {
    backendPort,
    webuiPort,
    backendIsDefault: backendPort === backendPreferred,
    webuiIsDefault: webuiPort === webuiPreferred,
  };
}

function assertPort(port, label) {
  if (!Number.isInteger(port) || port < MIN_PORT || port > MAX_PORT) {
    throw new RangeError(`${label} 端口必须是 [${MIN_PORT}, ${MAX_PORT}] 内的整数，当前为 ${port}`);
  }
}
