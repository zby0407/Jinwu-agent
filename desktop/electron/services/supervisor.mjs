/**
 * 子进程监管（唯一监管者）。
 *
 * 每个服务（后端 Python / 前端 Node）对应一个子进程；进程退出通过
 * `onUnexpectedExit` 通知主进程展示错误页。主动停止时标记为 stopping，
 * 不触发崩溃回调。日志流由主进程打开并传入，stdout/stderr 直接写入
 * `<userData>/logs/*.log`。
 */

import { createChildProcess, terminateProcessTree, waitForExit } from '../lib/process-tree.mjs';

export const SERVICE_TAIL_LINES = 60;
export const STOP_EXIT_WAIT_MS = 3_000;

export class ServiceExitError extends Error {
  constructor(message, { name, code, signal, pid } = {}) {
    super(message);
    this.name = 'ServiceExitError';
    this.code = 'SERVICE_EXITED';
    this.serviceName = name ?? null;
    this.exitCode = code ?? null;
    this.signal = signal ?? null;
    this.pid = pid ?? null;
  }
}

export class ProcessSupervisor {
  #services = new Map();
  #exitHandlers = new Set();
  #stopping = new Set();
  #logger;
  #platform;
  #spawnProcess;
  #terminate;

  constructor({
    platform = process.platform,
    logger,
    spawnProcess = createChildProcess,
    terminate = terminateProcessTree,
  } = {}) {
    this.#logger = logger ?? null;
    this.#platform = platform;
    this.#spawnProcess = spawnProcess;
    this.#terminate = terminate;
  }

  /**
   * 启动一个受监管服务。
   *
   * @param {object} spec
   * @param {string} spec.name
   * @param {string} spec.command 绝对路径
   * @param {string[]} [spec.args]
   * @param {string} [spec.cwd]
   * @param {NodeJS.ProcessEnv} [spec.env]
   * @param {import('node:stream').Writable} [spec.logStream] 子进程输出写入流
   * @param {string} [spec.label] 日志中的显示名
   */
  spawnService({ name, command, args = [], cwd, env, logStream, label } = {}) {
    if (typeof name !== 'string' || name.length === 0) {
      throw new Error('spawnService 需要服务名');
    }
    if (this.isRunning(name)) {
      throw new Error(`服务 ${name} 已在运行（pid ${this.#services.get(name)?.pid}）`);
    }
    const child = this.#spawnProcess({
      command,
      args,
      cwd,
      env,
      platform: this.#platform,
    });
    const record = {
      name,
      label: label ?? name,
      command,
      args,
      child,
      pid: child.pid,
      startedAt: Date.now(),
      exited: null,
      tail: [],
    };

    const collect = (chunk) => this.#appendTail(record, chunk);
    child.stdout?.on('data', collect);
    child.stderr?.on('data', collect);
    if (logStream) {
      child.stdout?.pipe(logStream, { end: false });
      child.stderr?.pipe(logStream, { end: false });
    }
    child.on('error', (error) => {
      this.#logger?.error(`[${record.label}] 进程错误：${error?.message ?? error}`);
    });
    child.on('exit', (code, signal) => this.#handleExit(record, code, signal));

    this.#services.set(name, record);
    this.#logger?.info(`[${record.label}] 已启动 pid=${record.pid}`);
    return record;
  }

  isRunning(name) {
    const record = this.#services.get(name);
    return Boolean(record && record.exited === null);
  }

  runningServiceNames() {
    return [...this.#services.keys()].filter((name) => this.isRunning(name));
  }

  pidOf(name) {
    return this.#services.get(name)?.pid ?? null;
  }

  /** 最近输出行（用于本地排查；错误页不直接内嵌，避免密钥外泄）。 */
  tailOf(name) {
    return [...(this.#services.get(name)?.tail ?? [])];
  }

  /**
   * 注册“非主动退出”回调；返回取消注册函数。
   * @param {(info: {name: string, label: string, code: number|null, signal: string|null, pid: number|null}) => void} handler
   */
  onUnexpectedExit(handler) {
    if (typeof handler !== 'function') {
      throw new TypeError('onUnexpectedExit 需要函数');
    }
    this.#exitHandlers.add(handler);
    return () => this.#exitHandlers.delete(handler);
  }

  /**
   * 停止单个服务：先 SIGTERM 进程组 / taskkill，超时再 SIGKILL。
   * @param {string} name
   * @param {{graceMs?: number}} [options]
   */
  async stopService(name, { graceMs } = {}) {
    const record = this.#services.get(name);
    if (!record || record.exited !== null) {
      return { status: 'already-exited', pid: record?.pid ?? null, mode: 'none' };
    }
    this.#stopping.add(name);
    try {
      const result = await this.#terminate({
        pid: record.pid,
        graceMs,
        platform: this.#platform,
      });
      this.#logger?.info(
        `[${record.label}] 清理进程 pid=${record.pid} -> ${result.status}（${result.mode}）`,
      );
      if (record.exited === null && record.child) {
        await waitForExit(record.child, STOP_EXIT_WAIT_MS);
      }
      return result;
    } catch (error) {
      this.#logger?.error(
        `[${record.label}] 清理进程异常 pid=${record.pid}：${error?.message ?? error}`,
      );
      return { status: 'error', pid: record.pid, error };
    } finally {
      this.#stopping.delete(name);
    }
  }

  /** 停止全部服务（并行）。 */
  async stopAll(options = {}) {
    const names = this.runningServiceNames();
    const results = await Promise.all(names.map((name) => this.stopService(name, options)));
    return Object.fromEntries(names.map((name, index) => [name, results[index]]));
  }

  #handleExit(record, code, signal) {
    record.exited = { code, signal, at: Date.now() };
    const label = record.label;
    if (this.#stopping.has(record.name)) {
      this.#logger?.info(`[${label}] 已退出（code=${code}, signal=${signal}）`);
      return;
    }
    this.#logger?.error(
      `[${label}] 意外退出（code=${code}, signal=${signal}, pid=${record.pid}）`,
    );
    const info = {
      name: record.name,
      label,
      code,
      signal,
      pid: record.pid,
    };
    for (const handler of this.#exitHandlers) {
      try {
        handler(info);
      } catch (error) {
        this.#logger?.error(`崩溃回调异常：${error?.message ?? error}`);
      }
    }
  }

  #appendTail(record, chunk) {
    const text = String(chunk);
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trimEnd();
      if (trimmed.length === 0) {
        continue;
      }
      record.tail.push(trimmed);
    }
    if (record.tail.length > SERVICE_TAIL_LINES) {
      record.tail.splice(0, record.tail.length - SERVICE_TAIL_LINES);
    }
  }
}
