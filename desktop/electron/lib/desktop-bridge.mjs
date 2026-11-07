/**
 * 桌面设置桥接（loopback only）。
 *
 * WebUI 运行在 Next.js 服务端，渲染进程本身没有任何 Node 能力。应用内“模型与 API”
 * 设置需要复用桌面端已有的校验 / 暂存 / 重启流程，因此由主进程监听 127.0.0.1 的
 * 随机端口，WebUI 的 `/api/model-config` 路由在服务端携带一次性票据请求它。
 *
 * 约束：
 * - 只监听 127.0.0.1，端口由系统分配（0）。
 * - 每个请求都必须携带 `x-jw-bridge-ticket`，否则 401。
 * - 不落地任何密钥；请求体仅在本进程内存中处理。
 * - 路径限定为 `/model-config`（读取/保存配置）与 `/provider-models`（查询在线
 *   模型列表），未知路径 404，避免变成通用代理。
 */

import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';

export const BRIDGE_TICKET_HEADER = 'x-jw-bridge-ticket';
export const BRIDGE_PATH = '/model-config';
export const BRIDGE_MODELS_PATH = '/provider-models';
export const BRIDGE_MAX_BODY_BYTES = 64 * 1024;
const BRIDGE_PATHS = new Set([BRIDGE_PATH, BRIDGE_MODELS_PATH]);

class BridgeRequestError extends Error {
  constructor(statusCode, message) {
    super(message);
    this.name = 'BridgeRequestError';
    this.statusCode = statusCode;
  }
}

function ticketsMatch(expected, received) {
  if (typeof expected !== 'string' || typeof received !== 'string') {
    return false;
  }
  const expectedBuffer = Buffer.from(expected);
  const receivedBuffer = Buffer.from(received);
  if (expectedBuffer.length !== receivedBuffer.length) {
    return false;
  }
  return timingSafeEqual(expectedBuffer, receivedBuffer);
}

function sendJson(response, statusCode, payload) {
  const body = JSON.stringify(payload ?? {});
  response.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  });
  response.end(body);
}

async function readJsonBody(request, limit = BRIDGE_MAX_BODY_BYTES) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) {
      throw new BridgeRequestError(413, '请求体过大。');
    }
    chunks.push(chunk);
  }
  if (size === 0) {
    return {};
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('not an object');
    }
    return parsed;
  } catch {
    throw new BridgeRequestError(400, '请求体不是合法的 JSON 对象。');
  }
}

/**
 * @param {object} options
 * @param {(request: {path: string, method: 'GET'|'POST', body: object}) => Promise<{status?: number, body?: object}>} options.handler
 * @param {(level: 'info'|'warn'|'error', message: string) => void} [options.log]
 * @returns {Promise<{url: string, ticket: string, port: number, close: () => Promise<void>}>}
 */
export function startDesktopBridge({ handler, log } = {}) {
  if (typeof handler !== 'function') {
    return Promise.reject(new TypeError('startDesktopBridge 需要 handler 函数'));
  }
  const ticket = randomBytes(24).toString('hex');
  const logger = typeof log === 'function' ? log : () => {};

  const server = createServer((request, response) => {
    void (async () => {
      try {
        const url = new URL(request.url ?? '/', 'http://127.0.0.1');
        if (!BRIDGE_PATHS.has(url.pathname)) {
          sendJson(response, 404, { ok: false, error: 'Not found.' });
          return;
        }
        if (!ticketsMatch(ticket, request.headers[BRIDGE_TICKET_HEADER])) {
          sendJson(response, 401, { ok: false, error: 'Unauthorized.' });
          return;
        }
        const method = request.method ?? 'GET';
        if (method !== 'GET' && method !== 'POST') {
          response.setHeader('allow', 'GET, POST');
          sendJson(response, 405, { ok: false, error: 'Method not allowed.' });
          return;
        }
        const body = method === 'POST' ? await readJsonBody(request) : {};
        const result = await handler({ path: url.pathname, method, body });
        sendJson(response, result?.status ?? 200, result?.body ?? {});
      } catch (error) {
        const statusCode = error instanceof BridgeRequestError ? error.statusCode : 500;
        if (statusCode >= 500) {
          logger('error', `桌面设置桥接请求失败：${error?.message ?? error}`);
        }
        sendJson(response, statusCode, {
          ok: false,
          error: statusCode >= 500 ? '内部错误。' : (error?.message ?? '请求失败。'),
        });
      }
    })();
  });

  server.on('clientError', (_error, socket) => {
    socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      server.unref?.();
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      logger('info', `桌面设置桥接监听 127.0.0.1:${port}`);
      resolve({
        url: `http://127.0.0.1:${port}${BRIDGE_PATH}`,
        ticket,
        port,
        close: () =>
          new Promise((done) => {
            server.close(() => done());
            // 保持进程不被 keep-alive 连接拖住。
            server.closeAllConnections?.();
          }),
      });
    });
  });
}
