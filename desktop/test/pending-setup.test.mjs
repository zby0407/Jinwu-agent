import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { stageSetup, readPendingSetup, clearPendingSetup, backendIdle, frontendQueueIdle } from '../electron/lib/pending-setup.mjs';

test('pending configuration survives reopening and clears only after application', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jw-pending-'));
  try {
    assert.equal(readPendingSetup(dir), null);
    stageSetup(dir, { provider: 'dashscope', api_key: '', proxy_mode: 'direct' });
    assert.equal(readPendingSetup(dir).proxy_mode, 'direct');
    assert.equal(readPendingSetup(dir).api_key, '');
    clearPendingSetup(dir);
    assert.equal(readPendingSetup(dir), null);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('only a successful empty busy-thread search permits restart', async () => {
  let body;
  assert.equal(await backendIdle('http://localhost', async (_url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, json: async () => [] };
  }), true);
  assert.equal(body.status, 'busy');
  assert.equal(await backendIdle('http://localhost', async () => ({ ok: true, json: async () => [{ thread_id: 'queued' }] })), false);
  assert.equal(await backendIdle('http://localhost', async () => ({ ok: false })), false);
  assert.equal(await backendIdle('http://localhost', async () => ({ ok: true, json: async () => ({}) })), false);
});

test('an unavailable renderer cannot confirm an idle frontend queue', async () => {
  assert.equal(await frontendQueueIdle(null), false);
  assert.equal(await frontendQueueIdle({ isDestroyed: () => true }), false);
});
