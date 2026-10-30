/** Measure the real packaged Windows executable, with isolated user data.
 *
 * startup uses synthetic credentials and makes no model requests.
 * configure lets the user enter credentials in the application's own UI.
 * tasks uses that profile and sends two bounded, synthetic test prompts.
 * No credential files, request headers or historical conversations are read.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { parseArgs } from "node:util";

const require = createRequire(
  new URL("../../webui/package.json", import.meta.url)
);
const { _electron: electron } = require("playwright");
const { values } = parseArgs({
  options: {
    exe: { type: "string" },
    output: { type: "string" },
    profile: { type: "string" },
    mode: { type: "string", default: "startup" },
    samples: { type: "string", default: "3" },
  },
});
if (
  !values.exe ||
  !values.output ||
  !["startup", "configure", "tasks"].includes(values.mode)
) {
  throw new Error(
    "Usage: --exe <Jinwu.exe> --output <directory> --mode startup|configure|tasks [--samples 3]"
  );
}
const executablePath = path.resolve(values.exe);
const output = path.resolve(values.output);
fs.mkdirSync(output, { recursive: true });
if (values.profile && values.mode === "startup")
  throw new Error("startup must use an isolated synthetic profile");
const profile = values.profile
  ? path.resolve(values.profile)
  : path.join(
      output,
      values.mode === "startup" ? "startup-profile" : "live-profile"
    );
fs.mkdirSync(profile, { recursive: true });
const env = Object.fromEntries(
  ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"]
    .filter((k) => process.env[k])
    .map((k) => [k, process.env[k]])
);
Object.assign(env, {
  USERPROFILE: path.join(profile, "home"),
  HOME: path.join(profile, "home"),
  LOCALAPPDATA: path.join(profile, "local"),
  APPDATA: path.join(profile, "roaming"),
  PYTHONUTF8: "1",
  PYTHON_DOTENV_DISABLED: "1",
  LANGSMITH_TRACING: "false",
  LANGCHAIN_TRACING_V2: "false",
  JW_KB_AUTO_GROWTH: "0",
  JW_AGENT_MODEL_CALL_LIMIT: "12",
  JW_SUBAGENT_MODEL_CALL_LIMIT: "6",
});
for (const directory of [
  env.USERPROFILE,
  env.LOCALAPPDATA,
  env.APPDATA,
  path.join(env.USERPROFILE, "Documents"),
])
  fs.mkdirSync(directory, { recursive: true });
if (values.mode === "startup") {
  const config = path.join(env.LOCALAPPDATA, "Jinwu/config/jw");
  fs.mkdirSync(config, { recursive: true });
  fs.writeFileSync(
    path.join(config, "config.yaml"),
    "provider: dashscope\nmodel: qwen3.7-plus\ndashscope_api_key: synthetic-startup-only\ndashscope_proxy_mode: direct\n"
  );
  env.DASHSCOPE_BASE_URL = "http://127.0.0.1:9/v1";
}
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(probe, timeout = 120000) {
  const started = performance.now();
  while (performance.now() - started < timeout) {
    const value = await probe();
    if (value) return value;
    await delay(50);
  }
  throw new Error(`Timed out after ${timeout}ms`);
}
async function closed(url) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(500) });
    return false;
  } catch {
    return true;
  }
}
const samples = Number(values.samples);
if (!Number.isInteger(samples) || samples < 1 || samples > 10)
  throw new Error("samples must be 1..10");
const reports = [];
for (
  let index = 0;
  index < (values.mode === "startup" ? samples : 1);
  index++
) {
  const report = {
    index,
    mode: values.mode,
    startedAt: new Date().toISOString(),
  };
  let app;
  let frontend;
  let backend;
  try {
    const started = performance.now();
    app = await electron.launch({ executablePath, env, timeout: 120000 });
    report.packaged = await app.evaluate(({ app }) => app.isPackaged);
    if (!report.packaged) throw new Error("A packaged executable is required");
    console.log(
      JSON.stringify({ phase: "window-open", mode: values.mode, index })
    );
    const page = await until(
      () =>
        app
          .windows()
          .find((page) => page.url().startsWith("http://127.0.0.1:")),
      values.mode === "configure" ? 1800000 : 120000
    );
    frontend = new URL(page.url()).origin;
    report.frontendMs = performance.now() - started;
    const config = await (await fetch(`${frontend}/api/jw-config`)).json();
    backend = config.deploymentUrl;
    await until(async () => {
      let response;
      try {
        response = await fetch(`${backend}/api/runtime-status`);
      } catch (error) {
        if (error.cause?.code === "ECONNREFUSED") return false;
        throw error;
      }
      if (!response.ok)
        throw new Error(`Runtime status returned HTTP ${response.status}`);
      const state = await response.json();
      if (state.agent?.state === "failed")
        throw new Error("Agent initialization failed");
      return state.canSend;
    });
    report.backendReadyMs = performance.now() - started;
    const composer = page.getByRole("textbox", { name: "消息", exact: true });
    await composer.fill("启动可用检查（不发送）");
    await page.getByRole("button", { name: "发送消息", exact: true }).waitFor();
    await until(() =>
      page.getByRole("button", { name: "发送消息", exact: true }).isEnabled()
    );
    report.startupUsableMs = performance.now() - started;
    await composer.fill("");
    if (values.mode === "configure") {
      report.configurationReady = true;
    }
    if (values.mode === "tasks") {
      report.tasks = [];
      const scenarios = [
        {
          name: "first-reply",
          prompt:
            "这是客户端响应计时测试。请只回复“计时就绪”，不要调用任何工具。",
          timeout: 180000,
        },
        {
          name: "multi-step",
          prompt:
            '这是本地客户端的多步工具性能测试，无需检索资料、科研分析或后台委派。请依次完成三步：1. 用 write_file 在当前工作区创建 perf-result.json，内容为 {"numbers":[1,2,3],"total":0}；2. 用 edit_file 把该文件中的 "total":0 替换为 "total":6；3. 用 read_file 读取该文件并核对 numbers 仍为 [1,2,3] 且 total 为 6。请实际依次使用这三个工具，最后只回复“多步计时完成，total=6”。',
          timeout: 360000,
        },
      ];
      for (const scenario of scenarios) {
        const expandNavigation = page.getByRole("button", {
          name: "展开研究导航",
          exact: true,
        });
        if (await expandNavigation.isVisible()) await expandNavigation.click();
        await page
          .getByRole("button", { name: "开始新实验", exact: true })
          .click();
        await composer.fill(scenario.prompt);
        await page.evaluate(() => {
          const oldIds = new Set(
            [
              ...document.querySelectorAll('[data-message-role="assistant"]'),
            ].map((e) => e.dataset.messageId)
          );
          const timing = (window.__jwClientTiming = {
            submitted: null,
            firstVisible: null,
            completed: null,
            sawRunning: false,
          });
          document.addEventListener(
            "click",
            function start(event) {
              if (
                event.target.closest("button")?.getAttribute("aria-label") ===
                "发送消息"
              ) {
                timing.submitted = performance.now();
                document.removeEventListener("click", start, true);
              }
            },
            true
          );
          const observer = new MutationObserver(() => {
            if (timing.submitted === null) return;
            const running = !!document.querySelector(
              'button[aria-label="停止生成"]'
            );
            timing.sawRunning ||= running;
            const text = [
              ...document.querySelectorAll('[data-message-role="assistant"]'),
            ]
              .filter((e) => !oldIds.has(e.dataset.messageId))
              .map(
                (e) =>
                  e
                    .querySelector("[data-message-content]")
                    ?.textContent?.trim() || ""
              )
              .filter(Boolean);
            if (text.length && timing.firstVisible === null)
              timing.firstVisible = performance.now();
            if (
              timing.sawRunning &&
              !running &&
              document.querySelector('button[aria-label="发送消息"]')
            ) {
              timing.completed = performance.now();
              observer.disconnect();
            }
          });
          observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            characterData: true,
          });
        });
        await page
          .getByRole("button", { name: "发送消息", exact: true })
          .click();
        await page.waitForFunction(
          () => window.__jwClientTiming?.completed != null,
          null,
          { timeout: scenario.timeout }
        );
        const timing = await page.evaluate(() => window.__jwClientTiming);
        const threadId = new URL(page.url()).searchParams.get("threadId");
        if (!threadId) throw new Error("Missing measured thread id");
        const state = await (
          await fetch(`${backend}/threads/${threadId}/state`)
        ).json();
        const messages = state.values.messages;
        const toolMessages = messages.filter((m) => m.type === "tool");
        const answer = messages.filter((m) => m.type === "ai").at(-1)?.content;
        const passed =
          scenario.name === "first-reply"
            ? typeof answer === "string" && answer.includes("计时就绪")
            : typeof answer === "string" &&
              answer.includes("多步计时完成") &&
              ["write_file", "edit_file", "read_file"].every((name) =>
                toolMessages.some((m) => m.name === name && m.status !== "error")
              ) &&
              answer.includes("total=6") &&
              toolMessages.some(
                (m) =>
                  m.name === "read_file" &&
                  /"total"\s*:\s*6/.test(
                    typeof m.content === "string"
                      ? m.content
                      : JSON.stringify(m.content)
                  )
              );
        report.tasks.push({
          name: scenario.name,
          threadId,
          firstVisibleReplyMs:
            timing.firstVisible === null
              ? null
              : timing.firstVisible - timing.submitted,
          completedMs: timing.completed - timing.submitted,
          toolCalls: toolMessages.map((m) => m.name),
          toolErrors: toolMessages
            .filter((m) => m.status === "error")
            .map((m) => m.name),
          argumentCorrections: toolMessages
            .filter((m) => m.additional_kwargs?.argument_aliases)
            .map((m) => ({
              name: m.name,
              aliases: m.additional_kwargs.argument_aliases,
            })),
          passed,
          answer,
        });
        await page.bringToFront();
        await page.screenshot({
          path: path.join(output, `${scenario.name}.png`),
          fullPage: false,
        });
        if (!passed)
          throw new Error(
            `Task did not satisfy ${scenario.name} acceptance criteria`
          );
      }
    }
    await page.bringToFront();
    await page.screenshot({
      path: path.join(output, `${values.mode}-${index}.png`),
    });
    report.ok = true;
  } catch (error) {
    report.ok = false;
    report.error = error.message;
    process.exitCode = 1;
  } finally {
    if (app) await app.close();
    if (frontend && backend)
      report.portsClosed = await until(
        async () => (await closed(frontend)) && (await closed(`${backend}/ok`)),
        30000
      );
    reports.push(report);
    fs.writeFileSync(
      path.join(output, `${values.mode}-timings.json`),
      JSON.stringify(reports, null, 2)
    );
    console.log(JSON.stringify(report));
  }
  if (!report.ok) break;
}
