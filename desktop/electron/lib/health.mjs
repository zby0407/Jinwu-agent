/**
 * 健康检查：等待后端 `/ok` 与前端根路径可访问。
 *
 * 只访问 127.0.0.1；超时/进程提前退出都抛出可读错误，主进程据此展示错误页。
 */

export const BACKEND_HEALTH_PATH = '/ok';
// 冷启动实测：字节码缓存失效（首次 import 整个 jw 包）叠加 CPU 争抢时，
// langgraph dev 的导入阶段曾达到 131 秒，120 秒窗口会把仍然健康的启动判为失败，
// 因此后端窗口放宽到 180 秒；前端在本地只需十余秒，保持 90 秒。
export const BACKEND_HEALTH_TIMEOUT_MS = 180_000;
export const WEBUI_HEALTH_TIMEOUT_MS = 90_000;
export const HEALTH_INTERVAL_MS = 500;
export const HEALTH_PROBE_TIMEOUT_MS = 2_000;

export class HealthCheckError extends Error {
  constructor(message, { url, code = 'HEALTH_CHECK_FAILED', cause } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'HealthCheckError';
    this.code = code;
    this.url = url ?? null;
  }
}

/** 后端健康检查地址：`http://127.0.0.1:<port>/ok` */
export function backendHealthUrl(port, host = '127.0.0.1') {
  return `http://${host}:${port}${BACKEND_HEALTH_PATH}`;
}

/** 前端地址：`http://127.0.0.1:<port>/` */
export function webuiHealthUrl(port, host = '127.0.0.1') {
  return `http://${host}:${port}/`;
}

/**
 * 单次 HTTP 探测。
 * @returns {Promise<{ok: boolean, status: number, error?: string}>}
 */
export async function probeHttp(
  url,
  { timeoutMs = HEALTH_PROBE_TIMEOUT_MS, fetchImpl = globalThis.fetch } = {},
) {
  if (typeof fetchImpl !== 'function') {
    return { ok: false, status: 0, error: '当前环境缺少 fetch 实现' };
  }
  try {
    const signal =
      typeof AbortSignal?.timeout === 'function' ? AbortSignal.timeout(timeoutMs) : undefined;
    const response = await fetchImpl(url, {
      method: 'GET',
      redirect: 'manual',
      signal,
    });
    return { ok: Boolean(response?.ok), status: response?.status ?? 0 };
  } catch (cause) {
    return { ok: false, status: 0, error: cause?.message || String(cause) };
  }
}

/**
 * 轮询直到 HTTP 就绪。
 *
 * @param {object} options
 * @param {string} options.url
 * @param {number} [options.timeoutMs]
 * @param {number} [options.intervalMs]
 * @param {Function} [options.fetchImpl]
 * @param {(port: number) => void} [options.onAttempt]
 * @param {() => boolean} [options.isAlive] 返回 false 表示子进程已退出，立即失败
 * @param {AbortSignal} [options.signal] 兄弟服务失败时用于取消等待
 * @param {string} [options.label]
 * @returns {Promise<{attempts: number, status: number}>}
 */
export async function waitForHttp({
  url,
  timeoutMs = BACKEND_HEALTH_TIMEOUT_MS,
  intervalMs = HEALTH_INTERVAL_MS,
  fetchImpl,
  probe = probeHttp,
  isAlive,
  signal,
  label = url,
  sleep = defaultSleep,
  now = Date.now,
} = {}) {
  if (typeof url !== 'string' || url.length === 0) {
    throw new TypeError('waitForHttp 需要显式的 url');
  }
  const deadline = now() + timeoutMs;
  let attempts = 0;
  let lastError = '未知错误';

  while (true) {
    if (signal?.aborted) {
      throw new HealthCheckError(`${label} 健康检查已取消`, {
        url,
        code: 'HEALTH_ABORTED',
      });
    }
    if (typeof isAlive === 'function' && !isAlive()) {
      throw new HealthCheckError(`${label} 进程已退出，无法继续等待就绪`, {
        url,
        code: 'HEALTH_PROCESS_EXITED',
      });
    }
    attempts += 1;
    const result = await probe(url, { fetchImpl });
    if (result?.ok) {
      return { attempts, status: result.status ?? 0 };
    }
    lastError = result?.error || `HTTP ${result?.status ?? 0}`;
    if (now() >= deadline) {
      throw new HealthCheckError(
        `${label} 在 ${Math.round(timeoutMs / 1000)} 秒内未就绪（最后错误：${lastError}）`,
        { url, code: 'HEALTH_TIMEOUT' },
      );
    }
    const remaining = Math.max(0, deadline - now());
    await sleep(Math.min(intervalMs, remaining));
  }
}

function defaultSleep(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
  });
}
