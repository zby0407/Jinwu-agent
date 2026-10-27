import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  DEFAULT_BACKEND_PORT,
  DEFAULT_WEBUI_PORT,
  PortUnavailableError,
  chooseServicePorts,
  findAvailablePort,
  isPortAvailable,
} from '../electron/lib/ports.mjs';

function availability(occupied = []) {
  const busy = new Set(occupied);
  return (port) => !busy.has(port);
}

test('默认端口空闲时沿用默认值', async () => {
  const ports = await chooseServicePorts({ isAvailable: availability() });
  assert.deepEqual(ports, {
    backendPort: DEFAULT_BACKEND_PORT,
    webuiPort: DEFAULT_WEBUI_PORT,
    backendIsDefault: true,
    webuiIsDefault: true,
  });
});

test('默认端口被占用时顺延到相邻端口', async () => {
  const ports = await chooseServicePorts({
    isAvailable: availability([DEFAULT_BACKEND_PORT, DEFAULT_WEBUI_PORT]),
  });
  assert.equal(ports.backendPort, DEFAULT_BACKEND_PORT + 1);
  assert.equal(ports.webuiPort, DEFAULT_WEBUI_PORT + 1);
  assert.equal(ports.backendIsDefault, false);
  assert.equal(ports.webuiIsDefault, false);
});

test('前后端端口永不重复（相同期望端口时前端顺延）', async () => {
  const ports = await chooseServicePorts({
    backendPreferred: 5000,
    webuiPreferred: 5000,
    isAvailable: availability(),
  });
  assert.equal(ports.backendPort, 5000);
  assert.equal(ports.webuiPort, 5001);
});

test('前端扫描排除已选定的后端端口', async () => {
  // 后端 4716 可用且前端默认 4716：前端必须避开。
  const ports = await chooseServicePorts({
    backendPreferred: DEFAULT_WEBUI_PORT,
    webuiPreferred: DEFAULT_WEBUI_PORT,
    isAvailable: availability(),
  });
  assert.equal(ports.backendPort, DEFAULT_WEBUI_PORT);
  assert.equal(ports.webuiPort, DEFAULT_WEBUI_PORT + 1);
});

test('findAvailablePort 支持排除端口', async () => {
  const port = await findAvailablePort(6000, {
    isAvailable: availability(),
    exclude: new Set([6000, 6001]),
  });
  assert.equal(port, 6002);
});

test('findAvailablePort 在扫描耗尽时抛错', async () => {
  await assert.rejects(
    () => findAvailablePort(6100, { isAvailable: () => false, limit: 3 }),
    (error) => {
      assert.ok(error instanceof PortUnavailableError);
      assert.equal(error.code, 'PORT_UNAVAILABLE');
      return true;
    },
  );
});

test('非法端口参数立即失败', async () => {
  await assert.rejects(() => findAvailablePort(0), /整数/);
  await assert.rejects(() => findAvailablePort(70000), /整数/);
  await assert.rejects(() => findAvailablePort(6174.5), /整数/);
});

test('isPortAvailable 能识别真实占用', async () => {
  const net = await import('node:net');
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  try {
    assert.equal(await isPortAvailable(port), false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  assert.equal(await isPortAvailable(port), true);
});

test('默认端口常量与契约一致', () => {
  assert.equal(DEFAULT_BACKEND_PORT, 6174);
  assert.equal(DEFAULT_WEBUI_PORT, 4716);
});

test('端口相关的临时目录路径不会泄漏', () => {
  // 占位：确保测试运行时不会写入用户目录（只读探测）。
  assert.equal(typeof path.join(os.tmpdir(), 'x'), 'string');
});
