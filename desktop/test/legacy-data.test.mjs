import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  LEGACY_DATA_MARKER,
  importLegacyData,
  resolveLegacyDataDirectory,
} from '../electron/lib/legacy-data.mjs';

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf8');
}

function seedLegacyData(legacyDir) {
  writeFile(path.join(legacyDir, 'memories', 'profile', 'SOUL.md'), '# SOUL\n');
  writeFile(
    path.join(legacyDir, 'memories', 'profile', 'projects', 'P-1', 'PROJECT_PROFILE.md'),
    '# project\n',
  );
  writeFile(
    path.join(legacyDir, 'memories', 'observations', 'projects', 'P-1', 'O-1.md'),
    '# obs\n',
  );
  writeFile(path.join(legacyDir, 'knowledge.db'), 'sqlite-bytes');
  writeFile(path.join(legacyDir, 'knowledge.db-wal'), 'wal-bytes');
  writeFile(path.join(legacyDir, 'sessions.db'), 'sessions-bytes');
}

test('resolveLegacyDataDirectory 默认 ~/.jw，可用 JW_LEGACY_DATA_DIR 覆盖', () => {
  assert.equal(
    resolveLegacyDataDirectory({ homedir: '/Users/tester', env: {} }),
    path.join('/Users/tester', '.jw'),
  );
  assert.equal(
    resolveLegacyDataDirectory({
      homedir: '/Users/tester',
      env: { JW_LEGACY_DATA_DIR: '/tmp/custom-jw' },
    }),
    '/tmp/custom-jw',
  );
  assert.throws(() => resolveLegacyDataDirectory({ env: {} }), TypeError);
});

test('首次启动把记忆与知识库拷贝到 <userData>/data 并写标记', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');
  const legacyDir = path.join(home, '.jw');
  seedLegacyData(legacyDir);

  const result = importLegacyData({ userDataDir, homedir: home, env: {} });

  assert.equal(result.status, 'imported');
  assert.deepEqual(result.failed, []);
  const dataDir = path.join(userDataDir, 'data');
  assert.equal(
    fs.readFileSync(path.join(dataDir, 'memories', 'profile', 'SOUL.md'), 'utf8'),
    '# SOUL\n',
  );
  assert.equal(
    fs.readFileSync(
      path.join(dataDir, 'memories', 'observations', 'projects', 'P-1', 'O-1.md'),
      'utf8',
    ),
    '# obs\n',
  );
  assert.equal(fs.readFileSync(path.join(dataDir, 'knowledge.db'), 'utf8'), 'sqlite-bytes');
  assert.equal(fs.readFileSync(path.join(dataDir, 'knowledge.db-wal'), 'utf8'), 'wal-bytes');
  assert.equal(fs.readFileSync(path.join(dataDir, 'sessions.db'), 'utf8'), 'sessions-bytes');
  assert.ok(fs.existsSync(path.join(dataDir, LEGACY_DATA_MARKER)));
});

test('不覆盖桌面版已有文件，只补齐缺失内容', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');
  const legacyDir = path.join(home, '.jw');
  seedLegacyData(legacyDir);
  const dataDir = path.join(userDataDir, 'data');
  writeFile(path.join(dataDir, 'memories', 'profile', 'SOUL.md'), '# desktop SOUL\n');
  writeFile(path.join(dataDir, 'knowledge.db'), 'desktop-db');

  const result = importLegacyData({ userDataDir, homedir: home, env: {} });

  assert.equal(result.status, 'imported');
  assert.equal(
    fs.readFileSync(path.join(dataDir, 'memories', 'profile', 'SOUL.md'), 'utf8'),
    '# desktop SOUL\n',
  );
  assert.equal(fs.readFileSync(path.join(dataDir, 'knowledge.db'), 'utf8'), 'desktop-db');
  // 缺失的库没有被补上（主库已存在时整组跳过）。
  assert.equal(fs.existsSync(path.join(dataDir, 'knowledge.db-wal')), false);
  // 其他缺失文件仍然补齐。
  assert.equal(
    fs.readFileSync(
      path.join(dataDir, 'memories', 'observations', 'projects', 'P-1', 'O-1.md'),
      'utf8',
    ),
    '# obs\n',
  );
});

test('用户显式设置 JW_DATA_DIR 时完全跳过', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');
  seedLegacyData(path.join(home, '.jw'));

  const result = importLegacyData({
    userDataDir,
    homedir: home,
    env: { JW_DATA_DIR: '/tmp/somewhere-else' },
  });

  assert.equal(result.status, 'skipped');
  assert.equal(result.reason, 'data-dir-overridden');
  assert.equal(fs.existsSync(path.join(userDataDir, 'data')), false);
});

test('已有标记时不重复导入', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');
  seedLegacyData(path.join(home, '.jw'));
  writeFile(path.join(userDataDir, 'data', LEGACY_DATA_MARKER), 'done\n');

  const result = importLegacyData({ userDataDir, homedir: home, env: {} });

  assert.equal(result.status, 'skipped');
  assert.equal(result.reason, 'already-imported');
  assert.equal(fs.existsSync(path.join(userDataDir, 'data', 'knowledge.db')), false);
});

test('没有旧数据目录或旧数据为空时不导入', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');

  const missing = importLegacyData({ userDataDir, homedir: home, env: {} });
  assert.equal(missing.status, 'skipped');
  assert.equal(missing.reason, 'no-legacy-directory');

  fs.mkdirSync(path.join(home, '.jw', 'skills'), { recursive: true });
  const empty = importLegacyData({ userDataDir, homedir: home, env: {} });
  assert.equal(empty.status, 'skipped');
  assert.equal(empty.reason, 'no-legacy-content');
  // 空目录不写标记：用户之后用命令行版产生的数据仍会被导入。
  assert.equal(fs.existsSync(path.join(userDataDir, 'data', LEGACY_DATA_MARKER)), false);
});

test('拷贝失败时返回 failed 且不写标记，便于下次重试', () => {
  const home = makeTempDir('jw-legacy-home-');
  const userDataDir = makeTempDir('jw-legacy-userdata-');
  seedLegacyData(path.join(home, '.jw'));
  // 用普通文件占住 memories 目录位置，制造合并失败。
  writeFile(path.join(userDataDir, 'data', 'memories'), 'not-a-directory');

  const result = importLegacyData({ userDataDir, homedir: home, env: {} });

  assert.equal(result.status, 'failed');
  assert.ok(result.failed.length > 0);
  assert.equal(fs.existsSync(path.join(userDataDir, 'data', LEGACY_DATA_MARKER)), false);
});
