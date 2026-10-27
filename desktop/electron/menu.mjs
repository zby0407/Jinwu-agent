/**
 * 应用菜单（模板与副作用分离，便于测试）。
 *
 * 约定：
 * - macOS 应用菜单符合常规（关于 / 服务 / 隐藏 / 退出）
 * - 编辑菜单使用系统 role
 * - 视图菜单始终提供重载/缩放；开发者工具仅在 dev 模式出现
 * - 帮助菜单提供打开日志目录、打开工作区
 */

export const APP_MENU_LABEL = 'Jinwu';

/**
 * @param {object} options
 * @param {boolean} [options.isDev]
 * @param {string} [options.platform]
 * @param {object} [options.actions]
 * @param {() => void} [options.actions.openLogsDirectory]
 * @param {() => void} [options.actions.openWorkspaceDirectory]
 * @param {() => void} [options.actions.restartServices]
 * @param {() => void} [options.actions.reloadWindow]
 */
export function buildApplicationMenu({ isDev = false, platform = process.platform, actions = {} } = {}) {
  const isMac = platform === 'darwin';
  const template = [];

  if (isMac) {
    template.push({
      label: APP_MENU_LABEL,
      submenu: [
        { role: 'about', label: `关于 ${APP_MENU_LABEL}` },
        { type: 'separator' },
        { role: 'services', label: '服务' },
        { type: 'separator' },
        { role: 'hide', label: `隐藏 ${APP_MENU_LABEL}` },
        { role: 'hideOthers', label: '隐藏其他' },
        { role: 'unhide', label: '显示全部' },
        { type: 'separator' },
        { role: 'quit', label: `退出 ${APP_MENU_LABEL}` },
      ],
    });
  }

  template.push({
    label: '文件',
    submenu: [isMac ? { role: 'close', label: '关闭窗口' } : { role: 'quit', label: '退出' }],
  });

  template.push({
    label: '编辑',
    submenu: [
      { role: 'undo', label: '撤销' },
      { role: 'redo', label: '重做' },
      { type: 'separator' },
      { role: 'cut', label: '剪切' },
      { role: 'copy', label: '复制' },
      { role: 'paste', label: '粘贴' },
      { role: 'selectAll', label: '全选' },
    ],
  });

  const viewSubmenu = [
    {
      label: '重载界面',
      accelerator: 'CmdOrCtrl+R',
      click: actions.reloadWindow,
    },
    { label: '强制重载', accelerator: 'CmdOrCtrl+Shift+R', role: 'forceReload' },
  ];
  if (isDev) {
    viewSubmenu.push(
      { type: 'separator' },
      { label: '开发者工具', accelerator: 'CmdOrCtrl+Alt+I', role: 'toggleDevTools' },
    );
  }
  viewSubmenu.push(
    { type: 'separator' },
    { role: 'resetZoom', label: '实际大小' },
    { role: 'zoomIn', label: '放大' },
    { role: 'zoomOut', label: '缩小' },
    { type: 'separator' },
    {
      label: '重启金乌服务',
      accelerator: 'CmdOrCtrl+Shift+K',
      click: actions.restartServices,
    },
  );
  template.push({ label: '视图', submenu: viewSubmenu });

  template.push({
    label: '窗口',
    submenu: [
      { role: 'minimize', label: '最小化' },
      { role: 'zoom', label: '缩放' },
      ...(isMac ? [{ type: 'separator' }, { role: 'front', label: '前置全部窗口' }] : []),
    ],
  });

  template.push({
    label: '帮助',
    role: 'help',
    submenu: [
      { label: '打开日志目录', click: actions.openLogsDirectory },
      { label: '打开工作区目录', click: actions.openWorkspaceDirectory },
      ...(isMac
        ? []
        : [
            { type: 'separator' },
            { role: 'about', label: `关于 ${APP_MENU_LABEL}` },
          ]),
    ],
  });

  return template;
}
