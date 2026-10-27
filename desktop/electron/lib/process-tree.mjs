/**
 * 子进程与进程树清理。
 *
 * 契约：
 * - macOS：`detached: true` 让子进程成为独立进程组组长；关闭时先对进程组发
 *   SIGTERM，等待宽限期后仍存活则 SIGKILL 整组（python → langgraph、node 子树）。
 * - Windows：`windowsHide: true` 隐藏控制台窗口；清理用
 *   `taskkill /PID <pid> /T /F` 杀整棵树（包含 langgraph 子进程）。
 *
 * 终止逻辑接受依赖注入（signal / taskkill / isAlive / sleep），因此单元测试
 * 不需要真的启动进程。
 */

import { execFile, spawn } from 'node:child_process';

export const TERMINATE_GRACE_MS = 5_000;
export const KILL_WAIT_MS = 3_000;
export const EXIT_WAIT_MS = 5_000;
export const WIN32_HIDE_WINDOW = true;

export class ProcessTreeError extends Error {
  constructor(message, { code = 'PROCESS_TREE_FAILED', pid, cause } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'ProcessTreeError';
    this.code = code;
    this.pid = pid ?? null;
  }
}

/**
 * 生成 spawn 选项：macOS 独立进程组，Windows 隐藏窗口；两条平台都只接管输出。
 * @param {{platform?: string, cwd?: string, env?: NodeJS.ProcessEnv}} options
 */
export function buildSpawnOptions({ platform = process.platform, cwd, env } = {}) {
  return {
    cwd,
    env,
    detached: platform === 'darwin',
    windowsHide: platform === 'win32' ? WIN32_HIDE_WINDOW : false,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: false,
  };
}

/** `taskkill /PID <pid> /T /F` —— 终止整棵进程树。 */
export function buildTaskkillArgs(pid) {
  assertPid(pid);
  return ['/PID', String(pid), '/T', '/F'];
}

/**
 * 启动受监管子进程。
 * @param {object} options
 * @param {string} options.command 绝对路径（随包 python / node）
 * @param {string[]} [options.args]
 * @param {string} [options.cwd]
 * @param {NodeJS.ProcessEnv} [options.env]
 * @param {string} [options.platform]
 * @param {typeof spawn} [options.spawnImpl]
 */
export function createChildProcess({
  command,
  args = [],
  cwd,
  env,
  platform = process.platform,
  spawnImpl = spawn,
}) {
  if (typeof command !== 'string' || command.length === 0) {
    throw new ProcessTreeError('子进程缺少可执行文件路径');
  }
  if (!Array.isArray(args)) {
    throw new ProcessTreeError('子进程 args 必须是字符串数组');
  }
  const child = spawnImpl(command, args, buildSpawnOptions({ platform, cwd, env }));
  return child;
}

/**
 * 等待子进程退出。
 * @param {import('node:child_process').ChildProcess} child
 * @param {number} [timeoutMs]
 * @returns {Promise<boolean>} 是否在超时前退出
 */
export function waitForExit(child, timeoutMs = EXIT_WAIT_MS) {
  if (!child || typeof child.once !== 'function') {
    return Promise.resolve(true);
  }
  if (child.exitCode !== null && child.exitCode !== undefined) {
    return Promise.resolve(true);
  }
  if (child.signalCode !== null && child.signalCode !== undefined) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const onExit = () => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(true);
    };
    child.once('exit', onExit);
    timer = setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      if (typeof child.removeListener === 'function') {
        child.removeListener('exit', onExit);
      }
      resolve(false);
    }, timeoutMs);
  });
}

/**
 * 向进程组发送信号（macOS 等 POSIX 平台）。进程组不存在时回退到单进程。
 * @returns {{ok: boolean, error?: Error, fallback?: boolean}}
 */
export function signalProcessGroup({ pid, signal, killImpl = process.kill }) {
  assertPid(pid);
  try {
    killImpl(-pid, signal);
    return { ok: true };
  } catch (groupError) {
    try {
      killImpl(pid, signal);
      return { ok: true, fallback: true };
    } catch (error) {
      return { ok: false, error: error ?? groupError };
    }
  }
}

