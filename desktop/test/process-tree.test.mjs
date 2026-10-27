import { EventEmitter } from 'node:events';
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ProcessTreeError,
  buildSpawnOptions,
  buildTaskkillArgs,
  isProcessAlive,
  signalProcessGroup,
  taskkillTree,
  terminateProcessTree,
  waitForExit,
} from '../electron/lib/process-tree.mjs';

function fakeChild({ exitCode = null, signalCode = null } = {}) {
  const child = new EventEmitter();
  child.exitCode = exitCode;
  child.signalCode = signalCode;
  return child;
}

test('macOS 使用独立进程组，Windows 隐藏控制台窗口', () => {
  const macOptions = buildSpawnOptions({ platform: 'darwin', cwd: '/app', env: { A: '1' } });
  assert.equal(macOptions.detached, true);
  assert.equal(macOptions.windowsHide, false);
  assert.deepEqual(macOptions.stdio, ['ignore', 'pipe', 'pipe']);
  assert.equal(macOptions.shell, false);

  const winOptions = buildSpawnOptions({ platform: 'win32', cwd: 'C:\\app', env: { A: '1' } });
  assert.equal(winOptions.detached, false);
  assert.equal(winOptions.windowsHide, true);
});

test('Windows 清理参数为 taskkill /PID <pid> /T /F', () => {
  assert.deepEqual(buildTaskkillArgs(4321), ['/PID', '4321', '/T', '/F']);
  assert.throws(() => buildTaskkillArgs(0), ProcessTreeError);
  assert.throws(() => buildTaskkillArgs('4321'), ProcessTreeError);
});

test('signalProcessGroup 优先向进程组发信号', () => {
  const calls = [];
  const result = signalProcessGroup({
    pid: 99,
    signal: 'SIGTERM',
    killImpl: (pid, signal) => {
      calls.push([pid, signal]);
    },
  });
  assert.deepEqual(result, { ok: true });
  assert.deepEqual(calls, [[-99, 'SIGTERM']]);
});

test('进程组不存在时回退到单进程信号', () => {
  const calls = [];
  const result = signalProcessGroup({
    pid: 99,
    signal: 'SIGTERM',
    killImpl: (pid, signal) => {
      calls.push([pid, signal]);
      if (pid < 0) {
        throw new Error('ESRCH');
      }
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.fallback, true);
  assert.deepEqual(calls, [[-99, 'SIGTERM'], [99, 'SIGTERM']]);
});

test('进程已退出时直接返回 already-exited', async () => {
  const result = await terminateProcessTree({
    pid: 123,
    platform: 'darwin',
    isAlive: () => false,
  });
  assert.deepEqual(result, { status: 'already-exited', pid: 123, mode: 'none' });
});

test('macOS 先 SIGTERM 进程组，进程正常退出', async () => {
  const signals = [];
  let aliveChecks = 0;
  const result = await terminateProcessTree({
    pid: 321,
    platform: 'darwin',
    graceMs: 100,
    isAlive: () => {
      aliveChecks += 1;
      return aliveChecks <= 1; // 第一次检查存活，SIGTERM 后退出
    },
    sendSignal: ({ pid, signal }) => {
      signals.push({ pid, signal });
    },
    sleep: async () => {},
  });
  assert.equal(result.status, 'terminated');
  assert.equal(result.mode, 'process-group');
  assert.deepEqual(signals, [{ pid: 321, signal: 'SIGTERM' }]);
});

test('macOS 宽限期后仍存活则 SIGKILL 进程组', async () => {
  const signals = [];
  let alive = true;
  const result = await terminateProcessTree({
    pid: 654,
    platform: 'darwin',
    graceMs: 0,
    isAlive: () => alive,
    sendSignal: ({ pid, signal }) => {
      signals.push({ pid, signal });
      if (signal === 'SIGKILL') {
        alive = false;
      }
    },
    sleep: async () => {},
  });
  assert.equal(result.status, 'killed');
  assert.deepEqual(
    signals.map((entry) => entry.signal),
    ['SIGTERM', 'SIGKILL'],
  );
});

test('Windows 通过 taskkill 清理整棵树', async () => {
  const received = [];
  let alive = true;
  const result = await terminateProcessTree({
    pid: 888,
    platform: 'win32',
    isAlive: () => alive,
    runTaskkill: async ({ pid }) => {
      received.push(pid);
      alive = false; // taskkill /T /F 后进程树消失
      return { ok: true, args: buildTaskkillArgs(pid) };
    },
    sleep: async () => {},
  });
  assert.deepEqual(received, [888]);
  assert.equal(result.status, 'killed');
  assert.equal(result.mode, 'taskkill');
});

test('Windows taskkill 失败且进程仍存活时报告 taskkill-failed', async () => {
  const result = await terminateProcessTree({
    pid: 889,
    platform: 'win32',
    killWaitMs: 0,
    isAlive: () => true,
    runTaskkill: async () => ({ ok: false, error: 'access denied' }),
    sleep: async () => {},
  });
  assert.equal(result.status, 'taskkill-failed');
});

test('taskkillTree 使用注入的 execFile 并返回失败详情', async () => {
  let capturedArgs = null;
  const success = await taskkillTree({
    pid: 12,
    execFileImpl: (command, args, options, callback) => {
      capturedArgs = { command, args, options };
      callback(null, '', '');
    },
  });
  assert.equal(success.ok, true);
  assert.equal(capturedArgs.command, 'taskkill');
  assert.deepEqual(capturedArgs.args, ['/PID', '12', '/T', '/F']);
  assert.equal(capturedArgs.options.windowsHide, true);

  const failure = await taskkillTree({
    pid: 12,
    execFileImpl: (_command, _args, _options, callback) => {
      callback(new Error('boom'), '', '');
    },
  });
  assert.equal(failure.ok, false);
  assert.match(failure.error, /boom/);
});

test('waitForExit 在子进程退出时立即 resolve', async () => {
  const child = fakeChild();
  const promise = waitForExit(child, 1000);
  child.emit('exit', 0, null);
  assert.equal(await promise, true);
});

test('waitForExit 超时返回 false 且移除监听', async () => {
  const child = fakeChild();
  assert.equal(await waitForExit(child, 20), false);
  assert.equal(child.listenerCount('exit'), 0);
});

test('waitForExit 对已退出子进程立即返回', async () => {
  assert.equal(await waitForExit(fakeChild({ exitCode: 0 })), true);
});

test('isProcessAlive 对自身 pid 返回 true', () => {
  assert.equal(isProcessAlive(process.pid), true);
});
