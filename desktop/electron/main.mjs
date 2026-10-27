/**
 * 金乌（JW）桌面外壳主进程（Electron ESM）。
 *
 * 唯一进程监管者：
 * - 直接 spawn 随包 Python：`<python> -m jw deploy --workdir <workspace> --port <backendPort>`
 * - 直接 spawn 随包 Node：`<node> <runtime>/webui/dist/server.js`
 * - 不使用系统 python/node/npm/uv，也不复用 `jw --ui webui` 的双进程启动器
 * - 后端输出 → `<userData>/logs/backend.log`，前端输出 → `<userData>/logs/webui.log`，
 *   桌面自身 → `<userData>/logs/desktop.log`
 * - 并行启动，后端 `/ok` 与前端根路径都健康后才 loadURL；失败展示可读错误页
 *
 * 退出：before-quit 拦截并异步清理（幂等、有超时），确保不残留
 * python / node / langgraph 进程；清理异常仍允许退出并记录错误。
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { app, BrowserWindow, Menu, dialog, ipcMain, shell } from 'electron';

import { buildApplicationMenu } from './menu.mjs';
import {
  buildBackendEnvironment,
  buildWebuiEnvironment,
  isSecretEnvName,
} from './lib/environment.mjs';
import { renderErrorPage } from './lib/error-page.mjs';
import {
  BACKEND_HEALTH_TIMEOUT_MS,
  WEBUI_HEALTH_TIMEOUT_MS,
  backendHealthUrl,
  waitForHttp,
  webuiHealthUrl,
} from './lib/health.mjs';
import {
  DesktopLogger,
  createLogStream,
  ensureLogDirectory,
  resolveLogPaths,
} from './lib/logs.mjs';
import {
  APP_NAME,
  resolveUserDataDirectory,
  resolveWorkspaceDirectory,
} from './lib/paths.mjs';
import { chooseServicePorts } from './lib/ports.mjs';
import { sweepOrphanedBackend } from './lib/orphan-process.mjs';
import { findMissingRuntimeFiles, loadRuntimeManifest } from './lib/runtime-manifest.mjs';
import {
  readSetupStatus,
  saveSetupConfiguration,
} from './lib/setup.mjs';
import {
  buildWebPreferences,
  decideNavigation,
  decideWindowOpen,
  parseActionUrl,
} from './lib/window-security.mjs';
import { ProcessSupervisor, ServiceExitError } from './services/supervisor.mjs';

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOADING_PAGE_PATH = path.join(APP_ROOT, 'electron', 'ui', 'loading.html');
const SETUP_PAGE_PATH = path.join(APP_ROOT, 'electron', 'ui', 'setup.html');
const SETUP_PRELOAD_PATH = path.join(APP_ROOT, 'electron', 'preload', 'setup-preload.cjs');
const IS_DEV = !app.isPackaged;

const BACKEND_ARGS_TEMPLATE = Object.freeze(['-m', 'jw', 'deploy']);
// 退出宽限期：jw deploy 收到 SIGTERM 后会通过 atexit 停止其 langgraph dev
// 子树（该子树运行在独立 session 中，不属于我们的进程组），因此需要比默认
// 5 秒更长的窗口；宽限期后仍存活则对进程组 SIGKILL。
const TERMINATE_GRACE_MS = 10_000;
const QUIT_CLEANUP_TIMEOUT_MS = 20_000;
const FORCE_EXIT_DELAY_MS = 3_000;

// ---------------------------------------------------------------------------
// 基础路径：app.setName / setPath('userData') 必须在 ready 之前完成。
// ---------------------------------------------------------------------------

app.setName(APP_NAME);
if (process.platform === 'win32') {
  app.setAppUserModelId('com.jinwu.agent');
}

const paths = initialisePaths();
app.setPath('userData', paths.userDataDir);

const logPaths = resolveLogPaths(paths.userDataDir);
ensureLogDirectory(paths.userDataDir);

const logger = new DesktopLogger({ logPath: logPaths.desktop });

const state = {
  window: null,
  setupWindow: null,
  supervisor: null,
  runtime: null,
  ports: null,
  webuiUrl: null,
  backendLogStream: null,
  webuiLogStream: null,
  servicesReady: false,
  restarting: false,
  quitting: false,
  forceQuitStarted: false,
  cleanupPromise: null,
  crashHandlerRegistered: false,
  setupStatus: null,
  setupSchema: [],
  setupSaving: false,
};

process.on('SIGINT', () => app.quit());
process.on('SIGTERM', () => app.quit());
process.on('uncaughtException', (error) => {
  logger.error(`未捕获异常：${error?.stack ?? error}`);
});
process.on('unhandledRejection', (reason) => {
  logger.error(`未处理的 Promise 拒绝：${reason?.stack ?? reason}`);
});

// ---------------------------------------------------------------------------
// 单实例锁：已有实例时聚焦窗口并退出本实例。
// ---------------------------------------------------------------------------

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  logger.info('已有金乌实例在运行，本实例退出。');
  app.quit();
} else {
  registerSetupIpc();
  app.on('second-instance', () => {
    logger.info('检测到第二个实例启动请求，聚焦现有窗口。');
    focusWindow();
  });
  app.on('window-all-closed', () => {
    app.quit();
  });
  app.on('activate', () => {
    void handleActivate();
  });
  app.on('web-contents-created', hardenWebContents);
  app.on('before-quit', handleBeforeQuit);
  app.on('will-quit', handleWillQuit);

  void app
    .whenReady()
    .then(bootstrap)
    .catch((error) => {
      logger.error(`初始化失败：${error?.message ?? error}`);
      void showErrorPage(error, { canRetry: false }).catch(() => {
        dialog.showErrorBox('金乌启动失败', String(error?.message ?? error));
      });
    });
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

function initialisePaths() {
  const homedir = os.homedir();
  try {
    return {
      userDataDir: resolveUserDataDirectory({
        platform: process.platform,
        homedir,
        env: process.env,
      }),
      workspaceDir: resolveWorkspaceDirectory({
        homedir,
        documentsDir:
          process.platform === 'win32' ? path.join(homedir, 'Documents') : undefined,
      }),
      platformError: null,
    };
  } catch (error) {
    // 非 mac/win 平台：仍然启动到可读错误页/对话框，而不是直接崩溃。
    return {
      userDataDir: app.getPath('userData'),
      workspaceDir: path.join(homedir, 'Documents', APP_NAME),
      platformError: error,
    };
  }
}

async function bootstrap() {
  logger.info(
    `金乌桌面外壳启动：Electron ${process.versions.electron}，宿主 ${process.platform}/${process.arch}，dev=${IS_DEV}`,
  );
  logger.info(`用户数据目录：${paths.userDataDir}`);
  logger.info(`默认工作区：${paths.workspaceDir}`);
  refreshMenu();

  if (paths.platformError) {
    await showErrorPage(paths.platformError, { canRetry: false });
    return;
  }

  const runtime = loadRuntime();
  state.runtime = runtime;
  logger.info(`运行时清单：${runtime.manifestPath}`);
  logger.info(
    `运行时版本：python=${runtime.versions.python ?? '未知'} node=${runtime.versions.node ?? '未知'} ` +
      `jw=${runtime.versions.jw ?? '未知'} webui=${runtime.versions.webui ?? '未知'}`,
  );
  const missing = findMissingRuntimeFiles(runtime);
  if (missing.length > 0) {
    throw new Error(
      '运行时文件缺失，安装包可能不完整：\n' +
        missing.map((item) => `${item.field} → ${item.path}`).join('\n'),
    );
  }
  ensureWorkspaceDirectory(paths.workspaceDir);

  const setupStatus = await getSetupStatus(runtime);
  state.setupStatus = setupStatus;
  state.setupSchema = Array.isArray(setupStatus.schema) ? setupStatus.schema : [];
  if (!setupStatus.configured) {
    logger.info(`首次配置未完成，打开配置向导：${setupStatus.configPath}`);
    await showSetupWindow();
    return;
  }

  await showLoadingPage();
  await startServices({ reason: 'launch' });
}

/**
 * Check whether the configured provider has a usable credential. The Python
 * helper reads the same effective config as the backend (config.yaml + env).
 */
