// Resume an existing persisted research thread through the production WebUI.
// The continuation text is entered in the browser composer; no backend run is
// created directly by this harness.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [
  threadId,
  continuation = "继续完成上述完整科研闭环。",
  requestedProvider = "",
  requestedModel = "",
] = process.argv.slice(2);
if (!/^[A-Za-z0-9-]+$/.test(threadId || "")) {
  throw new Error(
    "Usage: node run_webui_resume.mjs <thread-id> [continuation] [provider] [model]",
  );
}
if (Boolean(requestedProvider) !== Boolean(requestedModel)) {
  throw new Error("provider and model must be supplied together");
}
const frontend = process.env.JW_EVAL_FRONTEND || "http://127.0.0.1:4717/";
const backend = process.env.JW_EVAL_BACKEND || "http://127.0.0.1:6174";
const chromium =
  process.env.JW_EVAL_CHROME ||
  "/home/zzz/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome";
const port = Number(process.env.JW_EVAL_DEBUG_PORT || 9228);
const headless = process.env.JW_EVAL_HEADLESS !== "0";
const hasWslg = existsSync("/mnt/wslg/runtime-dir/wayland-0");
if (
  !headless &&
  !process.env.DISPLAY &&
  !process.env.WAYLAND_DISPLAY &&
  !hasWslg
) {
  throw new Error(
    "Visible frontend validation requires a graphical display; use JW_EVAL_HEADLESS=1 only for automation diagnostics",
  );
}
const chromeEnv = { ...process.env };
if (!headless && hasWslg) {
  chromeEnv.DISPLAY ||= ":0";
  chromeEnv.WAYLAND_DISPLAY ||= "wayland-0";
  chromeEnv.XDG_RUNTIME_DIR ||= "/mnt/wslg/runtime-dir";
  chromeEnv.PULSE_SERVER ||= "/mnt/wslg/PulseServer";
}
const profile = await mkdtemp(path.join(os.tmpdir(), "jw-eval-resume-"));
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const observerUrl = new URL(frontend);
observerUrl.searchParams.set("threadId", threadId);
process.stdout.write(`${JSON.stringify({
  schema_version: "webui-eval-observer-v1",
  event: "observer_ready",
  thread_id: threadId,
  observer_url: observerUrl.toString(),
})}\n`);

async function waitFor(fn, timeout, label) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await delay(500);
  }
  throw new Error(`${label} timed out${last ? `: ${last.message}` : ""}`);
}

class Cdp {
  constructor(url) {
    this.url = url;
    this.id = 0;
    this.pending = new Map();
  }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const row = JSON.parse(event.data);
      if (!row.id) return;
      const pending = this.pending.get(row.id);
      if (!pending) return;
      this.pending.delete(row.id);
      row.error ? pending.reject(new Error(JSON.stringify(row.error))) : pending.resolve(row.result);
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
}

const chrome = spawn(
  chromium,
  [
    ...(headless ? ["--headless=new"] : []),
    "--disable-gpu",
    "--no-first-run",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--window-size=1600,1000",
    "about:blank",
  ],
  { env: chromeEnv, stdio: "ignore" },
);
let cdp;
try {
  const target = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    if (!response.ok) return null;
    return (await response.json()).find((row) => row.type === "page");
  }, 30_000, "Chrome target");
  cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.connect();
  await Promise.all([cdp.send("Page.enable"), cdp.send("Runtime.enable")]);
  const evalJs = async (expression) => {
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result?.value;
  };
  await cdp.send("Page.navigate", { url: frontend });
  await waitFor(() => evalJs("document.readyState === 'complete'"), 30_000, "page load");
  await evalJs(`
    localStorage.setItem("jw-config", JSON.stringify({deploymentUrl:${JSON.stringify(backend)},assistantId:"JW"}));
    localStorage.setItem("jw-auto-approve", JSON.stringify({${JSON.stringify(threadId)}:true}));
    location.href = ${JSON.stringify(`${frontend}?threadId=${threadId}`)};
    true;
  `);
  await waitFor(() => evalJs("Boolean(document.querySelector('textarea'))"), 90_000, "composer");
  // The composer renders before useStream has hydrated the restored thread.
  // Sending in that window makes the WebUI pass checkpoint_id=null and the
  // backend correctly rejects the request.  A restored task always has prior
  // transcript text; wait for it and then give the stream state one bounded
  // settle interval before clicking Send.
  await waitFor(
    () => evalJs("document.body.innerText.trim().length > 200"),
    30_000,
    "restored transcript",
  );
  await delay(5_000);
  const SEND_BTN = 'button[aria-label="发送消息"]';
  if (requestedModel) {
    await evalJs("document.querySelector('textarea').focus(); true;");
    await cdp.send("Input.insertText", {
      text: `/model ${requestedModel} ${requestedProvider}`,
    });
    await waitFor(
      () => evalJs(`Boolean(document.querySelector('${SEND_BTN}:not([disabled])'))`),
      10_000,
      "enabled Send button for model command",
    );
    await evalJs(`document.querySelector('${SEND_BTN}').click(); true;`);
    await waitFor(
      () => evalJs("document.querySelector('textarea').value === ''"),
      10_000,
      "model command composer reset",
    );
    await waitFor(
      () => evalJs(`document.body.innerText.includes(${JSON.stringify(requestedModel)})`),
      15_000,
      "thread model override",
    );
  }
  const priorRunsResponse = await fetch(`${backend}/threads/${threadId}/runs`);
  if (!priorRunsResponse.ok) {
    throw new Error(`existing runs unavailable: HTTP ${priorRunsResponse.status}`);
  }
  const priorRunIds = new Set(
    (await priorRunsResponse.json()).map((row) => row.run_id).filter(Boolean),
  );
  await evalJs("document.querySelector('textarea').focus(); true;");
  await cdp.send("Input.insertText", { text: continuation });
  await waitFor(
    () => evalJs(`Boolean(document.querySelector('${SEND_BTN}:not([disabled])'))`),
    10_000,
    "send button",
  );
  await evalJs(`document.querySelector('${SEND_BTN}').click(); true;`);
  await waitFor(
    () => evalJs("document.querySelector('textarea').value === ''"),
    10_000,
    "composer reset after continuation",
  );
  const run = await waitFor(async () => {
    const response = await fetch(`${backend}/threads/${threadId}/runs`);
    if (!response.ok) return null;
    const rows = await response.json();
    return rows.find((row) => row.run_id && !priorRunIds.has(row.run_id));
  }, 30_000, "resumed backend run");
  process.stdout.write(`${JSON.stringify({
    thread_id: threadId,
    run_id: run.run_id,
    submitted_via: "production_webui",
    automation_browser_mode: headless ? "headless" : "headed",
    observer_url: observerUrl.toString(),
    model: requestedModel || null,
    provider: requestedProvider || null,
  })}\n`);
} finally {
  try { await cdp?.send("Browser.close"); } catch {}
  chrome.kill("SIGTERM");
  await delay(300);
  await rm(profile, { recursive: true, force: true });
}
