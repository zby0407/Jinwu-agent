import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  SECRET_ENV_PATTERN,
  buildBackendEnvironment,
  buildWebuiEnvironment,
  describeEnvironmentNames,
  isSecretEnvName,
  serializeLanggraphCommand,
  stripSecrets,
} from '../electron/lib/environment.mjs';

const RUNTIME_ROOT = path.join(os.tmpdir(), 'jw-desktop-env-runtime');

const RUNTIME = {
  runtimeRoot: RUNTIME_ROOT,
  projectRootPath: path.join(RUNTIME_ROOT, 'project'),
  pythonPath: path.join(RUNTIME_ROOT, 'python', 'bin', 'python3'),
  nodePath: path.join(RUNTIME_ROOT, 'node', 'bin', 'node'),
  webuiServerPath: path.join(RUNTIME_ROOT, 'webui', 'dist', 'server.js'),
  langgraphExecutablePath: path.join(RUNTIME_ROOT, 'python', 'bin', 'python3'),
  langgraphArgs: ['-m', 'langgraph_cli'],
  langgraphCommand: [
    path.join(RUNTIME_ROOT, 'python', 'bin', 'python3'),
    '-m',
    'langgraph_cli',
  ],
};

const INHERITED_ENV = {
  PATH: '/usr/bin:/bin',
  HOME: '/Users/tester',
  OPENAI_API_KEY: 'sk-do-not-leak',
  ANTHROPIC_AUTH_TOKEN: 'token-do-not-leak',
  DB_PASSWORD: 'password-do-not-leak',
  SOME_SECRET: 'secret-do-not-leak',
  CUSTOM_KEY: 'custom-key-do-not-leak',
  MONKEY: 'not-a-secret',
  LANG: 'zh_CN.UTF-8',
};

const BASE_OPTIONS = {
  runtime: RUNTIME,
  workspaceDir: path.join('/Users/tester', 'Documents', 'Jinwu'),
  userDataDir: path.join('/Users/tester', 'Library', 'Application Support', 'Jinwu'),
  backendPort: 6174,
  inheritedEnv: INHERITED_ENV,
};

test('密钥变量名匹配规则（大小写不敏感）', () => {
  for (const name of [
    'OPENAI_API_KEY',
    'openai_api_key',
    'ANTHROPIC_AUTH_TOKEN',
    'SOME_SECRET',
    'DB_PASSWORD',
    'CUSTOM_KEY',
    'HF_TOKEN',
  ]) {
    assert.equal(isSecretEnvName(name), true, `${name} 应被视为密钥`);
  }
  for (const name of ['PATH', 'HOME', 'MONKEY', 'LANG', 'JW_DATA_DIR']) {
    assert.equal(isSecretEnvName(name), false, `${name} 不应被视为密钥`);
  }
  assert.equal(SECRET_ENV_PATTERN.test('api_key'), true);
});

test('stripSecrets 返回副本且删除密钥变量', () => {
  const stripped = stripSecrets(INHERITED_ENV);
  assert.equal(stripped.OPENAI_API_KEY, undefined);
  assert.equal(stripped.ANTHROPIC_AUTH_TOKEN, undefined);
  assert.equal(stripped.DB_PASSWORD, undefined);
  assert.equal(stripped.SOME_SECRET, undefined);
  assert.equal(stripped.CUSTOM_KEY, undefined);
  assert.equal(stripped.PATH, INHERITED_ENV.PATH);
  assert.equal(stripped.MONKEY, 'not-a-secret');
  // 不修改入参
  assert.equal(INHERITED_ENV.OPENAI_API_KEY, 'sk-do-not-leak');
});

