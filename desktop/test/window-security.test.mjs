import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ACTION_URL_PREFIX,
  buildWebPreferences,
  decideNavigation,
  decideWindowOpen,
  isAppUrl,
  isExternalUrl,
  parseActionUrl,
} from '../electron/lib/window-security.mjs';

test('生产环境安全基线：隔离、无 Node、sandbox、无 preload', () => {
  const prefs = buildWebPreferences({ isDev: false });
  assert.equal(prefs.contextIsolation, true);
  assert.equal(prefs.nodeIntegration, false);
  assert.equal(prefs.sandbox, true);
  assert.equal(prefs.preload, undefined);
  assert.equal(prefs.webSecurity, true);
  assert.equal(prefs.allowRunningInsecureContent, false);
  assert.equal(prefs.devTools, false);
});

test('开发环境允许 DevTools，但安全基线保持不变', () => {
  const prefs = buildWebPreferences({ isDev: true });
  assert.equal(prefs.devTools, true);
  assert.equal(prefs.contextIsolation, true);
  assert.equal(prefs.nodeIntegration, false);
  assert.equal(prefs.sandbox, true);
});

test('window.open 一律拒绝，仅 https 交给系统浏览器', () => {
  assert.deepEqual(decideWindowOpen('https://example.com/doc'), {
    action: 'deny',
    openExternal: 'https://example.com/doc',
  });
  assert.deepEqual(decideWindowOpen('http://example.com'), {
    action: 'deny',
    openExternal: null,
  });
  assert.deepEqual(decideWindowOpen('file:///etc/passwd'), {
    action: 'deny',
    openExternal: null,
  });
});

test('解析错误页操作链接', () => {
  assert.equal(ACTION_URL_PREFIX, 'jinwu-action:');
  assert.equal(parseActionUrl('jinwu-action:retry'), 'retry');
  assert.equal(parseActionUrl('jinwu-action:quit'), 'quit');
  assert.equal(parseActionUrl('jinwu-action:open-logs'), 'open-logs');
  assert.equal(parseActionUrl('jinwu-action:open-workspace'), 'open-workspace');
  assert.equal(parseActionUrl('jinwu-action:unknown'), null);
  assert.equal(parseActionUrl('https://example.com'), null);
  assert.equal(parseActionUrl(undefined), null);
});

test('同源导航允许，外部 https 外链交给系统浏览器', () => {
  const appUrls = ['http://127.0.0.1:4716/'];
  assert.deepEqual(decideNavigation({ url: 'http://127.0.0.1:4716/chat', appUrls }), {
    allow: true,
    openExternal: null,
  });
  assert.deepEqual(decideNavigation({ url: 'https://example.com', appUrls }), {
    allow: false,
    openExternal: 'https://example.com',
  });
  assert.deepEqual(decideNavigation({ url: 'http://example.com', appUrls }), {
    allow: false,
    openExternal: null,
  });
  assert.deepEqual(decideNavigation({ url: 'file:///etc/passwd', appUrls }), {
    allow: false,
    openExternal: null,
  });
});

test('错误页 data: URL 允许作为主进程自身内容', () => {
  const decision = decideNavigation({ url: 'data:text/html;charset=utf-8,abc', appUrls: [] });
  assert.equal(decision.allow, true);
});

test('URL 判定辅助函数', () => {
  assert.equal(isExternalUrl('https://example.com'), true);
  assert.equal(isExternalUrl('http://example.com'), false);
  assert.equal(isExternalUrl('not a url'), false);
  assert.equal(isAppUrl('http://127.0.0.1:4716/x', ['http://127.0.0.1:4716/']), true);
  assert.equal(isAppUrl('http://127.0.0.1:4717/x', ['http://127.0.0.1:4716/']), false);
});
