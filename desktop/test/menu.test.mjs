import assert from 'node:assert/strict';
import test from 'node:test';

import { buildApplicationMenu } from '../electron/menu.mjs';

function findSubmenu(template, label) {
  return template.find((item) => item.label === label);
}

function flatLabels(menu) {
  return (menu?.submenu ?? []).map((item) => item.label ?? item.role ?? item.type);
}

test('macOS 应用菜单符合常规', () => {
  const template = buildApplicationMenu({ platform: 'darwin', isDev: false });
  const appMenu = template[0];
  assert.equal(appMenu.label, 'Jinwu');
  const roles = appMenu.submenu.map((item) => item.role);
  assert.ok(roles.includes('about'));
  assert.ok(roles.includes('hide'));
  assert.ok(roles.includes('quit'));
  assert.ok(flatLabels(appMenu).includes('服务'));
});

test('Windows 没有 macOS 应用菜单', () => {
  const template = buildApplicationMenu({ platform: 'win32', isDev: false });
  assert.equal(template[0].label, '文件');
  assert.ok(flatLabels(template[0]).includes('退出'));
});

test('编辑菜单包含常用 role', () => {
  const template = buildApplicationMenu({ platform: 'darwin', isDev: false });
  const roles = findSubmenu(template, '编辑').submenu.map((item) => item.role);
  for (const role of ['undo', 'redo', 'cut', 'copy', 'paste', 'selectAll']) {
    assert.ok(roles.includes(role), `缺少编辑菜单 role：${role}`);
  }
});

test('视图菜单始终提供重载，开发者工具仅 dev 出现', () => {
  const prodView = findSubmenu(
    buildApplicationMenu({ platform: 'darwin', isDev: false }),
    '视图',
  );
  const prodRoles = prodView.submenu.map((item) => item.role);
  assert.ok(prodRoles.includes('forceReload'));
  assert.ok(!prodRoles.includes('toggleDevTools'));
  assert.ok(flatLabels(prodView).includes('重载界面'));

  const devView = findSubmenu(buildApplicationMenu({ platform: 'darwin', isDev: true }), '视图');
  assert.ok(devView.submenu.map((item) => item.role).includes('toggleDevTools'));
});

test('视图菜单提供重启服务；帮助菜单提供打开日志目录', () => {
  let restarted = false;
  let openedLogs = false;
  const template = buildApplicationMenu({
    platform: 'darwin',
    isDev: false,
    actions: {
      restartServices: () => {
        restarted = true;
      },
      openLogsDirectory: () => {
        openedLogs = true;
      },
    },
  });
  const restartItem = findSubmenu(template, '视图').submenu.find(
    (item) => item.label === '重启金乌服务',
  );
  restartItem.click();
  assert.equal(restarted, true);

  const logsItem = findSubmenu(template, '帮助').submenu.find(
    (item) => item.label === '打开日志目录',
  );
  logsItem.click();
  assert.equal(openedLogs, true);
});

test('帮助菜单包含打开工作区目录', () => {
  const template = buildApplicationMenu({ platform: 'win32', isDev: false });
  assert.ok(flatLabels(findSubmenu(template, '帮助')).includes('打开工作区目录'));
});