test('后端环境变量完整且保留模型密钥', () => {
  const env = buildBackendEnvironment(BASE_OPTIONS);
  assert.equal(env.JW_PROJECT_ROOT, RUNTIME.projectRootPath);
  assert.equal(env.JW_RUNTIME_ROOT, RUNTIME.runtimeRoot);
  assert.equal(env.JW_WEBUI_DIST, RUNTIME.webuiServerPath);
  assert.equal(env.JW_NODE_BIN, RUNTIME.nodePath);
  assert.equal(env.JW_DATA_DIR, path.join(BASE_OPTIONS.userDataDir, 'data'));
  assert.equal(env.XDG_CONFIG_HOME, path.join(BASE_OPTIONS.userDataDir, 'config'));
  assert.equal(env.JW_WORKSPACE_DIR, BASE_OPTIONS.workspaceDir);
  assert.equal(env.JW_LANGGRAPH_DEV_PORT, '6174');
  assert.equal(env.JW_LANGGRAPH_COMMAND_JSON, JSON.stringify(RUNTIME.langgraphCommand));
  // Windows 管道默认 ANSI 代码页，必须强制 UTF-8，否则中文/emoji 输出会崩溃。
  assert.equal(env.PYTHONUTF8, '1');
  assert.equal(env.PYTHONIOENCODING, 'utf-8');
  assert.equal(env.PYTHONDONTWRITEBYTECODE, '1');
  // 后端保留配置密钥（由 Python 端自行读取）
  assert.equal(env.OPENAI_API_KEY, 'sk-do-not-leak');
  assert.equal(env.PATH, INHERITED_ENV.PATH);
});

test('前端环境变量剥离密钥并注入 PORT/HOSTNAME/NODE_ENV', () => {
  const env = buildWebuiEnvironment({ ...BASE_OPTIONS, webuiPort: 4716 });
  assert.equal(env.PORT, '4716');
  assert.equal(env.HOSTNAME, '127.0.0.1');
  assert.equal(env.NODE_ENV, 'production');
  assert.equal(env.JW_LANGGRAPH_DEV_PORT, '6174');
  assert.equal(env.JW_WORKSPACE_DIR, BASE_OPTIONS.workspaceDir);
  assert.equal(env.XDG_CONFIG_HOME, path.join(BASE_OPTIONS.userDataDir, 'config'));
  for (const name of Object.keys(env)) {
    assert.equal(isSecretEnvName(name), false, `前端环境变量不应包含密钥：${name}`);
  }
  assert.equal(env.OPENAI_API_KEY, undefined);
});

test('前端提供 WebUI 侧 agent_* 兼容别名', () => {
  const env = buildWebuiEnvironment({ ...BASE_OPTIONS, webuiPort: 4716 });
  assert.equal(env.agent_LANGGRAPH_DEV_PORT, '6174');
  assert.equal(env.agent_DATA_DIR, path.join(BASE_OPTIONS.userDataDir, 'data'));
  assert.equal(env.agent_WORKSPACE_DIR, BASE_OPTIONS.workspaceDir);
});

test('langgraph command 序列化格式符合 Python 端契约', () => {
  const serialized = serializeLanggraphCommand('/runtime/python/bin/python3', [
    '-m',
    'langgraph_cli',
  ]);
  assert.equal(serialized, JSON.stringify(['/runtime/python/bin/python3', '-m', 'langgraph_cli']));
  assert.deepEqual(JSON.parse(serialized), ['/runtime/python/bin/python3', '-m', 'langgraph_cli']);
});

test('langgraph command 序列化拒绝非法输入', () => {
  assert.throws(() => serializeLanggraphCommand('', []), /非空/);
  assert.throws(() => serializeLanggraphCommand('/usr/bin/python3', 'not-array'), /字符串数组/);
  assert.throws(() => serializeLanggraphCommand('/usr/bin/python3', ['']), /非空字符串数组/);
});

test('环境变量构造在关键字段缺失时快速失败', () => {
  assert.throws(() => buildBackendEnvironment({ ...BASE_OPTIONS, backendPort: 0 }), /后端端口/);
  assert.throws(() => buildBackendEnvironment({ ...BASE_OPTIONS, runtime: null }), /运行时清单/);
  assert.throws(
    () =>
      buildBackendEnvironment({
        ...BASE_OPTIONS,
        runtime: { ...RUNTIME, pythonPath: '' },
      }),
    /pythonPath/,
  );
  assert.throws(
    () => buildBackendEnvironment({ ...BASE_OPTIONS, workspaceDir: '' }),
    /工作区目录/,
  );
});

test('describeEnvironmentNames 只返回变量名', () => {
  const names = describeEnvironmentNames({ B_VAR: '2', A_VAR: '1' });
  assert.deepEqual(names, ['A_VAR', 'B_VAR']);
});
