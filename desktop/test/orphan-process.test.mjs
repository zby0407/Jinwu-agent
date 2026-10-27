import assert from 'node:assert/strict';
import test from 'node:test';

import {
  commandLineMatchesLanggraph,
  findListeningPids,
  isBundledPythonExecutable,
  parseListeningPids,
  parseLsofExecutablePath,
  readProcessCommandLine,
  readProcessExecutablePath,
  sweepOrphanedBackend,
  waitForPortRelease,
} from '../electron/lib/orphan-process.mjs';

const LANGGRAPH_COMMAND =
  '/runtime/python/bin/python3 -m langgraph_cli dev --config /runtime/jw/langgraph_dev/langgraph.json --port 6174 --n-jobs-per-worker 10 --allow-blocking --no-browser --no-reload';

test('解析 macOS lsof 输出', () => {
  assert.deepEqual(
    parseListeningPids('42540\n42541\n\n', { platform: 'darwin', port: 6174 }),
    [42540, 42541],
  );
  assert.deepEqual(parseListeningPids('', { platform: 'darwin', port: 6174 }), []);
});

test('解析 Windows netstat 输出（仅匹配本端口 LISTENING）', () => {
  const output = [
    '  TCP    127.0.0.1:6174    0.0.0.0:0    LISTENING    42540',
    '  TCP    127.0.0.1:4716    0.0.0.0:0    LISTENING    42537',
    '  TCP    127.0.0.1:6174    127.0.0.1:50000    ESTABLISHED    42540',
  ].join('\r\n');
  assert.deepEqual(parseListeningPids(output, { platform: 'win32', port: 6174 }), [42540]);
});

test('命令行必须同时匹配 langgraph 与本次端口', () => {
  assert.equal(commandLineMatchesLanggraph(LANGGRAPH_COMMAND, 6174), true);
  assert.equal(commandLineMatchesLanggraph(LANGGRAPH_COMMAND, 6175), false);
  assert.equal(
    commandLineMatchesLanggraph('/usr/bin/node /runtime/webui/dist/server.js', 6174),
    false,
  );
  assert.equal(commandLineMatchesLanggraph('', 6174), false);
  // --port=6174 形式
  assert.equal(
    commandLineMatchesLanggraph('python -m langgraph_cli dev --port=6174', 6174),
    true,
  );
});

test('waitForPortRelease 在后端端口释放后返回 true', async () => {
  let calls = 0;
  const released = await waitForPortRelease({
    port: 6174,
    timeoutMs: 1000,
    intervalMs: 5,
    probe: async () => {
      calls += 1;
      return calls < 2 ? { ok: true, status: 200 } : { ok: false, status: 0 };
    },
    sleep: async () => {},
  });
  assert.equal(released, true);
});

test('waitForPortRelease 超时返回 false', async () => {
  const released = await waitForPortRelease({
    port: 6174,
    timeoutMs: 20,
    intervalMs: 5,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
  });
  assert.equal(released, false);
});

test('端口已释放时不执行清理', async () => {
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: false, status: 0 }),
    sleep: async () => {},
    listPids: async () => {
      throw new Error('不应调用');
    },
  });
  assert.equal(result.status, 'released');
  assert.equal(result.swept, 0);
});

test('端口仍响应 /ok 且命令行匹配时清理残留进程树', async () => {
  const terminated = [];
  const logs = [];
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
    waitForRelease: async () => false,
    listPids: async () => [42540],
    readCommandLine: async () => LANGGRAPH_COMMAND,
    isAlive: () => true,
    terminate: async ({ pid, graceMs }) => {
      terminated.push({ pid, graceMs });
      return { status: 'terminated', pid, mode: 'process-group' };
    },
    logger: { warn: (m) => logs.push(m), info: (m) => logs.push(m) },
  });
  assert.deepEqual(terminated, [{ pid: 42540, graceMs: 3000 }]);
  assert.equal(result.status, 'swept');
  assert.deepEqual(result.orphans, [42540]);
  assert.ok(logs.some((line) => line.includes('42540')));
});

test('命令行不匹配时不误杀（例如用户其他 jw 实例）', async () => {
  let terminated = false;
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
    listPids: async () => [9999],
    readCommandLine: async () => 'python -m langgraph_cli dev --port 6999',
    isAlive: () => true,
    terminate: async () => {
      terminated = true;
      return { status: 'terminated', pid: 9999, mode: 'process-group' };
    },
    logger: { warn: () => {}, info: () => {} },
  });
  assert.equal(terminated, false);
  assert.equal(result.status, 'skipped');
});

