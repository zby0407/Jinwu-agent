import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  LOG_DIRECTORY_NAME,
  LOG_SERVICES,
  formatLogLine,
  logDirectory,
  logFilePath,
  resolveLogPaths,
} from '../electron/lib/logs.mjs';

const USER_DATA = path.join(os.tmpdir(), 'jw-desktop-user-data');

test('日志目录位于 userData/logs', () => {
  assert.equal(LOG_DIRECTORY_NAME, 'logs');
  assert.equal(logDirectory(USER_DATA), path.join(USER_DATA, 'logs'));
  assert.throws(() => logDirectory(''), /userDataDir/);
});

test('三类日志文件路径符合契约', () => {
  assert.equal(logFilePath(USER_DATA, 'desktop'), path.join(USER_DATA, 'logs', 'desktop.log'));
  assert.equal(logFilePath(USER_DATA, 'backend'), path.join(USER_DATA, 'logs', 'backend.log'));
  assert.equal(logFilePath(USER_DATA, 'webui'), path.join(USER_DATA, 'logs', 'webui.log'));
  assert.deepEqual([...LOG_SERVICES], ['desktop', 'backend', 'webui']);
});

test('未知日志类型抛错', () => {
  assert.throws(() => logFilePath(USER_DATA, 'python'), /未知的日志类型/);
});

test('resolveLogPaths 一次解析全部路径', () => {
  const paths = resolveLogPaths(USER_DATA);
  assert.deepEqual(paths, {
    directory: path.join(USER_DATA, 'logs'),
    desktop: path.join(USER_DATA, 'logs', 'desktop.log'),
    backend: path.join(USER_DATA, 'logs', 'backend.log'),
    webui: path.join(USER_DATA, 'logs', 'webui.log'),
  });
});

test('formatLogLine 生成带时间戳与级别的单行日志', () => {
  const line = formatLogLine({
    time: new Date('2026-01-02T03:04:05.000Z'),
    level: 'error',
    message: '启动失败',
  });
  assert.equal(line, '2026-01-02T03:04:05.000Z [ERROR] 启动失败');
});