async function getSetupStatus(runtime) {
  const env = buildBackendEnvironment({
    runtime,
    workspaceDir: paths.workspaceDir,
    userDataDir: paths.userDataDir,
    backendPort: 6174,
    inheritedEnv: process.env,
  });
  return readSetupStatus({
    pythonPath: runtime.pythonPath,
    cwd: runtime.projectRootPath,
    env,
  });
}

// ---------------------------------------------------------------------------
// 服务启动 / 重启
// ---------------------------------------------------------------------------

async function startServices({ reason = 'launch' } = {}) {
  if (state.restarting) {
    logger.warn('服务正在启动/重启，忽略重复请求。');
    return;
  }
  state.restarting = true;
  state.servicesReady = false;
  try {
    logger.info(`开始启动金乌服务（原因：${reason}）`);
    await showLoadingPage();
    await stopServices(`${reason}-restart`);

    const runtime = loadRuntime();
    state.runtime = runtime;
    logger.info(`运行时清单：${runtime.manifestPath}`);
    logger.info(
      `运行时版本：python=${runtime.versions.python ?? '未知'} node=${runtime.versions.node ?? '未知'} ` +
        `jw=${runtime.versions.jw ?? '未知'} webui=${runtime.versions.webui ?? '未知'}`,
    );

    const missing = findMissingRuntimeFiles(runtime);
    if (missing.length > 0) {
      throw new Error(
        '运行时文件缺失，安装包可能不完整：\n' +
          missing.map((item) => `${item.field} → ${item.path}`).join('\n'),
      );
    }

    ensureWorkspaceDirectory(paths.workspaceDir);

    const ports = await chooseServicePorts();
    state.ports = ports;
    logger.info(
      `端口选择：后端 ${ports.backendPort}（${ports.backendIsDefault ? '默认 6174' : '默认端口被占用，已顺延'}）；` +
        `前端 ${ports.webuiPort}（${ports.webuiIsDefault ? '默认 4716' : '默认端口被占用，已顺延'}）`,
    );

    const backendEnv = buildBackendEnvironment({
      runtime,
      workspaceDir: paths.workspaceDir,
      userDataDir: paths.userDataDir,
      backendPort: ports.backendPort,
      inheritedEnv: process.env,
    });
    const webuiEnv = buildWebuiEnvironment({
      runtime,
      workspaceDir: paths.workspaceDir,
      userDataDir: paths.userDataDir,
      backendPort: ports.backendPort,
      webuiPort: ports.webuiPort,
      inheritedEnv: process.env,
    });
    logEnvironmentSummary('后端', backendEnv);
    logEnvironmentSummary('前端', webuiEnv);
    const leakedSecretNames = Object.keys(webuiEnv).filter(isSecretEnvName).sort();
    if (leakedSecretNames.length > 0) {
      logger.error(`前端环境变量仍包含疑似密钥：${leakedSecretNames.join(', ')}`);
    }

    ensureSupervisor();
    state.backendLogStream = createLogStream(logPaths.backend);
    state.webuiLogStream = createLogStream(logPaths.webui);
    attachStreamErrorHandler(state.backendLogStream, logPaths.backend);
    attachStreamErrorHandler(state.webuiLogStream, logPaths.webui);

    state.supervisor.spawnService({
      name: 'backend',
      label: '后端',
      command: runtime.pythonPath,
      args: [
        ...BACKEND_ARGS_TEMPLATE,
        '--workdir',
        paths.workspaceDir,
        '--port',
        String(ports.backendPort),
      ],
      cwd: runtime.projectRootPath,
      env: backendEnv,
      logStream: state.backendLogStream,
    });
    state.supervisor.spawnService({
      name: 'webui',
      label: '前端',
      command: runtime.nodePath,
      args: [runtime.webuiServerPath],
      cwd: path.dirname(runtime.webuiServerPath),
      env: webuiEnv,
      logStream: state.webuiLogStream,
    });

    logger.info('已并行启动后端与前端，开始健康检查…');
    await waitForServices(ports);

    state.webuiUrl = webuiHealthUrl(ports.webuiPort);
    const window = createWindow();
    await window.loadURL(state.webuiUrl);
    state.servicesReady = true;
    logger.info(`界面已加载：${state.webuiUrl}`);
    refreshMenu();
  } catch (error) {
    logger.error(`启动失败：${error?.message ?? error}`);
    await showErrorPage(error, { canRetry: true }).catch((pageError) => {
      logger.error(`展示错误页失败：${pageError?.message ?? pageError}`);
    });
    await stopServices('start-failed');
    refreshMenu();
  } finally {
    state.restarting = false;
  }
}