/**
 * 调用 taskkill 清理整棵进程树（Windows）。
 * @returns {Promise<{ok: boolean, error?: string, args: string[]}>}
 */
export function taskkillTree({ pid, execFileImpl = execFile, args = buildTaskkillArgs(pid) } = {}) {
  assertPid(pid);
  return new Promise((resolve) => {
    try {
      execFileImpl('taskkill', args, { windowsHide: true }, (error, stdout, stderr) => {
        if (error) {
          resolve({
            ok: false,
            error: String(stderr || error.message || error).trim(),
            args,
          });
          return;
        }
        resolve({ ok: true, args });
      });
    } catch (error) {
      resolve({ ok: false, error: error?.message || String(error), args });
    }
  });
}

/** 进程是否仍存活（pid 存在且不属于其他用户）。 */
export function isProcessAlive(pid) {
  assertPid(pid);
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM 表示进程存在但无权限探测，视为存活。
    return error?.code === 'EPERM';
  }
}

/**
 * 终止进程树。
 *
 * @param {object} options
 * @param {number} options.pid
 * @param {string} [options.platform]
 * @param {number} [options.graceMs]
 * @param {number} [options.killWaitMs]
 * @param {(pid: number) => boolean} [options.isAlive]
 * @param {(args: {pid: number, signal: string}) => unknown} [options.sendSignal]
 * @param {(args: {pid: number}) => Promise<{ok: boolean, error?: string}>} [options.runTaskkill]
 * @param {(ms: number) => Promise<unknown>} [options.sleep]
 * @returns {Promise<{status: 'already-exited'|'terminated'|'killed'|'still-running'|'taskkill-failed', pid: number, mode: string}>}
 */
export async function terminateProcessTree({
  pid,
  platform = process.platform,
  graceMs = TERMINATE_GRACE_MS,
  killWaitMs = KILL_WAIT_MS,
  isAlive = isProcessAlive,
  sendSignal = signalProcessGroup,
  runTaskkill = taskkillTree,
  sleep = defaultSleep,
} = {}) {
  assertPid(pid);
  if (!isAlive(pid)) {
    return { status: 'already-exited', pid, mode: 'none' };
  }

  if (platform === 'win32') {
    const result = await runTaskkill({ pid });
    const dead = await waitUntilDead({ pid, isAlive, sleep, timeoutMs: killWaitMs });
    if (!result?.ok && !dead) {
      return { status: 'taskkill-failed', pid, mode: 'taskkill', error: result?.error };
    }
    return { status: dead ? 'killed' : 'still-running', pid, mode: 'taskkill' };
  }

  // POSIX（macOS）：先礼后兵，对进程组发信号。
  await sendSignal({ pid, signal: 'SIGTERM', processGroup: true });
  const exitedGracefully = await waitUntilDead({
    pid,
    isAlive,
    sleep,
    timeoutMs: graceMs,
  });
  if (exitedGracefully) {
    return { status: 'terminated', pid, mode: 'process-group' };
  }
  await sendSignal({ pid, signal: 'SIGKILL', processGroup: true });
  const dead = await waitUntilDead({ pid, isAlive, sleep, timeoutMs: killWaitMs });
  return { status: dead ? 'killed' : 'still-running', pid, mode: 'process-group' };
}

async function waitUntilDead({ pid, isAlive, sleep, timeoutMs, pollMs = 100 }) {
  const deadline = Date.now() + timeoutMs;
  while (isAlive(pid)) {
    if (Date.now() >= deadline) {
      return false;
    }
    await sleep(Math.min(pollMs, Math.max(0, deadline - Date.now())));
  }
  return true;
}

function assertPid(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    throw new ProcessTreeError(`非法 pid：${pid}`);
  }
}

function defaultSleep(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, Math.max(0, ms));
  });
}
