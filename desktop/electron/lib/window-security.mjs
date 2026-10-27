/**
 * 窗口安全基线。
 *
 * - contextIsolation: true / nodeIntegration: false / sandbox: true / 无 preload
 * - 禁止 window.open 与新窗口；外部 https 链接交给系统浏览器
 * - 生产禁用 DevTools（devTools: false 且菜单不提供入口）
 *
 * 错误页与加载页不依赖 preload：错误页上的操作使用自定义 `jinwu-action:` 链接，
 * 主进程在 `will-navigate` 中拦截并执行（因此渲染进程无需任何 Node 能力）。
 */

export const ACTION_URL_PREFIX = 'jinwu-action:';
export const DESKTOP_ACTIONS = Object.freeze([
  'retry',
  'quit',
  'open-logs',
  'open-workspace',
]);
export const EXTERNAL_PROTOCOLS = Object.freeze(['https:']);

/** 主窗口 webPreferences（安全基线 + 生产关闭 DevTools）。 */
export function buildWebPreferences({ isDev = false } = {}) {
  return {
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    experimentalFeatures: false,
    spellcheck: false,
    devTools: Boolean(isDev),
    backgroundThrottling: false,
    // 明确不提供 preload：渲染进程没有任何桥接能力。
    preload: undefined,
  };
}

/** 解析错误页/加载页上的自定义操作链接。 */
export function parseActionUrl(rawUrl) {
  if (typeof rawUrl !== 'string' || !rawUrl.startsWith(ACTION_URL_PREFIX)) {
    return null;
  }
  const action = rawUrl.slice(ACTION_URL_PREFIX.length).replace(/[/?#].*$/, '');
  return DESKTOP_ACTIONS.includes(action) ? action : null;
}

/**
 * window.open / target=_blank 决策：一律拒绝；仅 https 交给系统浏览器。
 * @returns {{action: 'deny', openExternal: string|null}}
 */
export function decideWindowOpen(url) {
  return { action: 'deny', openExternal: isExternalUrl(url) ? url : null };
}

/**
 * 页面内导航决策：允许应用自身地址；其余 https 外链交给系统浏览器，其他一律拒绝。
 *
 * @param {object} options
 * @param {string} options.url
 * @param {string[]} [options.appUrls] 允许留在应用内的地址（如 WebUI 根地址）
 * @returns {{allow: boolean, openExternal: string|null}}
 */
export function decideNavigation({ url, appUrls = [] } = {}) {
  if (typeof url !== 'string' || url.length === 0) {
    return { allow: false, openExternal: null };
  }
  if (isAppUrl(url, appUrls)) {
    return { allow: true, openExternal: null };
  }
  // 主进程自身加载的错误页（data: URL）允许继续导航。
  if (url.startsWith('data:text/html')) {
    return { allow: true, openExternal: null };
  }
  if (isExternalUrl(url)) {
    return { allow: false, openExternal: url };
  }
  return { allow: false, openExternal: null };
}

/** 是否为 https 外链。 */
export function isExternalUrl(url) {
  try {
    const parsed = new URL(url);
    return EXTERNAL_PROTOCOLS.includes(parsed.protocol);
  } catch {
    return false;
  }
}

/** 是否属于应用内部地址（同源）。 */
export function isAppUrl(url, appUrls = []) {
  let target;
  try {
    target = new URL(url);
  } catch {
    return false;
  }
  for (const appUrl of appUrls) {
    if (typeof appUrl !== 'string' || appUrl.length === 0) {
      continue;
    }
    try {
      const allowed = new URL(appUrl);
      if (target.origin === allowed.origin) {
        return true;
      }
    } catch {
      // 配置错误时忽略该条。
    }
  }
  return false;
}