async function waitForServices(ports) {
  const abort = new AbortController();
  try {
    await Promise.all([
      waitForHttp({
        url: backendHealthUrl(ports.backendPort),
        timeoutMs: BACKEND_HEALTH_TIMEOUT_MS,
        label: '后端服务',
        signal: abort.signal,
        isAlive: () => state.supervisor?.isRunning('backend') ?? false,
      }),
      waitForHttp({
        url: webuiHealthUrl(ports.webuiPort),
        timeoutMs: WEBUI_HEALTH_TIMEOUT_MS,
        label: 'WebUI 前端',
        signal: abort.signal,
        isAlive: () => state.supervisor?.isRunning('webui') ?? false,
      }),
    ]);
  } catch (error) {
    abort.abort();
    throw error;
  }
}

async function stopServices(reason = 'shutdown') {
  if (state.supervisor) {
    try {
      await state.supervisor.stopAll({ graceMs: TERMINATE_GRACE_MS });
      const leftover = state.supervisor.runningServiceNames();
      if (leftover.length > 0) {
        logger.error(`清理后仍有服务在运行：${leftover.join(', ')}`);
      } else {
        logger.info(`服务清理完成（${reason}）`);
      }
    } catch (error) {
      logger.error(`服务清理异常（${reason}）：${error?.message ?? error}`);
    }
  }
  await sweepLeftoverBackend(reason);
  await closeLogStreams();
}

