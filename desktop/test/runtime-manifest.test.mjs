import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  findMissingRuntimeFiles,
  loadRuntimeManifest,
  manifestCandidates,
  readRuntimeManifest,
  resolveManifestPath,
  resolveRuntimeManifest,
  RuntimeManifestError,
  validateRuntimeManifest,
} from '../electron/lib/runtime-manifest.mjs';

const RUNTIME_ROOT = path.join(os.tmpdir(), 'jw-desktop-test-runtime');
const MANIFEST_PATH = path.join(RUNTIME_ROOT, 'runtime-manifest.json');

function validManifest(overrides = {}) {
  return {
    schema_version: 1,
    platform: 'darwin',
    arch: 'arm64',
    python: 'python/bin/python3',
    node: 'node/bin/node',
    langgraph: { executable: 'python/bin/python3', args: ['-m', 'langgraph_cli'] },
    webui: 'webui/dist/server.js',
    projectRoot: 'project',
    pythonVersion: '3.12.7',
    nodeVersion: '22.22.3',
    jwVersion: '0.3.0',
    webuiVersion: '0.1.8',
    ...overrides,
  };
}

function validate(raw, options = {}) {
  return validateRuntimeManifest(raw, {
    platform: 'darwin',
    arch: 'arm64',
    manifestPath: MANIFEST_PATH,
    ...options,
  });
}

test('校验合法的运行时清单并解析相对路径', () => {
  const resolved = resolveRuntimeManifest(validManifest(), {
    manifestPath: MANIFEST_PATH,
    expectedPlatform: 'darwin',
    expectedArch: 'arm64',
  });

  assert.equal(resolved.runtimeRoot, RUNTIME_ROOT);
  assert.equal(resolved.pythonPath, path.join(RUNTIME_ROOT, 'python', 'bin', 'python3'));
  assert.equal(resolved.nodePath, path.join(RUNTIME_ROOT, 'node', 'bin', 'node'));
  assert.equal(
    resolved.webuiServerPath,
    path.join(RUNTIME_ROOT, 'webui', 'dist', 'server.js'),
  );
  assert.equal(resolved.projectRootPath, path.join(RUNTIME_ROOT, 'project'));
  assert.deepEqual(resolved.langgraphCommand, [
    path.join(RUNTIME_ROOT, 'python', 'bin', 'python3'),
    '-m',
    'langgraph_cli',
  ]);
  assert.equal(resolved.versions.jw, '0.3.0');
  assert.equal(resolved.versions.webui, '0.1.8');
  assert.equal(resolved.platform, 'darwin');
  assert.equal(resolved.arch, 'arm64');
  assert.ok(Object.isFrozen(resolved));
});

test('Windows 清单使用反斜杠路径也能解析', () => {
  const resolved = resolveRuntimeManifest(
    validManifest({
      platform: 'win32',
      arch: 'x64',
      python: 'python\\python.exe',
      node: 'node\\node.exe',
      langgraph: { executable: 'python\\python.exe', args: ['-m', 'langgraph_cli'] },
      webui: 'webui\\dist\\server.js',
    }),
    {
      manifestPath: MANIFEST_PATH,
      expectedPlatform: 'win32',
      expectedArch: 'x64',
    },
  );
  assert.equal(resolved.pythonPath, path.join(RUNTIME_ROOT, 'python', 'python.exe'));
  assert.equal(resolved.nodePath, path.join(RUNTIME_ROOT, 'node', 'node.exe'));
});

test('清单平台必须与宿主平台一致', () => {
  assert.throws(
    () => validate(validManifest({ platform: 'win32' })),
    (error) => {
      assert.ok(error instanceof RuntimeManifestError);
      assert.equal(error.code, 'RUNTIME_MANIFEST_PLATFORM_MISMATCH');
      assert.match(error.message, /win32/);
      return true;
    },
  );
});

test('清单架构必须与宿主架构一致', () => {
  assert.throws(
    () => validate(validManifest({ arch: 'x64' })),
    (error) => {
      assert.equal(error.code, 'RUNTIME_MANIFEST_ARCH_MISMATCH');
      return true;
    },
  );
});

test('不支持的宿主平台/架构直接失败', () => {
  assert.throws(
    () => validate(validManifest(), { platform: 'linux', arch: 'x64' }),
    (error) => error.code === 'RUNTIME_MANIFEST_HOST_UNSUPPORTED',
  );
  assert.throws(
    () => validate(validManifest(), { arch: 'armv7l' }),
    (error) => error.code === 'RUNTIME_MANIFEST_ARCH_MISMATCH',
  );
});

test('schema_version 与平台字段类型校验', () => {
  assert.throws(
    () => validate(validManifest({ schema_version: 2 })),
    (error) => error.code === 'RUNTIME_MANIFEST_SCHEMA_UNSUPPORTED',
  );
  assert.throws(
    () => validate(validManifest({ platform: 'linux' })),
    (error) => error.code === 'RUNTIME_MANIFEST_PLATFORM_UNSUPPORTED',
  );
  assert.throws(() => validate(null), /JSON 对象/);
});

