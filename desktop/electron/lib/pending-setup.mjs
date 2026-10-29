import fs from 'node:fs';
import path from 'node:path';
import { normalizeSetupPayload } from './setup.mjs';

export function pendingSetupPath(userDataDir) {
  return path.join(userDataDir, 'config', 'pending-desktop-setup.json');
}

export function stageSetup(userDataDir, payload) {
  const target = pendingSetupPath(userDataDir);
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temp = `${target}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(normalizeSetupPayload(payload)), { mode: 0o600 });
  fs.renameSync(temp, target);
}

export function readPendingSetup(userDataDir) {
  const target = pendingSetupPath(userDataDir);
  if (!fs.existsSync(target)) return null;
  return normalizeSetupPayload(JSON.parse(fs.readFileSync(target, 'utf8')));
}

export function clearPendingSetup(userDataDir) {
  fs.rmSync(pendingSetupPath(userDataDir), { force: true });
}

export async function frontendQueueIdle(webContents) {
  if (!webContents || webContents.isDestroyed()) return false;
  return webContents.executeJavaScript(`Array.from(document.querySelectorAll('[data-jw-queued-count]')).every(el => el.getAttribute('data-jw-queued-count') === '0')`);
}

// Unknown state is deliberately not idle. No restart on an unavailable API.
export async function backendIdle(base, fetchImpl = fetch) {
  for (const status of ['busy']) {
    const response = await fetchImpl(`${base}/threads/search`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, limit: 1 }), signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return false;
    const threads = await response.json();
    if (!Array.isArray(threads) || threads.length) return false;
  }
  return true;
}
