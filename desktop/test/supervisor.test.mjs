import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import assert from 'node:assert/strict';
import test from 'node:test';

import { ProcessSupervisor, SERVICE_TAIL_LINES } from '../electron/services/supervisor.mjs';

function fakeChild(pid) {
  const child = new EventEmitter();
  child.pid = pid;
  child.exitCode = null;
  child.signalCode = null;
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  return child;
}

function createSupervisor() {
  const spawned = [];
  const supervisor = new ProcessSupervisor({
    platform: 'darwin',
    logger: null,
    spawnProcess: ({ command, args }) => {
      const child = fakeChild(4000 + spawned.length);
      spawned.push({ child, command, args });
      return child;
    },
    terminate: async ({ pid }) => {
      const entry = spawned.find((item) => item.child.pid === pid);
      entry?.child.emit('exit', null, 'SIGTERM');
      return { status: 'terminated', pid, mode: 'process-group' };
    },
  });
  return { supervisor, spawned };
}

test('spawnService 注册服务并回传 pid', () => {
  const { supervisor } = createSupervisor();
  const record = supervisor.spawnService({
    name: 'backend',
    command: '/runtime/python/bin/python3',
    args: ['-m', 'jw', 'deploy', '--port', '6174'],
  });
  assert.equal(record.name, 'backend');
  assert.equal(supervisor.isRunning('backend'), true);
  assert.equal(supervisor.pidOf('backend'), record.pid);
  assert.deepEqual(supervisor.runningServiceNames(), ['backend']);
});

test('重复启动同名服务抛错', () => {
  const { supervisor } = createSupervisor();
  supervisor.spawnService({ name: 'webui', command: '/runtime/node/bin/node' });
  assert.throws(
    () => supervisor.spawnService({ name: 'webui', command: '/runtime/node/bin/node' }),
    /已在运行/,
  );
});

test('非主动退出触发 onUnexpectedExit 回调', async () => {
  const { supervisor, spawned } = createSupervisor();
  const events = [];
  const unsubscribe = supervisor.onUnexpectedExit((info) => events.push(info));
  supervisor.spawnService({ name: 'backend', command: '/python', label: '后端' });
  spawned.at(-1).child.emit('exit', 1, null);
  assert.equal(events.length, 1);
  assert.equal(events[0].name, 'backend');
  assert.equal(events[0].label, '后端');
  assert.equal(events[0].code, 1);
  assert.equal(supervisor.isRunning('backend'), false);

  unsubscribe();
  supervisor.spawnService({ name: 'webui', command: '/node' });
  spawned.at(-1).child.emit('exit', 0, null);
  assert.equal(events.length, 1);
});

test('主动停止不会触发崩溃回调', async () => {
  const { supervisor } = createSupervisor();
  const events = [];
  supervisor.onUnexpectedExit((info) => events.push(info));
  supervisor.spawnService({ name: 'backend', command: '/python' });
  // 注入的 terminate 会在停止过程中发出 exit 事件：因为处于 stopping 状态，
  // 不应触发崩溃回调。
  const result = await supervisor.stopService('backend');
  assert.equal(result.status, 'terminated');
  assert.equal(events.length, 0);
  assert.equal(supervisor.isRunning('backend'), false);
});

test('输出行进入 tail 缓冲并截断', () => {
  const { supervisor, spawned } = createSupervisor();
  supervisor.spawnService({ name: 'backend', command: '/python' });
  const child = spawned.at(-1).child;
  child.stdout.write('第一行\n第二行\n');
  assert.deepEqual(supervisor.tailOf('backend').slice(0, 2), ['第一行', '第二行']);
  for (let index = 0; index < SERVICE_TAIL_LINES + 20; index += 1) {
    child.stderr.write(`line-${index}\n`);
  }
  assert.ok(supervisor.tailOf('backend').length <= SERVICE_TAIL_LINES);
});

test('stopAll 停止全部服务', async () => {
  const { supervisor, spawned } = createSupervisor();
  supervisor.spawnService({ name: 'backend', command: '/python' });
  supervisor.spawnService({ name: 'webui', command: '/node' });
  const results = await supervisor.stopAll({ graceMs: 10 });
  assert.equal(results.backend.status, 'terminated');
  assert.equal(results.webui.status, 'terminated');
  assert.deepEqual(supervisor.runningServiceNames(), []);
  assert.equal(spawned.length, 2);
});