/**
 * 兜底清理：`jw deploy` 的 langgraph dev 子进程运行在独立 session 中，若后端在
 * 注册 atexit 清理钩子之前就被 SIGTERM 结束，它可能残留并继续占用后端端口。
 * 仅在“我们独占的端口仍能访问 /ok，且监听者被确认是随包 Python 或本端口
 * langgraph dev”时才清理。
 */
async function sweepLeftoverBackend(reason) {
  const port = state.ports?.backendPort;
  if (!port) {
    return;
  }
  try {
    const result = await sweepOrphanedBackend({
      port,
      platform: process.platform,
      pythonPath: state.runtime?.pythonPath ?? '',
      logger,
    });
    if (result.swept > 0) {
      logger.warn(
        `已在清理阶段（${reason}）兜底结束残留后端进程：${result.orphans.join(', ')}`,
      );
    }
  } catch (error) {
    logger.error(`兜底清理残留后端进程失败：${error?.message ?? error}`);
  }
}

function ensureSupervisor() {
  if (!state.supervisor) {
    state.supervisor = new ProcessSupervisor({
      platform: process.platform,
      logger,
    });
  }
  if (!state.crashHandlerRegistered) {
    state.supervisor.onUnexpectedExit((info) => {
      void handleServiceCrash(info).catch((error) => {
        logger.error(`崩溃处理失败：${error?.message ?? error}`);
      });
    });
    state.crashHandlerRegistered = true;
  }
}

async function handleServiceCrash(info) {
  if (state.quitting || state.restarting) {
    return;
  }
  state.restarting = true;
  state.servicesReady = false;
  try {
    const message =
      `${info.label}意外退出（code=${info.code ?? 'null'}, signal=${info.signal ?? 'null'}）。` +
      '服务已停止，可重试启动。';
    logger.error(message);
    await stopServices('crash');
    await showErrorPage(new ServiceExitError(message, info), { canRetry: true });
    refreshMenu();
  } finally {
    state.restarting = false;
  }
}

function logEnvironmentSummary(label, env) {
  const jwKeys = Object.keys(env)
    .filter((name) => name.startsWith('JW_') || name === 'XDG_CONFIG_HOME')
    .sort();
  logger.info(`${label}环境变量（仅 JW_* 名称）：${jwKeys.join(', ')}`);
}

function ensureWorkspaceDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true });
  logger.info(`工作区已就绪：${directory}`);
}

function loadRuntime() {
  return loadRuntimeManifest({
    env: process.env,
    resourcesPath: process.resourcesPath,
    isPackaged: app.isPackaged,
    appRoot: APP_ROOT,
    expectedPlatform: process.platform,
    expectedArch: process.arch,
  });
}

// ---------------------------------------------------------------------------
// 首次配置向导
// ---------------------------------------------------------------------------

