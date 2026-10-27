/**
 * 残留后端进程清理（兜底）。
 *
 * 背景：`jw deploy` 通过 `subprocess.Popen(..., start_new_session=True)` 启动
 * `python -m langgraph_cli dev`，该进程不在桌面外壳的进程组里。正常情况下
 * `jw deploy` 收到 SIGTERM 后会通过 atexit 清理它；但若 SIGTERM 恰好发生在其
 * 注册清理钩子之前（例如启动过程中立即退出），langgraph dev 会变成孤儿并继续
 * 占用后端端口。
 *
 * 兜底策略（安全优先，绝不误杀无关进程）：
 * 1. 我们独占使用的后端端口在后端进程已退出后仍能访问 `/ok`；
 * 2. 通过 lsof / netstat 找到监听该端口的 pid；
 * 3. 校验该 pid 的命令行同时包含 langgraph 标识与本端口；
 * 4. 满足以上全部条件才对整棵进程树发 SIGTERM/SIGKILL 或 taskkill /T /F。
 *
 * 任一校验失败都只记录警告并跳过。
 */

import { execFile } from 'node:child_process';
import path from 'node:path';

import { backendHealthUrl, probeHttp } from './health.mjs';
import { isProcessAlive, terminateProcessTree } from './process-tree.mjs';

export const ORPHAN_POLL_INTERVAL_MS = 200;
export const ORPHAN_WAIT_MS = 2_000;

/**
 * 解析端口监听者列表（纯函数，便于测试）。
 *
 * - macOS `lsof -nP -tiTCP:<port> -sTCP:LISTEN`：每行一个 pid
 * - Windows `netstat -ano -p tcp`：形如
 *   `  TCP    127.0.0.1:6174    0.0.0.0:0    LISTENING    42540`
 *
 * @param {string} output
 * @param {{platform?: string, port: number}} options
 * @returns {number[]}
 */
export function parseListeningPids(output, { platform = process.platform, port } = {}) {
  if (typeof output !== 'string' || output.length === 0) {
    return [];
  }
  const pids = new Set();
  if (platform === 'win32') {
    for (const line of output.split(/\r?\n/)) {
      const columns = line.trim().split(/\s+/);
      if (columns.length < 4) {
        continue;
      }
      const localAddress = columns[1];
      const state = columns[3];
      const pid = Number.parseInt(columns[columns.length - 1], 10);
      if (state !== 'LISTENING' || !Number.isInteger(pid)) {
        continue;
      }
      if (localAddress.endsWith(`:${port}`)) {
        pids.add(pid);
      }
    }
    return [...pids];
  }
  for (const line of output.split(/\r?\n/)) {
    const pid = Number.parseInt(line.trim(), 10);
    if (Number.isInteger(pid) && pid > 0) {
      pids.add(pid);
    }
  }
  return [...pids];
}

/**
 * 命令行校验：必须是 langgraph CLI，且端口与我们一致。
 * 只有这一条命中时才允许清理，避免误杀用户自己用同一 jw 数据目录启动的
 * 其他实例（端口不同）。
 */
export function commandLineMatchesLanggraph(commandLine, port) {
  if (typeof commandLine !== 'string' || commandLine.length === 0) {
    return false;
  }
  const text = commandLine.toLowerCase();
  if (!text.includes('langgraph')) {
    return false;
  }
  return (
    text.includes(`--port ${port}`) ||
    text.includes(`--port=${port}`) ||
    new RegExp(`--port\\s+${port}(\\s|$)`).test(text)
  );
}

/** 读取端口监听 pid。 */
export function findListeningPids({ port, platform = process.platform, execFileImpl = execFile } = {}) {
  const command = platform === 'win32' ? 'netstat' : 'lsof';
  const args =
    platform === 'win32'
      ? ['-ano', '-p', 'tcp']
      : ['-nP', `-tiTCP:${port}`, '-sTCP:LISTEN'];
  return runCommand(command, args, execFileImpl).then((output) =>
    parseListeningPids(output, { platform, port }),
  );
}

/**
 * 解析 `lsof -nP -a -d txt -p <pid>` 输出，取第一个 REG txt 文件作为可执行文件路径。
 * 纯函数，便于测试。
 */
export function parseLsofExecutablePath(output) {
  if (typeof output !== 'string' || output.length === 0) {
    return '';
  }
  for (const line of output.split(/\r?\n/)) {
    if (!line.trim() || line.startsWith('COMMAND')) {
      continue;
    }
    const match = line.match(/\s+txt\s+REG\s+\S+\s+\S+\s+\S+\s+(\/.*)$/);
    if (match) {
      return match[1].trim();
    }
  }
  return '';
}

/** 可执行文件是否就是随包 Python（同目录且 basename 以 python 开头）。 */
export function isBundledPythonExecutable(executablePath, pythonPath) {
  if (typeof executablePath !== 'string' || executablePath.length === 0) {
    return false;
  }
  if (typeof pythonPath !== 'string' || pythonPath.length === 0) {
    return false;
  }
  const executableDir = path.dirname(path.resolve(executablePath));
  const pythonDir = path.dirname(path.resolve(pythonPath));
  const executableName = path.basename(executablePath).toLowerCase();
  return executableDir === pythonDir && executableName.startsWith('python');
}