test('找不到监听 pid 时跳过并告警', async () => {
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
    listPids: async () => [],
    logger: { warn: () => {}, info: () => {} },
  });
  assert.equal(result.status, 'no-pid');
});

test('非法端口直接跳过', async () => {
  const result = await sweepOrphanedBackend({ port: 0 });
  assert.equal(result.status, 'invalid-port');
});

test('findListeningPids 与 readProcessCommandLine 在命令失败时返回安全空值', async () => {
  const failingExec = (_command, _args, _options, callback) => {
    callback(new Error('not found'), '', '');
  };
  const macPids = await findListeningPids({ port: 6174, platform: 'darwin', execFileImpl: failingExec });
  assert.deepEqual(macPids, []);
  const winPids = await findListeningPids({ port: 6174, platform: 'win32', execFileImpl: failingExec });
  assert.deepEqual(winPids, []);
  assert.equal(
    await readProcessCommandLine({ pid: 1, platform: 'darwin', execFileImpl: failingExec }),
    '',
  );
  assert.equal(
    await readProcessCommandLine({ pid: 1, platform: 'win32', execFileImpl: failingExec }),
    '',
  );
});

test('解析 lsof 可执行文件路径', () => {
  const output = [
    'COMMAND     PID   USER   FD     TYPE             DEVICE SIZE/OFF                NODE NAME',
    'python3.1 42540 zhuanz  cwd      DIR               1,17       96           125172566 /workspace',
    'python3.1 42540 zhuanz  txt      REG               1,17    49968           125117483 /runtime/python/bin/python3.12',
    'python3.1 42540 zhuanz  txt      REG               1,17  4129840 1152921500312609216 /usr/lib/dyld',
  ].join('\n');
  assert.equal(parseLsofExecutablePath(output), '/runtime/python/bin/python3.12');
  assert.equal(parseLsofExecutablePath(''), '');
});

test('随包 Python 可执行文件判定', () => {
  assert.equal(
    isBundledPythonExecutable('/runtime/python/bin/python3.12', '/runtime/python/bin/python3'),
    true,
  );
  assert.equal(
    isBundledPythonExecutable('/runtime/python/bin/python3', '/runtime/python/bin/python3'),
    true,
  );
  assert.equal(
    isBundledPythonExecutable('/usr/bin/python3', '/runtime/python/bin/python3'),
    false,
  );
  assert.equal(isBundledPythonExecutable('', '/runtime/python/bin/python3'), false);
  assert.equal(isBundledPythonExecutable('/runtime/python/bin/python3', ''), false);
});

test('lsof 可执行文件匹配时无需 ps 命令行即可清理', async () => {
  const terminated = [];
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
    waitForRelease: async () => false,
    listPids: async () => [42540],
    readExecutablePath: async () => '/runtime/python/bin/python3.12',
    readCommandLine: async () => '', // ps 不可用（或输出为空）
    pythonPath: '/runtime/python/bin/python3',
    isAlive: () => true,
    terminate: async ({ pid }) => {
      terminated.push(pid);
      return { status: 'terminated', pid, mode: 'process-group' };
    },
    logger: { warn: () => {}, info: () => {} },
  });
  assert.deepEqual(terminated, [42540]);
  assert.equal(result.status, 'swept');
});

test('可执行文件与命令行都不匹配时绝不误杀', async () => {
  let terminated = false;
  const result = await sweepOrphanedBackend({
    port: 6174,
    probe: async () => ({ ok: true, status: 200 }),
    sleep: async () => {},
    waitForRelease: async () => false,
    listPids: async () => [9999],
    readExecutablePath: async () => '/usr/bin/python3',
    readCommandLine: async () => 'python -m langgraph_cli dev --port 6999',
    pythonPath: '/runtime/python/bin/python3',
    isAlive: () => true,
    terminate: async () => {
      terminated = true;
      return { status: 'terminated', pid: 9999, mode: 'process-group' };
    },
    logger: { warn: () => {}, info: () => {} },
  });
  assert.equal(terminated, false);
  assert.equal(result.status, 'skipped');
});

test('readProcessExecutablePath 在 Windows 上返回空字符串', async () => {
  assert.equal(await readProcessExecutablePath({ pid: 1, platform: 'win32' }), '');
});