function registerSetupIpc() {
  ipcMain.handle('setup:get-status', (event) => {
    try {
      assertSetupSender(event);
    } catch (error) {
      return { ok: false, error: error.message };
    }
    const status = state.setupStatus ?? {};
    return {
      ok: true,
      configured: Boolean(status.configured),
      provider: status.provider ?? null,
      model: status.model ?? null,
      configPath: status.configPath ?? null,
      schema: state.setupSchema,
    };
  });

  ipcMain.handle('setup:save', async (event, payload) => {
    try {
      assertSetupSender(event);
    } catch (error) {
      return { ok: false, error: error.message };
    }
    if (state.setupSaving) {
      return { ok: false, error: '配置正在保存，请稍候。' };
    }
    if (!state.runtime) {
      return { ok: false, error: '运行时尚未加载，无法保存配置。' };
    }
    state.setupSaving = true;
    try {
      const env = buildBackendEnvironment({
        runtime: state.runtime,
        workspaceDir: paths.workspaceDir,
        userDataDir: paths.userDataDir,
        backendPort: 6174,
        inheritedEnv: process.env,
      });
      const result = await saveSetupConfiguration({
        pythonPath: state.runtime.pythonPath,
        cwd: state.runtime.projectRootPath,
        env,
        payload,
      });
      const provider =
        typeof payload?.provider === 'string' ? payload.provider.trim() : '未知';
      logger.info(`首次配置已保存：provider=${provider}；开始启动服务。`);
      state.setupStatus = {
        ...(state.setupStatus ?? {}),
        configured: true,
        provider: result.provider ?? provider,
        model: result.model ?? null,
        configPath: result.configPath ?? null,
      };
      // Let the IPC response reach the page before closing its window.
      setTimeout(() => {
        void activateAfterSetup();
      }, 250);
      return {
        ok: true,
        provider: result.provider,
        model: result.model,
        configPath: result.configPath,
      };
    } catch (error) {
      logger.warn(`首次配置保存失败：${error?.message ?? error}`);
      return { ok: false, error: error?.message ?? String(error) };
    } finally {
      state.setupSaving = false;
    }
  });

  ipcMain.handle('setup:cancel', (event) => {
    try {
      assertSetupSender(event);
    } catch (error) {
      return { ok: false, error: error.message };
    }
    app.quit();
    return { ok: true };
  });
}

function assertSetupSender(event) {
  const expectedUrl = pathToFileURL(SETUP_PAGE_PATH).href;
  const senderUrl = event.senderFrame?.url ?? '';
  const window = state.setupWindow;
  if (
    !window ||
    window.isDestroyed() ||
    event.sender !== window.webContents ||
    !senderUrl.startsWith(expectedUrl)
  ) {
    throw new Error('拒绝非配置页面发起的配置请求');
  }
}

async function activateAfterSetup() {
  try {
    await showLoadingPage();
    const setupWindow = state.setupWindow;
    state.setupWindow = null;
    if (setupWindow && !setupWindow.isDestroyed()) {
      setupWindow.close();
    }
    await startServices({ reason: 'first-run-setup' });
  } catch (error) {
    logger.error(`首次配置完成后启动失败：${error?.message ?? error}`);
    await showErrorPage(error, { canRetry: true });
  }
}

async function showSetupWindow() {
  if (state.setupWindow && !state.setupWindow.isDestroyed()) {
    state.setupWindow.show();
    state.setupWindow.focus();
    return;
  }
  const window = new BrowserWindow({
    width: 720,
    height: 760,
    minWidth: 600,
    minHeight: 640,
    title: `${APP_NAME} · 首次配置`,
    backgroundColor: '#0d1117',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: SETUP_PRELOAD_PATH,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: IS_DEV,
    },
  });
  state.setupWindow = window;
  window.once('ready-to-show', () => {
    if (!window.isDestroyed()) window.show();
  });
  window.on('closed', () => {
    if (state.setupWindow === window) state.setupWindow = null;
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(pathToFileURL(SETUP_PAGE_PATH).href)) {
      event.preventDefault();
    }
  });
  try {
    await window.loadFile(SETUP_PAGE_PATH);
    if (!window.isDestroyed() && !window.isVisible()) window.show();
  } catch (error) {
    logger.error(`加载首次配置页面失败：${error?.message ?? error}`);
    await showErrorPage(error, { canRetry: false });
  }
}

