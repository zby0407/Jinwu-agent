import assert from 'node:assert/strict';
import test from 'node:test';

import {
  HealthCheckError,
  backendHealthUrl,
  probeHttp,
  waitForHttp,
  webuiHealthUrl,
} from '../electron/lib/health.mjs';

test('健康检查地址只监听 127.0.0.1', () => {
  assert.equal(backendHealthUrl(6174), 'http://127.0.0.1:6174/ok');
  assert.equal(webuiHealthUrl(4716), 'http://127.0.0.1:4716/');
});

test('probeHttp 区分成功与非 2xx', async () => {
  assert.deepEqual(
    await probeHttp('http://127.0.0.1:1/', {
      fetchImpl: async () => ({ ok: true, status: 200 }),
    }),
    { ok: true, status: 200 },
  );
  assert.deepEqual(
    await probeHttp('http://127.0.0.1:1/', {
      fetchImpl: async () => ({ ok: false, status: 503 }),
    }),
    { ok: false, status: 503 },
  );
});

test('probeHttp 捕获网络异常', async () => {
  const result = await probeHttp('http://127.0.0.1:1/', {
    fetchImpl: async () => {
      throw new Error('ECONNREFUSED');
    },
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /ECONNREFUSED/);
});

test('waitForHttp 轮询直到就绪并统计尝试次数', async () => {
  let calls = 0;
  const result = await waitForHttp({
    url: 'http://127.0.0.1:6174/ok',
    timeoutMs: 1000,
    intervalMs: 5,
    probe: async () => {
      calls += 1;
      return calls < 3 ? { ok: false, status: 0 } : { ok: true, status: 200 };
    },
  });
  assert.equal(result.attempts, 3);
  assert.equal(result.status, 200);
});

test('waitForHttp 超时抛出可读错误', async () => {
  await assert.rejects(
    () =>
      waitForHttp({
        url: 'http://127.0.0.1:6174/ok',
        timeoutMs: 40,
        intervalMs: 5,
        label: '后端服务',
        probe: async () => ({ ok: false, status: 0, error: 'ECONNREFUSED' }),
      }),
    (error) => {
      assert.ok(error instanceof HealthCheckError);
      assert.equal(error.code, 'HEALTH_TIMEOUT');
      assert.match(error.message, /后端服务/);
      return true;
    },
  );
});

test('子进程提前退出时立即失败', async () => {
  await assert.rejects(
    () =>
      waitForHttp({
        url: 'http://127.0.0.1:4716/',
        timeoutMs: 5000,
        intervalMs: 5,
        isAlive: () => false,
        probe: async () => ({ ok: false, status: 0 }),
      }),
    (error) => error.code === 'HEALTH_PROCESS_EXITED',
  );
});

test('取消信号会中断等待（兄弟服务失败时使用）', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    () =>
      waitForHttp({
        url: 'http://127.0.0.1:4716/',
        timeoutMs: 5000,
        signal: controller.signal,
        probe: async () => ({ ok: false, status: 0 }),
      }),
    (error) => error.code === 'HEALTH_ABORTED',
  );
});

test('缺少 fetch 实现时返回可读错误而不是崩溃', async () => {
  const result = await probeHttp('http://127.0.0.1:1/', { fetchImpl: undefined });
  assert.equal(result.ok, false);
  assert.match(result.error, /fetch/);
});