test('绝对路径字段被拒绝', () => {
  for (const value of ['/usr/bin/python3', 'C:\\Python\\python.exe', '\\\\server\\share\\node.exe']) {
    assert.throws(
      () => validate(validManifest({ python: value })),
      (error) => error.code === 'RUNTIME_MANIFEST_INVALID',
      `绝对路径应被拒绝：${value}`,
    );
  }
});

test('包含 .. 的路径被拒绝，防止越出 runtime 根目录', () => {
  assert.throws(
    () => validate(validManifest({ projectRoot: '../project' })),
    (error) => {
      assert.equal(error.code, 'RUNTIME_MANIFEST_INVALID');
      return true;
    },
  );
});

test('langgraph.args 必须是非空字符串数组', () => {
  assert.throws(
    () => validate(validManifest({ langgraph: { executable: 'python/bin/python3', args: [] } })),
    (error) => error.code === 'RUNTIME_MANIFEST_INVALID',
  );
  assert.throws(
    () =>
      validate(
        validManifest({
          langgraph: { executable: 'python/bin/python3', args: ['-m', 42] },
        }),
      ),
    (error) => error.code === 'RUNTIME_MANIFEST_INVALID',
  );
});

test('版本字段缺失时降级为 null 而不是失败', () => {
  const manifest = validManifest();
  delete manifest.pythonVersion;
  delete manifest.jwVersion;
  const resolved = validate(manifest);
  assert.equal(resolved.pythonVersion, null);
  assert.equal(resolved.jwVersion, null);
});

test('JW_DESKTOP_RUNTIME_MANIFEST 覆盖优先于打包路径', () => {
  const candidates = manifestCandidates({
    env: { JW_DESKTOP_RUNTIME_MANIFEST: '/tmp/some/manifest.json' },
    resourcesPath: '/app/resources',
    isPackaged: true,
    appRoot: '/repo/desktop',
  });
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].path, path.resolve('/tmp/some/manifest.json'));
  assert.equal(candidates[0].source, 'env');
});

test('打包后候选清单位于 resources/runtime 下', () => {
  const candidates = manifestCandidates({
    env: {},
    resourcesPath: '/app/resources',
    isPackaged: true,
    appRoot: '/repo/desktop',
  });
  assert.equal(
    candidates[0].path,
    path.join('/app/resources', 'runtime', 'runtime-manifest.json'),
  );
});

test('开发态候选清单位于 packaging/dist/runtime 下', () => {
  const candidates = manifestCandidates({
    env: {},
    isPackaged: false,
    appRoot: '/repo/desktop',
  });
  assert.equal(
    candidates[0].path,
    path.resolve('/repo', 'packaging', 'dist', 'runtime', 'runtime-manifest.json'),
  );
});

test('JW_DESKTOP_RUNTIME_MANIFEST 指向不存在的文件时给出明确错误', () => {
  assert.throws(
    () =>
      resolveManifestPath({
        env: { JW_DESKTOP_RUNTIME_MANIFEST: path.join(RUNTIME_ROOT, 'missing.json') },
        isPackaged: false,
      }),
    (error) => {
      assert.equal(error.code, 'RUNTIME_MANIFEST_NOT_FOUND');
      assert.match(error.message, /JW_DESKTOP_RUNTIME_MANIFEST/);
      return true;
    },
  );
});

test('没有任何候选路径时给出可读错误', () => {
  assert.throws(
    () => resolveManifestPath({ env: {}, isPackaged: false }),
    (error) => error.code === 'RUNTIME_MANIFEST_NOT_FOUND',
  );
});

test('loadRuntimeManifest 能读取真实清单文件', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jw-desktop-manifest-'));
  const manifestPath = path.join(directory, 'runtime-manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(validManifest()), 'utf8');
  try {
    const resolved = loadRuntimeManifest({
      env: { JW_DESKTOP_RUNTIME_MANIFEST: manifestPath },
      isPackaged: false,
      expectedPlatform: 'darwin',
      expectedArch: 'arm64',
    });
    assert.equal(resolved.manifestPath, manifestPath);
    assert.equal(resolved.runtimeRoot, directory);
    assert.equal(resolved.pythonPath, path.join(directory, 'python', 'bin', 'python3'));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('非法 JSON 清单给出解析错误', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jw-desktop-bad-json-'));
  const manifestPath = path.join(directory, 'runtime-manifest.json');
  fs.writeFileSync(manifestPath, '{ not json', 'utf8');
  try {
    assert.throws(
      () => readRuntimeManifest(manifestPath),
      (error) => error.code === 'RUNTIME_MANIFEST_PARSE_ERROR',
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('findMissingRuntimeFiles 报告缺失文件', () => {
  const runtime = {
    pythonPath: '/runtime/python/bin/python3',
    nodePath: '/runtime/node/bin/node',
    webuiServerPath: '/runtime/webui/dist/server.js',
  };
  assert.deepEqual(findMissingRuntimeFiles(runtime, { exists: () => true }), []);
  const missing = findMissingRuntimeFiles(runtime, {
    exists: (target) => target !== '/runtime/node/bin/node',
  });
  assert.deepEqual(missing, [{ field: 'node', path: '/runtime/node/bin/node' }]);
});