// ---------------------------------------------------------------------------
// 窗口与安全
// ---------------------------------------------------------------------------

function createWindow() {
  if (state.window && !state.window.isDestroyed()) {
    return state.window;
  }
  const window = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: APP_NAME,
    backgroundColor: '#0d1117',
    show: false,
    webPreferences: buildWebPreferences({ isDev: IS_DEV }),
  });
  state.window = window;
  window.once('ready-to-show', () => {
    if (!window.isDestroyed()) {
      window.show();
    }
  });
  window.on('closed', () => {
    if (state.window === window) {
      state.window = null;
    }
  });
  window.webContents.setWindowOpenHandler((details) => {
    const decision = decideWindowOpen(details.url);
    if (decision.openExternal) {
      void openExternalUrl(decision.openExternal);
    }
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    const action = parseActionUrl(url);
    if (action) {
      event.preventDefault();
      void handleDesktopAction(action);
      return;
    }
    const decision = decideNavigation({
      url,
      appUrls: state.webuiUrl ? [state.webuiUrl] : [],
    });
    if (!decision.allow) {
      event.preventDefault();
      if (decision.openExternal) {
        void openExternalUrl(decision.openExternal);
      }
    }
  });
  window.webContents.on('will-attach-webview', (event) => {
    event.preventDefault();
  });
  window.webContents.on('render-process-gone', (_event, details) => {
    logger.error(`渲染进程异常退出：${details?.reason ?? 'unknown'}`);
  });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  window.webContents.session.setPermissionCheckHandler(() => false);
  return window;
}

/** 对所有 webContents 应用同一套安全策略（兜底）。 */
function hardenWebContents(_event, contents) {
  contents.setWindowOpenHandler((details) => {
    const decision = decideWindowOpen(details.url);
    if (decision.openExternal) {
      void openExternalUrl(decision.openExternal);
    }
    return { action: 'deny' };
  });
}

async function openExternalUrl(url) {
  try {
    await shell.openExternal(url);
  } catch (error) {
    logger.warn(`打开外部链接失败：${url}（${error?.message ?? error}）`);
  }
}

function focusWindow() {
  const setupWindow = state.setupWindow;
  if (setupWindow && !setupWindow.isDestroyed()) {
    if (setupWindow.isMinimized()) setupWindow.restore();
    setupWindow.show();
    setupWindow.focus();
    return;
  }
  const window = state.window;
  if (!window || window.isDestroyed()) {
    createWindow();
    return;
  }
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  window.focus();
}

async function handleActivate() {
  if (state.setupWindow && !state.setupWindow.isDestroyed()) {
    state.setupWindow.show();
    state.setupWindow.focus();
    return;
  }
  const window = createWindow();
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  if (state.servicesReady && state.webuiUrl) {
    try {
      await window.loadURL(state.webuiUrl);
    } catch (error) {
      logger.warn(`恢复窗口失败：${error?.message ?? error}`);
    }
  }
}

// ---------------------------------------------------------------------------
// 加载页 / 错误页 / 桌面操作
// ---------------------------------------------------------------------------

async function showLoadingPage() {
  const window = createWindow();
  try {
    await window.loadFile(LOADING_PAGE_PATH);
  } catch (error) {
    logger.warn(`加载启动页失败：${error?.message ?? error}`);
  }
  if (!window.isDestroyed() && !window.isVisible()) {
    window.show();
  }
}

async function showErrorPage(error, { canRetry = true } = {}) {
  state.servicesReady = false;
  const window = createWindow();
  const details = [];
  if (state.ports) {
    details.push(
      `后端端口：${state.ports.backendPort}；前端端口：${state.ports.webuiPort}`,
    );
  }
  if (state.runtime) {
    details.push(
      `运行时：${state.runtime.platform}/${state.runtime.arch}；jw ${state.runtime.versions.jw ?? '未知'}；webui ${state.runtime.versions.webui ?? '未知'}`,
    );
  }
  if (error?.code) {
    details.push(`错误代码：${error.code}`);
  }
  const html = renderErrorPage({
    summary: error?.message ?? String(error),
    details,
    logFiles: {
      directory: logPaths.directory,
      backend: logPaths.backend,
      webui: logPaths.webui,
      desktop: logPaths.desktop,
    },
    // 不内嵌子进程原始日志：其中可能包含配置密钥，错误页只提供路径。
    isDev: IS_DEV,
    canRetry,
  });
  const dataUrl = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
  await window.loadURL(dataUrl);
  if (!window.isDestroyed() && !window.isVisible()) {
    window.show();
  }
}

