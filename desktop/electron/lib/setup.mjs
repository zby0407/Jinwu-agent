/**
 * First-run configuration bridge to Python's `jw.desktop_setup` JSON command.
 *
 * The setup command reads the API key from stdin (never argv) and returns a
 * secret-free JSON response. This module contains the pure helpers used by the
 * Electron main process and its tests.
 */

import { spawn } from 'node:child_process';

export const SETUP_MODULE = 'jw.desktop_setup';
export const SETUP_STATUS_TIMEOUT_MS = 30_000;
export const SETUP_SAVE_TIMEOUT_MS = 60_000;
export const SETUP_TEST_TIMEOUT_MS = 30_000;
export const SETUP_OUTPUT_LIMIT = 256 * 1024;

export class SetupCommandError extends Error {
  constructor(message, { code = 'SETUP_COMMAND_FAILED', detail } = {}) {
    super(message);
    this.name = 'SetupCommandError';
    this.code = code;
    this.detail = detail ?? null;
  }
}

/** Trim the form fields and discard anything the setup protocol does not own. */
export function normalizeSetupPayload(payload) {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw new SetupCommandError('配置内容不合法', { code: 'SETUP_PAYLOAD_INVALID' });
  }
  const result = {};
  for (const field of ['provider', 'model', 'api_key', 'base_url', 'proxy_mode']) {
    if (field === 'proxy_mode' && payload[field] === undefined) continue;
    const value = payload[field];
    if (value === undefined || value === null) {
      result[field] = '';
      continue;
    }
    if (typeof value !== 'string') {
      throw new SetupCommandError(`配置字段 ${field} 必须是字符串`, {
        code: 'SETUP_PAYLOAD_INVALID',
      });
    }
    result[field] = value.trim();
  }
  return result;
}

/** Parse the one-line JSON response emitted by `jw.desktop_setup`. */
export function parseSetupResponse(stdout, exitCode) {
  const text = String(stdout ?? '').trim();
  if (!text) {
    throw new SetupCommandError(
      exitCode === 0 ? '配置命令没有返回结果' : `配置命令失败（退出码 ${exitCode}）`,
      { code: 'SETUP_EMPTY_RESPONSE' },
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (cause) {
    throw new SetupCommandError('配置命令返回了无法解析的内容', {
      code: 'SETUP_INVALID_RESPONSE',
      detail: cause?.message,
    });
  }
  if (typeof parsed !== 'object' || parsed === null || parsed.ok !== true) {
    const message =
      typeof parsed?.error === 'string' && parsed.error
        ? parsed.error
        : `配置命令失败（退出码 ${exitCode}）`;
    throw new SetupCommandError(message, { code: 'SETUP_REJECTED' });
  }
  return parsed;
}

export function setupCommandArgs(mode) {
  if (!['status', 'configure', 'validate', 'test'].includes(mode)) {
    throw new SetupCommandError(`未知的配置命令模式：${mode}`, {
      code: 'SETUP_MODE_INVALID',
    });
  }
  return ['-m', SETUP_MODULE, mode];
}

function runSetupCommand({
  pythonPath,
  cwd,
  env,
  mode,
  stdin = '',
  timeoutMs,
  spawnImpl = spawn,
  signal,
}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    const child = spawnImpl(pythonPath, setupCommandArgs(mode), {
      cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      callback(value);
    };
    const timer = setTimeout(() => {
      finish(
        reject,
        new SetupCommandError(`配置命令超过 ${Math.round(timeoutMs / 1000)} 秒未完成`, {
          code: mode === 'test' ? 'TIMEOUT' : 'SETUP_TIMEOUT',
        }),
      );
      child.kill?.();
    }, timeoutMs);
    timer.unref?.();
    const abort = () => {
      finish(reject, new SetupCommandError('连接测试已取消', { code: 'SETUP_CANCELLED' }));
      child.kill?.();
    };
    signal?.addEventListener('abort', abort, { once: true });

    child.on('error', (error) => {
      finish(
        reject,
        new SetupCommandError(`无法启动配置命令：${error?.message ?? error}`, {
          code: 'SETUP_SPAWN_FAILED',
        }),
      );
    });
    child.stdout?.on('data', (chunk) => {
      if (stdout.length < SETUP_OUTPUT_LIMIT) stdout += String(chunk);
    });
    child.stderr?.on('data', (chunk) => {
      if (stderr.length < SETUP_OUTPUT_LIMIT) stderr += String(chunk);
    });
    child.on('close', (code) => {
      try {
        finish(resolve, parseSetupResponse(stdout, code ?? -1));
      } catch (error) {
        if (error instanceof SetupCommandError && !error.detail && stderr.trim()) {
          error.detail = stderr.trim().slice(-2_000);
        }
        finish(reject, error);
      }
    });
    child.stdin?.on('error', () => {
      // A rejected configuration may close stdin before the key is written.
    });
    if (signal?.aborted) abort();
    else child.stdin?.end(stdin);
  });
}

export function testSetupConnection({ pythonPath, cwd, env, payload, signal, spawnImpl }) {
  return runSetupCommand({ pythonPath, cwd, env, mode: 'test',
    stdin: JSON.stringify(normalizeSetupPayload(payload)), signal, spawnImpl,
    timeoutMs: SETUP_TEST_TIMEOUT_MS });
}

export function validateSetupConfiguration({ pythonPath, cwd, env, payload, spawnImpl }) {
  return runSetupCommand({ pythonPath, cwd, env, mode: 'validate',
    stdin: JSON.stringify(normalizeSetupPayload(payload)), spawnImpl,
    timeoutMs: SETUP_SAVE_TIMEOUT_MS });
}

export async function readSetupStatus({
  pythonPath,
  cwd,
  env,
  spawnImpl,
  timeoutMs = SETUP_STATUS_TIMEOUT_MS,
}) {
  return runSetupCommand({
    pythonPath,
    cwd,
    env,
    mode: 'status',
    timeoutMs,
    spawnImpl,
  });
}

export async function saveSetupConfiguration({
  pythonPath,
  cwd,
  env,
  payload,
  spawnImpl,
  timeoutMs = SETUP_SAVE_TIMEOUT_MS,
}) {
  const normalized = normalizeSetupPayload(payload);
  return runSetupCommand({
    pythonPath,
    cwd,
    env,
    mode: 'configure',
    stdin: JSON.stringify(normalized),
    timeoutMs,
    spawnImpl,
  });
}
