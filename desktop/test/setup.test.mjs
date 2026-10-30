import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';

import {
  normalizeSetupPayload,
  parseSetupResponse,
  saveSetupConfiguration,
  setupCommandArgs,
  testSetupConnection,
  SETUP_TEST_TIMEOUT_MS,
} from '../electron/lib/setup.mjs';

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
    this.stdin = new EventEmitter();
    this.stdin.data = '';
    this.stdin.end = (value) => {
      this.stdin.data += String(value ?? '');
      this.stdin.emit('finish');
    };
    this.killed = false;
  }

  kill() {
    this.killed = true;
    this.emit('close', -1);
  }
}

function fakeSpawn(response, { exitCode = 0, stderr = '' } = {}) {
  const calls = [];
  const spawnImpl = (command, args, options) => {
    const child = new FakeChild();
    calls.push({ command, args, options, child });
    process.nextTick(() => {
      if (stderr) child.stderr.emit('data', stderr);
      child.stdout.emit('data', typeof response === 'string' ? response : JSON.stringify(response));
      child.emit('close', exitCode);
    });
    return child;
  };
  return { calls, spawnImpl };
}

test('normalizeSetupPayload trims fields and drops unknown keys', () => {
  const payload = normalizeSetupPayload({
    provider: ' dashscope ',
    model: ' qwen3.7-plus ',
    api_key: ' sk-test ',
    base_url: '',
    ignored: 'do-not-forward',
  });

  assert.deepEqual(payload, {
    provider: 'dashscope',
    model: 'qwen3.7-plus',
    api_key: 'sk-test',
    base_url: '',
  });
});

test('normalizeSetupPayload rejects non-object and non-string fields', () => {
  assert.throws(() => normalizeSetupPayload(null), /配置内容/);
  assert.throws(
    () => normalizeSetupPayload({ provider: 'dashscope', api_key: 123 }),
    /必须是字符串/,
  );
});

test('parseSetupResponse accepts the secret-free success response', () => {
  const result = parseSetupResponse(
    JSON.stringify({ ok: true, configured: true, provider: 'dashscope' }),
    0,
  );

  assert.equal(result.ok, true);
  assert.equal(result.provider, 'dashscope');
});

test('parseSetupResponse surfaces a Python validation error', () => {
  assert.throws(
    () => parseSetupResponse(JSON.stringify({ ok: false, error: 'API Key 必填' }), 2),
    /API Key 必填/,
  );
});

test('parseSetupResponse rejects empty and malformed output', () => {
  assert.throws(() => parseSetupResponse('', 0), /没有返回结果/);
  assert.throws(() => parseSetupResponse('not json', 0), /无法解析/);
});

test('setupCommandArgs only permits known modes', () => {
  assert.deepEqual(setupCommandArgs('status'), ['-m', 'jw.desktop_setup', 'status']);
  assert.deepEqual(setupCommandArgs('configure'), ['-m', 'jw.desktop_setup', 'configure']);
  assert.throws(() => setupCommandArgs('shell'), /未知/);
  assert.deepEqual(setupCommandArgs('test'), ['-m', 'jw.desktop_setup', 'test']);
  assert.equal(SETUP_TEST_TIMEOUT_MS, 30_000);
});

test('connection test cancellation kills its child and never saves a draft', async () => {
  const controller = new AbortController();
  const child = new FakeChild();
  const calls = [];
  const result = testSetupConnection({ pythonPath: 'python', cwd: '.', env: {},
    payload: { provider: 'dashscope', api_key: 'synthetic-key', proxy_mode: 'direct' },
    signal: controller.signal, spawnImpl: (_cmd, args) => { calls.push(args); return child; } });
  controller.abort();
  await assert.rejects(result, (error) => error.code === 'SETUP_CANCELLED');
  assert.equal(child.killed, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].at(-1), 'test');
  assert.equal(JSON.parse(child.stdin.data).proxy_mode, 'direct');
});

test('an already cancelled test sends no credential payload', async () => {
  const controller = new AbortController();
  controller.abort();
  const child = new FakeChild();
  await assert.rejects(testSetupConnection({ pythonPath: 'python', cwd: '.', env: {},
    payload: { provider: 'dashscope', api_key: 'synthetic-key' }, signal: controller.signal,
    spawnImpl: () => child }), (error) => error.code === 'SETUP_CANCELLED');
  assert.equal(child.stdin.data, '');
  assert.equal(child.killed, true);
});

test('saveSetupConfiguration sends the API key on stdin, never argv', async () => {
  const { calls, spawnImpl } = fakeSpawn({
    ok: true,
    configured: true,
    provider: 'openai',
    model: 'gpt-5.6-terra',
    configPath: '/tmp/config.yaml',
  });

  const result = await saveSetupConfiguration({
    pythonPath: '/runtime/python/bin/python3',
    cwd: '/runtime/project',
    env: { XDG_CONFIG_HOME: '/tmp/config' },
    payload: {
      provider: 'openai',
      model: 'gpt-5.6-terra',
      api_key: 'sk-super-secret',
      base_url: '',
    },
    spawnImpl,
  });

  assert.equal(result.configured, true);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].args, ['-m', 'jw.desktop_setup', 'configure']);
  assert.ok(!calls[0].args.join(' ').includes('sk-super-secret'));
  assert.match(calls[0].child.stdin.data, /sk-super-secret/);
  assert.equal(calls[0].options.windowsHide, true);
  assert.deepEqual(calls[0].options.stdio, ['pipe', 'pipe', 'pipe']);
});

test('saveSetupConfiguration rejects secret-bearing errors from stderr only as detail', async () => {
  const { spawnImpl } = fakeSpawn('', { exitCode: 1, stderr: 'unexpected failure' });

  await assert.rejects(
    saveSetupConfiguration({
      pythonPath: '/runtime/python/bin/python3',
      cwd: '/runtime/project',
      env: {},
      payload: { provider: 'dashscope', api_key: 'sk-test' },
      spawnImpl,
    }),
    (error) => {
      assert.equal(error.detail, 'unexpected failure');
      assert.ok(!error.message.includes('sk-test'));
      return true;
    },
  );
});
