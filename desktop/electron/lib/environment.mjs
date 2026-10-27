/**
 * 子进程环境变量构造。
 *
 * 桌面外壳是唯一进程监管者，必须显式给随包 Python 与随包 Node 注入运行时路径，
 * 让它们不依赖任何系统安装（不调用系统 python/node/npm/uv）。
 *
 * 共享变量（前后端都会收到）：
 *   JW_PROJECT_ROOT=<runtime>/project
 *   JW_RUNTIME_ROOT=<runtime>
 *   JW_WEBUI_DIST=<runtime>/webui/dist/server.js
 *   JW_NODE_BIN=<runtime>/node...
 *   JW_LANGGRAPH_COMMAND_JSON=JSON.stringify([langgraph.executable, ...args])
 *   JW_DATA_DIR=<userData>/data
 *   XDG_CONFIG_HOME=<userData>/config
 *   JW_WORKSPACE_DIR=<Documents/Jinwu>
 *   JW_LANGGRAPH_DEV_PORT=<backendPort>
 *
 * 前端额外：PORT=<webuiPort>、HOSTNAME=127.0.0.1、NODE_ENV=production，
 * 并且剥离模型密钥（*API_KEY* / *_KEY / *TOKEN / *SECRET / *PASSWORD）。
 * 后端保留父进程中的配置密钥；密钥值永远不写入日志。
 */

import path from 'node:path';

import { resolveConfigDirectory, resolveDataDirectory } from './paths.mjs';

/**
 * 密钥变量名匹配（大小写不敏感）：
 * - 含 API_KEY（如 OPENAI_API_KEY）
 * - 以 _KEY 结尾（如 ANTHROPIC_KEY）
 * - 含 TOKEN / SECRET / PASSWORD
 */
export const SECRET_ENV_PATTERN = /(?:API_KEY)|(?:_KEY$)|(?:TOKEN)|(?:SECRET)|(?:PASSWORD)/i;

/**
 * 兼容别名：当前 WebUI（`webui/src/lib/server/*`、`app/api/jw-config`）读取的是
 * `agent_*` 而不是 `JW_*`。为了让桌面外壳在 WebUI 修正前即可正确定位数据目录与
 * 后端端口，这里为前端额外补一组别名（不覆盖已存在的同名变量）。
 * 这是 WebUI 侧的遗留命名，Lead 可在 WebUI 修正后删除。
 */
export const WEBUI_COMPAT_ENV_ALIASES = Object.freeze({
  agent_LANGGRAPH_DEV_PORT: 'JW_LANGGRAPH_DEV_PORT',
  agent_DATA_DIR: 'JW_DATA_DIR',
  agent_WORKSPACE_DIR: 'JW_WORKSPACE_DIR',
});

export class EnvironmentBuildError extends Error {
  constructor(message, { code = 'ENVIRONMENT_BUILD_FAILED' } = {}) {
    super(message);
    this.name = 'EnvironmentBuildError';
    this.code = code;
  }
}

/** 是否为需要剥离的密钥变量名。 */
export function isSecretEnvName(name) {
  return typeof name === 'string' && SECRET_ENV_PATTERN.test(name);
}

/**
 * 返回剥离密钥后的环境变量副本（不修改入参）。
 * @param {NodeJS.ProcessEnv} env
 */
export function stripSecrets(env = process.env) {
  const result = {};
  for (const [name, value] of Object.entries(env ?? {})) {
    if (!isSecretEnvName(name) && value !== undefined) {
      result[name] = value;
    }
  }
  return result;
}

/**
 * 序列化 LangGraph CLI 调用方式，供 Python 端 `JW_LANGGRAPH_COMMAND_JSON` 消费。
 * 例如：`JSON.stringify(["/app/resources/runtime/python/bin/python3", "-m", "langgraph_cli"])`
 *
 * @param {string} executable 绝对路径
 * @param {string[]} args
 */
export function serializeLanggraphCommand(executable, args = []) {
  if (typeof executable !== 'string' || executable.trim().length === 0) {
    throw new EnvironmentBuildError('JW_LANGGRAPH_COMMAND_JSON 需要非空的 langgraph 可执行文件路径', {
      code: 'ENVIRONMENT_LANGGRAPH_COMMAND_INVALID',
    });
  }
  if (!Array.isArray(args)) {
    throw new EnvironmentBuildError('JW_LANGGRAPH_COMMAND_JSON 的 args 必须是字符串数组', {
      code: 'ENVIRONMENT_LANGGRAPH_COMMAND_INVALID',
    });
  }
  const command = [executable.trim(), ...args];
  for (const part of command) {
    if (typeof part !== 'string' || part.trim().length === 0) {
      throw new EnvironmentBuildError(
        'JW_LANGGRAPH_COMMAND_JSON 必须是非空字符串数组',
        { code: 'ENVIRONMENT_LANGGRAPH_COMMAND_INVALID' },
      );
    }
  }
  return JSON.stringify(command);
}

