import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  APP_NAME,
  UnsupportedPlatformError,
  resolveConfigDirectory,
  resolveDataDirectory,
  resolveUserDataDirectory,
  resolveWorkspaceDirectory,
} from '../electron/lib/paths.mjs';

const HOMEDIR = '/Users/tester';

test('应用名为 Jinwu', () => {
  assert.equal(APP_NAME, 'Jinwu');
});

test('macOS 用户数据目录位于 Library/Application Support/Jinwu', () => {
  assert.equal(
    resolveUserDataDirectory({ platform: 'darwin', homedir: HOMEDIR, env: {} }),
    path.join(HOMEDIR, 'Library', 'Application Support', 'Jinwu'),
  );
});

test('Windows 用户数据目录使用 %LOCALAPPDATA%\\Jinwu', () => {
  assert.equal(
    resolveUserDataDirectory({
      platform: 'win32',
      homedir: 'C:\\Users\\tester',
      env: { LOCALAPPDATA: 'C:\\Users\\tester\\AppData\\Local' },
    }),
    path.join('C:\\Users\\tester\\AppData\\Local', 'Jinwu'),
  );
});

test('Windows 缺少 LOCALAPPDATA 时回退到 AppData\\Local', () => {
  assert.equal(
    resolveUserDataDirectory({
      platform: 'win32',
      homedir: 'C:\\Users\\tester',
      env: {},
    }),
    path.join('C:\\Users\\tester', 'AppData', 'Local', 'Jinwu'),
  );
});

test('不支持的平台抛错（初版仅 mac/win）', () => {
  assert.throws(
    () => resolveUserDataDirectory({ platform: 'linux', homedir: HOMEDIR, env: {} }),
    (error) => {
      assert.ok(error instanceof UnsupportedPlatformError);
      assert.equal(error.code, 'UNSUPPORTED_PLATFORM');
      return true;
    },
  );
});

test('默认工作区为 Documents/Jinwu', () => {
  assert.equal(
    resolveWorkspaceDirectory({ homedir: HOMEDIR }),
    path.join(HOMEDIR, 'Documents', 'Jinwu'),
  );
  assert.equal(
    resolveWorkspaceDirectory({ homedir: HOMEDIR, documentsDir: '/Volumes/data/Documents' }),
    path.join('/Volumes/data/Documents', 'Jinwu'),
  );
});

test('data / config 子目录位于 userData 之下', () => {
  const userData = '/Users/tester/Library/Application Support/Jinwu';
  assert.equal(resolveDataDirectory(userData), path.join(userData, 'data'));
  assert.equal(resolveConfigDirectory(userData), path.join(userData, 'config'));
});

test('homedir 缺失时快速失败', () => {
  assert.throws(() => resolveUserDataDirectory({ platform: 'darwin' }), /homedir/);
  assert.throws(() => resolveWorkspaceDirectory({}), /homedir/);
  assert.equal(typeof os.tmpdir(), 'string');
});