async function handleDesktopAction(action) {
  logger.info(`执行桌面操作：${action}`);
  switch (action) {
    case 'retry':
      await startServices({ reason: 'retry' });
      break;
    case 'quit':
      app.quit();
      break;
    case 'open-logs':
      await openDirectory(logPaths.directory);
      break;
    case 'open-workspace':
      await openDirectory(paths.workspaceDir);
      break;
    default:
      logger.warn(`未知的桌面操作：${action}`);
  }
}

async function openDirectory(target) {
  try {
    const failure = await shell.openPath(target);
    if (failure) {
      logger.warn(`打开目录失败：${target}（${failure}）`);
    }
  } catch (error) {
    logger.warn(`打开目录失败：${target}（${error?.message ?? error}）`);
  }
}

// ---------------------------------------------------------------------------
// 菜单
// ---------------------------------------------------------------------------

function refreshMenu() {
  const template = buildApplicationMenu({
    isDev: IS_DEV,
    platform: process.platform,
    actions: {
      reloadWindow: () => {
        const window = state.window;
        if (window && !window.isDestroyed()) {
          window.reload();
        }
      },
      restartServices: () => {
        void startServices({ reason: 'menu' });
      },
      openLogsDirectory: () => {
        void openDirectory(logPaths.directory);
      },
      openWorkspaceDirectory: () => {
        void openDirectory(paths.workspaceDir);
      },
    },
  });
  try {
    Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  } catch (error) {
    logger.error(`构建应用菜单失败：${error?.message ?? error}`);
  }
}

// ---------------------------------------------------------------------------
// 日志流
// ---------------------------------------------------------------------------

function attachStreamErrorHandler(stream, filePath) {
  stream?.on?.('error', (error) => {
    logger.error(`日志写入失败：${filePath}（${error?.message ?? error}）`);
  });
}

async function closeLogStreams() {
  const streams = [state.backendLogStream, state.webuiLogStream];
  state.backendLogStream = null;
  state.webuiLogStream = null;
  await Promise.all(
    streams.map(
      (stream) =>
        new Promise((resolve) => {
          if (!stream || stream.destroyed) {
            resolve();
            return;
          }
          stream.end(() => resolve());
        }),
    ),
  );
}

// ---------------------------------------------------------------------------
// 退出清理（幂等 + 超时 + 异常仍退出）
// ---------------------------------------------------------------------------

function handleBeforeQuit(event) {
  if (state.quitting) {
    return;
  }
  state.quitting = true;
  event.preventDefault();
  logger.info('收到退出请求，开始清理子进程…');

  const cleanup = cleanupForQuit();
  const timeout = new Promise((resolve) => {
    const timer = setTimeout(() => {
      logger.error(`退出清理超过 ${QUIT_CLEANUP_TIMEOUT_MS} ms，强制退出。`);
      resolve('timeout');
    }, QUIT_CLEANUP_TIMEOUT_MS);
    timer.unref?.();
  });

  void Promise.race([cleanup, timeout])
    .then(() => {
      logger.info('子进程清理结束，退出应用。');
    })
    .catch((error) => {
      logger.error(`退出清理失败（仍继续退出）：${error?.message ?? error}`);
    })
    .finally(() => {
      forceQuit();
    });
}

function cleanupForQuit() {
  if (!state.cleanupPromise) {
    state.cleanupPromise = (async () => {
      await stopServices('quit');
    })();
  }
  return state.cleanupPromise;
}

function forceQuit() {
  if (state.forceQuitStarted) {
    return;
  }
  state.forceQuitStarted = true;
  // 第二次 quit 会跳过 before-quit 的 preventDefault，正常进入 will-quit。
  app.quit();
  setTimeout(() => {
    logger.warn('退出流程未在预期时间内完成，强制退出进程。');
    app.exit(0);
  }, FORCE_EXIT_DELAY_MS);
}

function handleWillQuit() {
  logger.info('应用退出。');
  void logger.close();
}
