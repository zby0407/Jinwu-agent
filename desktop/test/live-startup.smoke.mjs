/** Run explicitly with Electron, using an isolated USERPROFILE and LOCALAPPDATA.
 * Never sends a model request. The parent checks cleanup after this app exits.
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { app, BrowserWindow, Menu } from 'electron';

const output = process.env.JW_SMOKE_OUTPUT;
if (!output || !path.resolve(os.homedir()).startsWith(path.resolve(output))) {
  throw new Error('Smoke test requires a separate profile below JW_SMOKE_OUTPUT');
}
const started = performance.now() - (process.env.JW_SMOKE_STARTED ? Date.now() - Number(process.env.JW_SMOKE_STARTED) : 0);
const report = { windowMs: null, frontendMs: null, agentMs: null };
const rendererMessages = [];
app.on('browser-window-created', (_event, window) => {
  window.webContents.on('console-message', (event) => {
    rendererMessages.push({ level: event.level, message: event.message });
  });
  window.webContents.on('did-fail-load', (_event, code, description, url) => {
    rendererMessages.push({ code, description, url });
  });
  window.once('ready-to-show', () => {
    report.windowMs ??= Math.round(performance.now() - started);
  });
  window.webContents.on('did-finish-load', () => {
    if (window.webContents.getURL().startsWith('http://127.0.0.1:')) {
      report.frontendMs ??= Math.round(performance.now() - started);
    }
  });
});
await import(process.env.JW_SMOKE_ENTRY ? pathToFileURL(process.env.JW_SMOKE_ENTRY).href : '../electron/main.mjs');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate, timeout = 120000) {
  const start = performance.now();
  while (performance.now() - start < timeout) {
    const value = await predicate();
    if (value) return value;
    await delay(100);
  }
  throw new Error('Timed out waiting for desktop stage');
}

void app.whenReady().then(async () => {
try {
  if (process.env.JW_SMOKE_FIRST_SETUP === '1') {
    const initial = await until(() => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().endsWith('setup.html')));
    await until(() => initial.webContents.executeJavaScript('Boolean(document.querySelector("#provider option"))'));
    const status = await initial.webContents.executeJavaScript('window.jinwuSetup.getStatus()');
    if (status.configured || status.hasCredential || status.proxyMode !== 'env') throw new Error('Unexpected first-run defaults');
    const result = await initial.webContents.executeJavaScript('window.jinwuSetup.save({provider:"dashscope",model:"qwen3.7-plus",api_key:"synthetic-startup-only",proxy_mode:"direct"})');
    if (!result.ok) throw new Error('First-run setup failed');
    report.firstSetup = true;
  }
  const window = await until(() => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith('http://127.0.0.1:')));
  const config = await (await fetch(new URL('/api/jw-config', window.webContents.getURL()))).json();
  report.backendUrl = config.deploymentUrl;
  report.webuiUrl = window.webContents.getURL();
  if (process.env.JW_SMOKE_LEGACY === '1') {
    await until(() => window.webContents.executeJavaScript(`(() => {
      if (document.querySelector('textarea')) return true;
      const save = Array.from(document.querySelectorAll('button')).find(el => el.textContent.trim() === '保存');
      if (!save) return false;
      save.click(); return true;
    })()`));
  }
  const composerReady = until(() => window.webContents.executeJavaScript('Boolean(document.querySelector("textarea"))'), 15000)
    .then(() => { report.composerMs = Math.round(performance.now() - started); });
  await until(async () => {
    try {
      const response = await fetch(`${config.deploymentUrl}/api/runtime-status`);
      if (response.status === 404 && process.env.JW_SMOKE_LEGACY === '1') {
        return (await fetch(`${config.deploymentUrl}/ok`)).ok;
      }
      const status = await response.json();
      if (status.agent?.state === 'failed') throw new Error(status.agent.message);
      return status.canSend;
    } catch (error) { if (error.message?.includes('初始化失败')) throw error; return false; }
  });
  report.agentMs = Math.round(performance.now() - started);
  await composerReady;
  if (process.env.JW_SMOKE_SETTINGS === '1') {
    await window.webContents.executeJavaScript(`(() => {
      const el = document.querySelector('textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, 'smoke draft');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    Menu.getApplicationMenu().items.find((item) => item.label === '文件').submenu.items.find((item) => item.label === 'API 与连接设置').click();
    const settings = await until(() => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().endsWith('setup.html')));
    await until(() => settings.webContents.executeJavaScript('Boolean(document.querySelector("#provider option"))'));
    const status = await settings.webContents.executeJavaScript('window.jinwuSetup.getStatus()');
    if (!status.hasCredential) throw new Error('Saved credential was not detected');
    if (await settings.webContents.executeJavaScript('document.querySelector("#api-key").value')) throw new Error('Credential unexpectedly displayed');
    await window.webContents.executeJavaScript('document.querySelector("[data-jw-queued-count]").setAttribute("data-jw-queued-count", "1")');
    const saved = await settings.webContents.executeJavaScript('window.jinwuSetup.save({provider:"dashscope", model:"qwen3.7-plus", api_key:"", proxy_mode:"direct"})');
    if (!saved.ok || !saved.pending) throw new Error('Configuration ignored a pending frontend queue');
    if (!(await (await fetch(`${config.deploymentUrl}/api/runtime-status`)).json()).canSend) throw new Error('Maintenance gate was not released while waiting');
    await window.webContents.executeJavaScript('document.querySelector("[data-jw-queued-count]").setAttribute("data-jw-queued-count", "0")');
    report.queuedChangeDeferred = true;
    await until(() => settings.isDestroyed());
    await delay(1500);
    await until(async () => {
      try { return (await (await fetch(`${config.deploymentUrl}/api/runtime-status`)).json()).canSend; }
      catch { return false; }
    });
    if (await window.webContents.executeJavaScript('document.querySelector("textarea").value') !== 'smoke draft') throw new Error('Draft was lost during backend restart');
    report.settingsPreservedDraft = true;
    const yaml = fs.readFileSync(path.join(output, 'local/Jinwu/config/jw/config.yaml'), 'utf8');
    if (!yaml.includes('dashscope_api_key: synthetic-startup-only')) throw new Error('Existing credential was not retained');
    report.credentialRetained = true;
  }
  fs.writeFileSync(path.join(output, 'desktop-smoke.json'), JSON.stringify({ ...report, ok: true }, null, 2));
} catch (error) {
  report.rendererMessages = rendererMessages;
  report.windows = await Promise.all(BrowserWindow.getAllWindows().map(async (window) => ({
    url: window.webContents.getURL(),
    body: await window.webContents.executeJavaScript('document.body.innerText').catch(() => 'unavailable'),
  })));
  fs.writeFileSync(path.join(output, 'desktop-smoke.json'), JSON.stringify({ ...report, ok: false, error: error.message }, null, 2));
  process.exitCode = 1;
} finally {
  app.quit();
}
});