/**
 * 读取进程可执行文件路径（macOS/Linux 用 lsof）。失败返回空字符串。
 * Windows 上 lsof 不可用，返回空字符串，由命令行校验兜底。
 */
export function readProcessExecutablePath({
  pid,
  platform = process.platform,
  execFileImpl = execFile,
} = {}) {
  if (platform === 'win32') {
    return Promise.resolve('');
  }
  return runCommand('lsof', ['-nP', '-a', '-d', 'txt', '-p', String(pid)], execFileImpl).then(
    parseLsofExecutablePath,
  );
}

/** 读取进程命令行（macOS ps / Windows PowerShell）。读取失败返回空字符串。 */
export function readProcessCommandLine({
  pid,
  platform = process.platform,
  execFileImpl = execFile,
} = {}) {
  if (platform === 'win32') {
    return runCommand(
      'powershell',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `(Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}').CommandLine`,
      ],
      execFileImpl,
    );
  }
  return runCommand('ps', ['-o', 'command=', '-p', String(pid)], execFileImpl);
}

/**
 * 等待后端端口上的 /ok 消失；返回是否已消失。
 * @returns {Promise<boolean>}
 */
export async function waitForPortRelease({
  port,
  timeoutMs = ORPHAN_WAIT_MS,
  intervalMs = ORPHAN_POLL_INTERVAL_MS,
  probe = probeHttp,
  sleep = defaultSleep,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await probe(backendHealthUrl(port), { timeoutMs: 800 });
    if (!result?.ok) {
      return true;
    }
    await sleep(intervalMs);
  }
  return false;
}

/**
 * 兜底清理：仅清理“仍在我们端口上提供 /ok 的 langgraph dev”进程树。
 *
 * @param {object} options
 * @param {number} options.port 我们使用的后端端口
 * @param {string} [options.platform]
 * @param {object} [options.logger]
 * @param {Function} [options.probe]
 * @param {Function} [options.listPids]
 * @param {Function} [options.readCommandLine]
 * @param {Function} [options.isAlive]
 * @param {Function} [options.terminate]
 * @param {Function} [options.sleep]
 * @returns {Promise<{swept: number, orphans: number[], status: string}>}
 */
export async function sweepOrphanedBackend({
  port,
  platform = process.platform,
  logger,
  probe = probeHttp,
  listPids = findListeningPids,
  readCommandLine = readProcessCommandLine,
  readExecutablePath = readProcessExecutablePath,
  pythonPath = '',
  isAlive = isProcessAlive,
  terminate = terminateProcessTree,
  sleep = defaultSleep,
  waitForRelease = waitForPortRelease,
} = {}) {
  if (!Number.isInteger(port) || port <= 0) {
    return { swept: 0, orphans: [], status: 'invalid-port' };
  }
  const released = await waitForRelease({ port, probe, sleep });
  if (released) {
    return { swept: 0, orphans: [], status: 'released' };
  }

  const pids = await listPids({ port, platform });
  if (pids.length === 0) {
    logger?.warn(`后端端口 ${port} 仍在响应 /ok，但未找到监听进程 pid；跳过兜底清理`);
    return { swept: 0, orphans: [], status: 'no-pid' };
  }

  const killed = [];
  for (const pid of pids) {
    if (!isAlive(pid)) {
      continue;
    }
    // 身份校验（任一通过即可）：
    // 1) 可执行文件就是随包 Python（macOS/Linux，通过 lsof 读取，不依赖 ps）
    // 2) 命令行同时包含 langgraph 标识与本端口
    const executablePath = await readExecutablePath({ pid, platform });
    const byExecutable = isBundledPythonExecutable(executablePath, pythonPath);
    const byCommandLine = commandLineMatchesLanggraph(
      await readCommandLine({ pid, platform }),
      port,
    );
    if (!byExecutable && !byCommandLine) {
      logger?.warn(
        `端口 ${port} 的监听进程 pid=${pid} 无法确认为本次启动的 langgraph dev，跳过清理`,
      );
      continue;
    }
    const result = await terminate({ pid, platform, graceMs: 3_000 });
    killed.push(pid);
    logger?.info(
      `兜底清理残留 langgraph dev：pid=${pid} -> ${result.status}（${result.mode}）`,
    );
  }
  return {
    swept: killed.length,
    orphans: killed,
    status: killed.length > 0 ? 'swept' : 'skipped',
  };
}

function runCommand(command, args, execFileImpl) {
  return new Promise((resolve) => {
    try {
      execFileImpl(command, args, { windowsHide: true, timeout: 5_000 }, (error, stdout) => {
        if (error) {
          resolve('');
          return;
        }
        resolve(String(stdout ?? ''));
      });
    } catch {
      resolve('');
    }
  });
}

function defaultSleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, Math.max(0, ms));
  });
}