/**
 * 后端环境变量：保留父进程中的模型密钥。
 *
 * @param {object} options
 * @param {ReturnType<import('./runtime-manifest.mjs').resolveRuntimeManifest>} options.runtime
 * @param {string} options.workspaceDir
 * @param {string} options.userDataDir
 * @param {number} options.backendPort
 * @param {NodeJS.ProcessEnv} [options.inheritedEnv]
 * @param {NodeJS.ProcessEnv} [options.extraEnv]
 */
export function buildBackendEnvironment({
  runtime,
  workspaceDir,
  userDataDir,
  backendPort,
  inheritedEnv = process.env,
  extraEnv = {},
}) {
  return {
    ...buildSharedEnvironment({ runtime, workspaceDir, userDataDir, backendPort, inheritedEnv }),
    ...extraEnv,
  };
}

/**
 * 前端环境变量：必须剥离模型密钥；额外注入 PORT / HOSTNAME / NODE_ENV。
 *
 * @param {object} options 同 buildBackendEnvironment + webuiPort
 */
export function buildWebuiEnvironment({
  runtime,
  workspaceDir,
  userDataDir,
  backendPort,
  webuiPort,
  inheritedEnv = process.env,
  extraEnv = {},
}) {
  const env = {
    ...buildSharedEnvironment({
      runtime,
      workspaceDir,
      userDataDir,
      backendPort,
      inheritedEnv,
      stripSecretEnv: true,
    }),
    PORT: String(webuiPort),
    HOSTNAME: '127.0.0.1',
    NODE_ENV: 'production',
    ...extraEnv,
  };
  for (const [alias, source] of Object.entries(WEBUI_COMPAT_ENV_ALIASES)) {
    if (env[alias] === undefined && env[source] !== undefined) {
      env[alias] = env[source];
    }
  }
  return env;
}

/**
 * 列出环境变量名（不包含值），用于日志与排查，避免密钥落盘。
 * @param {NodeJS.ProcessEnv} env
 */
export function describeEnvironmentNames(env = process.env) {
  return Object.keys(env ?? {}).sort();
}

function buildSharedEnvironment({
  runtime,
  workspaceDir,
  userDataDir,
  backendPort,
  inheritedEnv,
  stripSecretEnv = false,
}) {
  if (!runtime || typeof runtime !== 'object') {
    throw new EnvironmentBuildError('缺少已解析的运行时清单（runtime）');
  }
  if (typeof workspaceDir !== 'string' || workspaceDir.trim().length === 0) {
    throw new EnvironmentBuildError('缺少工作区目录（workspaceDir）');
  }
  if (typeof userDataDir !== 'string' || userDataDir.trim().length === 0) {
    throw new EnvironmentBuildError('缺少用户数据目录（userDataDir）');
  }
  if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65535) {
    throw new EnvironmentBuildError(`后端端口非法：${backendPort}`);
  }
  for (const field of ['pythonPath', 'nodePath', 'webuiServerPath', 'projectRootPath', 'runtimeRoot']) {
    if (typeof runtime[field] !== 'string' || runtime[field].length === 0) {
      throw new EnvironmentBuildError(`运行时清单缺少已解析字段：${field}`);
    }
  }

  const base = stripSecretEnv ? stripSecrets(inheritedEnv) : { ...(inheritedEnv ?? {}) };
  return {
    ...base,
    JW_PROJECT_ROOT: runtime.projectRootPath,
    JW_RUNTIME_ROOT: runtime.runtimeRoot,
    JW_WEBUI_DIST: runtime.webuiServerPath,
    JW_NODE_BIN: runtime.nodePath,
    JW_DESKTOP_MANAGED: '1',
    JW_LANGGRAPH_COMMAND_JSON: serializeLanggraphCommand(
      runtime.langgraphCommand?.[0] ?? runtime.langgraphExecutablePath,
      runtime.langgraphCommand?.slice(1) ?? runtime.langgraphArgs ?? [],
    ),
    JW_DATA_DIR: resolveDataDirectory(userDataDir),
    XDG_CONFIG_HOME: resolveConfigDirectory(userDataDir),
    JW_WORKSPACE_DIR: workspaceDir,
    JW_LANGGRAPH_DEV_PORT: String(backendPort),
  };
}

/** Windows 下 Node 子进程的可执行文件可能带空格；工具函数便于统一处理。 */
export function normalizeExecutablePath(target) {
  return path.normalize(target);
}
