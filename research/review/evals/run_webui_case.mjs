// Real-WebUI evaluation harness for Research Review 2.0 (SC26-B01..B06 and
// FR-H01..H12). Every case is driven through the real browser: model pick,
// prompt entry, interrupt/approval handling, and resume all happen on the
// page. Scoring reads ONLY the structured status API and task artifacts —
// never DOM text — per the harness contract.
//
//   node run_webui_case.mjs <case-id> [run-label] [provider] [model]
//
// Env:
//   JW_EVAL_SUITE      path to the case JSON (default sc26_core_e2e_v1.json)
//   JW_EVAL_FRONTEND   default http://127.0.0.1:4717/
//   JW_EVAL_BACKEND    default http://127.0.0.1:6174
//   JW_EVAL_MODEL / JW_EVAL_PROVIDER   default qwen3.8-max / custom-openai
//   JW_EVAL_PROFILE    Chrome user-data-dir; default a fresh mktemp profile
//   JW_EVAL_SUBMIT_ONLY=1   stop after the run is accepted by the backend
//
// The harness never persists auth material, full network bodies, or browser
// credentials. Chrome runs headless on a throwaway profile that is deleted
// after the run unless JW_EVAL_KEEP_PROFILE=1.

import { spawn } from "node:child_process";
import {
  access,
  mkdtemp,
  mkdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { classifyOutcome } from "./terminal_outcome.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_SUITE = path.join(HERE, "sc26_core_e2e_v1.json");

const [caseId, requestedRunLabel, requestedProvider, requestedModel] =
  process.argv.slice(2);
if (!caseId) {
  throw new Error(
    "Usage: node run_webui_case.mjs <case-id> [run-label] [provider] [model]",
  );
}

const rawFrontendUrl = process.env.JW_EVAL_FRONTEND || "http://127.0.0.1:4717/";
const frontendUrl = rawFrontendUrl.endsWith("/")
  ? rawFrontendUrl
  : `${rawFrontendUrl}/`;
const backendUrl = process.env.JW_EVAL_BACKEND || "http://127.0.0.1:6174";
const modelName = requestedModel || process.env.JW_EVAL_MODEL || "qwen3.8-max";
const modelProvider =
  requestedProvider || process.env.JW_EVAL_PROVIDER || "custom-openai";
const runLabel = requestedRunLabel || caseId;
if (!/^[A-Za-z0-9_.-]+$/.test(runLabel)) {
  throw new Error(`Invalid run label: ${runLabel}`);
}
const submitOnly = process.env.JW_EVAL_SUBMIT_ONLY === "1";
const stopStage = process.env.JW_EVAL_STOP_STAGE || "";
if (stopStage && !["planning", "evidence"].includes(stopStage)) {
  throw new Error(`Unsupported JW_EVAL_STOP_STAGE: ${stopStage}`);
}

async function loadSuite(suitePath) {
  const document = JSON.parse(await readFile(suitePath, "utf8"));
  if (!Array.isArray(document.source_suites)) return document;
  const cases = [];
  const seen = new Set();
  for (const source of document.source_suites) {
    const sourcePath = path.resolve(path.dirname(suitePath), String(source));
    const sourceDocument = JSON.parse(await readFile(sourcePath, "utf8"));
    for (const entry of sourceDocument.cases || []) {
      if (seen.has(entry.id)) throw new Error(`Duplicate case ID: ${entry.id}`);
      seen.add(entry.id);
      const overrides = document.case_overrides?.[entry.id] || {};
      cases.push({ ...(document.defaults || {}), ...entry, ...overrides });
    }
  }
  return { ...document, cases };
}

const suiteFile = path.resolve(process.env.JW_EVAL_SUITE || DEFAULT_SUITE);
const suite = await loadSuite(suiteFile);
const selectedCase = (suite.cases || []).find((entry) => entry.id === caseId);
if (!selectedCase) throw new Error(`Unknown case ID: ${caseId}`);
if (suite.schema_version === "research-review-visible-suite-v2") {
  for (const field of [
    "prompt",
    "input_files",
    "review_mode",
    "reviewer_model",
    "expected_outcome",
    "repetitions",
  ]) {
    if (selectedCase[field] === undefined) {
      throw new Error(`Case ${caseId} is missing required v2 field: ${field}`);
    }
  }
}
const prompt = String(selectedCase.prompt || "").trim();
if (!prompt) throw new Error(`Case ${caseId} has an empty prompt`);
const reviewMode = (
  process.env.JW_EVIDENCE_REVIEW_MODE ||
  selectedCase.review_mode ||
  "two_pass"
)
  .trim()
  .toLowerCase();
if (!["closed", "two_pass"].includes(reviewMode)) {
  throw new Error(`Unsupported Evidence review mode: ${reviewMode}`);
}
const reviewerSpec = selectedCase.reviewer_model || {};
const reviewerModel =
  process.env.JW_EVAL_REVIEWER_MODEL || reviewerSpec.model || "qwen3.8-max";
const reviewerProvider =
  process.env.JW_EVAL_REVIEWER_PROVIDER ||
  reviewerSpec.provider ||
  "custom-openai";
const inputFiles = (selectedCase.input_files || []).map((value) =>
  path.resolve(path.dirname(suiteFile), String(value)),
);
for (const inputFile of inputFiles) await access(inputFile);

function modelFamily(model) {
  const value = String(model || "")
    .toLowerCase()
    .split("/")
    .at(-1);
  for (const [prefixes, family] of [
    [["qwen", "qwq"], "qwen"],
    [["deepseek"], "deepseek"],
    [["kimi", "moonshot"], "kimi"],
    [["gpt", "o1", "o3", "o4", "codex"], "openai"],
    [["claude"], "claude"],
    [["gemini"], "gemini"],
  ]) {
    if (prefixes.some((prefix) => value.startsWith(prefix))) return family;
  }
  return value.split(/[-_:]/, 1)[0] || "unknown";
}

const producerModel = process.env.JW_EVAL_PRODUCER_MODEL || modelName;
const producerProvider = process.env.JW_EVAL_PRODUCER_PROVIDER || modelProvider;
const controllerFamily = modelFamily(modelName);
const generatorFamily =
  process.env.JW_EVAL_PRODUCER_FAMILY || modelFamily(producerModel);
const reviewerFamily =
  process.env.JW_EVAL_REVIEWER_FAMILY || modelFamily(reviewerModel);
const heterogeneous = process.env.JW_EVAL_HETEROGENEOUS
  ? process.env.JW_EVAL_HETEROGENEOUS === "1"
  : reviewerFamily !== generatorFamily;
const humanReviewRequired = process.env.JW_EVAL_HUMAN_REVIEW_REQUIRED
  ? process.env.JW_EVAL_HUMAN_REVIEW_REQUIRED === "1"
  : !heterogeneous;

const onWindows = process.platform === "win32";
// Under WSL the Windows Chrome at /mnt/c binds its DevTools port on the
// Windows side, which WSL2's localhost forwarding cannot reach (confirmed:
// ws://127.0.0.1:<port> unreachable on both IPv4 and IPv6). Prefer the
// WSL-native Playwright Chromium, which binds to a WSL-reachable interface.
// JW_EVAL_CHROME overrides the choice entirely.
const WSL_CHROMIUM =
  "/home/zzz/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome";
const chromePath =
  process.env.JW_EVAL_CHROME ||
  (onWindows
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : WSL_CHROMIUM);
const debugPort = Number(process.env.JW_EVAL_DEBUG_PORT || 9227);
if (!Number.isInteger(debugPort) || debugPort < 1024 || debugPort > 65535) {
  throw new Error(`Invalid JW_EVAL_DEBUG_PORT: ${debugPort}`);
}
const outputDir = path.resolve(HERE, "runs", runLabel);
await mkdir(outputDir, { recursive: true });

// Fresh throwaway Chrome profile per run (goal: new profile per case).
const ownsChromeProfile = !process.env.JW_EVAL_PROFILE;
const chromeProfile = ownsChromeProfile
  ? await mkdtemp(path.join(os.tmpdir(), "jw-eval-profile-"))
  : process.env.JW_EVAL_PROFILE;
const timeoutMs = Number(process.env.JW_EVAL_TIMEOUT_MS || 7_200_000);

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runProcess(command, args) {
  const child = spawn(command, args, {
    cwd: path.resolve(HERE, "../../.."),
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stdout = [];
  const stderr = [];
  child.stdout.on("data", (chunk) => stdout.push(chunk));
  child.stderr.on("data", (chunk) => stderr.push(chunk));
  const exitCode = await new Promise((resolve) => child.once("exit", resolve));
  if (exitCode !== 0) {
    throw new Error(
      `Probe setup failed (${exitCode}): ${Buffer.concat(stderr).toString("utf8").slice(-2000)}`,
    );
  }
  return Buffer.concat(stdout).toString("utf8").trim();
}

async function waitFor(fn, timeoutMs, label, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(intervalMs);
  }
  throw new Error(
    `Timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ""}`,
  );
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} from ${url}`);
  return response.json();
}

// Read-only status + artifact accessors. These hit the WebUI server API which
// canonicalizes and contains every path; we never read arbitrary disk paths.
async function readReviewStatus(threadId) {
  try {
    return await fetchJson(
      `${frontendUrl}api/research-review/status?threadId=${encodeURIComponent(
        threadId,
      )}`,
    );
  } catch (error) {
    return { active: false, error: String(error?.message || error) };
  }
}

async function listWorkspace(threadId, relPath) {
  try {
    const doc = await fetchJson(
      `${frontendUrl}api/workspace?threadId=${encodeURIComponent(
        threadId,
      )}&path=${encodeURIComponent(relPath)}`,
    );
    return Array.isArray(doc.entries) ? doc.entries : [];
  } catch {
    return [];
  }
}

async function readWorkspaceJson(threadId, relPath) {
  try {
    const response = await fetch(
      `${frontendUrl}api/workspace/file?threadId=${encodeURIComponent(
        threadId,
      )}&path=${encodeURIComponent(relPath)}`,
    );
    if (!response.ok) return null;
    const value = await response.json();
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

async function collectAssessments(threadId) {
  const entries = await listWorkspace(threadId, "research_review/assessments");
  const rows = [];
  for (const entry of entries) {
    if (entry.type !== "file" || !entry.name.endsWith(".json")) continue;
    const doc = await readWorkspaceJson(
      threadId,
      `research_review/assessments/${entry.name}`,
    );
    if (doc) rows.push(doc);
  }
  rows.sort((a, b) =>
    String(a.assessment_id).localeCompare(String(b.assessment_id)),
  );
  return rows;
}

async function collectPlannerPlans(threadId) {
  const entries = await listWorkspace(threadId, "planner/runs");
  const rows = [];
  for (const entry of entries) {
    if (!["directory", "dir"].includes(entry.type)) continue;
    const relPath = `planner/runs/${entry.name}/research_plan.json`;
    const plan = await readWorkspaceJson(threadId, relPath);
    if (plan) rows.push({ rel_path: relPath, plan });
  }
  return rows;
}

function assistantAnswers(state) {
  const messages = state?.values?.messages || [];
  return messages
    .filter((message) => message?.type === "ai")
    .map((message) => ({
      id: message.id || null,
      content: message.content,
      response_metadata: message.response_metadata || {},
      usage_metadata: message.usage_metadata || null,
    }));
}

function observedUsage(state) {
  const total = { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  for (const message of state?.values?.messages || []) {
    const usage =
      message?.usage_metadata ||
      message?.response_metadata?.usage_metadata ||
      message?.response_metadata?.token_usage;
    if (!usage || typeof usage !== "object") continue;
    for (const field of Object.keys(total)) {
      const value = Number(usage[field] || 0);
      if (Number.isFinite(value) && value >= 0) total[field] += value;
    }
  }
  return total;
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
  }
  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener(
        "error",
        () => reject(new Error("CDP WebSocket connection failed.")),
        { once: true },
      );
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) {
          pending.reject(new Error(JSON.stringify(message.error)));
        } else {
          pending.resolve(message.result);
        }
        return;
      }
      this.events.push(message);
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  close() {
    this.socket?.close();
  }
}

const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${chromeProfile}`,
    "--window-size=1600,1000",
    "about:blank",
  ],
  { stdio: ["ignore", "pipe", "pipe"] },
);
const chromeStdout = [];
const chromeStderr = [];
chrome.stdout.on("data", (chunk) => chromeStdout.push(chunk));
chrome.stderr.on("data", (chunk) => chromeStderr.push(chunk));

let cdp;
const startedAt = new Date().toISOString();
const startedMs = Date.now();
try {
  const target = await waitFor(
    async () => {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/list`, {
        signal: AbortSignal.timeout(2_000),
      });
      if (!response.ok) return null;
      const targets = await response.json();
      return targets.find(
        (entry) => entry.type === "page" && entry.webSocketDebuggerUrl,
      );
    },
    30_000,
    "Chrome DevTools target",
  );

  cdp = new CdpClient(target.webSocketDebuggerUrl);
  await cdp.connect();
  await Promise.all([
    cdp.send("Page.enable"),
    cdp.send("Runtime.enable"),
    cdp.send("Log.enable"),
    cdp.send("Network.enable"),
    cdp.send("DOM.enable"),
  ]);

  const evaluate = async (expression) => {
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(JSON.stringify(result.exceptionDetails));
    }
    return result.result?.value;
  };

  await cdp.send("Page.navigate", { url: frontendUrl });
  await waitFor(
    () => evaluate("document.readyState === 'complete'"),
    30_000,
    "initial frontend load",
  );
  await evaluate(`
    localStorage.setItem(
      "jw-config",
      JSON.stringify({
        deploymentUrl: ${JSON.stringify(backendUrl)},
        assistantId: "JW"
      })
    );
    localStorage.setItem("jw-auto-approve", JSON.stringify({ "__new__": true }));
    location.reload();
    true;
  `);
  await waitFor(
    () => evaluate("Boolean(document.querySelector('textarea'))"),
    90_000,
    "hydrated chat composer",
  );

  // Fail before submission when the production WebUI cannot reach the live
  // backend. A task workspace cannot be checked until the first message has
  // created its thread and binding, so that check happens immediately below.
  await waitFor(
    () => fetchJson(`${backendUrl}/api/models`),
    120_000,
    "live backend model registry",
    1_000,
  );

  // Model selection via the page command. The send button's aria-label is the
  // localized "发送消息" (see ChatInterface.tsx); there is no English label.
  const SEND_BTN = 'button[aria-label="发送消息"]';
  await evaluate(`document.querySelector("textarea").focus(); true;`);
  await cdp.send("Input.insertText", {
    text: `/model ${modelName} ${modelProvider}`,
  });
  await waitFor(
    () =>
      evaluate(`(() => {
        const b = document.querySelector('${SEND_BTN}');
        return Boolean(b && !b.disabled);
      })()`),
    10_000,
    "enabled Send button for model command",
  );
  await evaluate(`document.querySelector('${SEND_BTN}').click(); true;`);
  await waitFor(
    () =>
      evaluate(
        `document.body.innerText.includes(${JSON.stringify(modelName)})`,
      ),
    15_000,
    "thread model override",
  );
  await waitFor(
    () => evaluate(`document.querySelector("textarea").value === ""`),
    10_000,
    "model command composer reset",
  );

  let threadId = null;
  if (inputFiles.length > 0) {
    const documentNode = await cdp.send("DOM.getDocument", {
      depth: -1,
      pierce: true,
    });
    const inputNode = await cdp.send("DOM.querySelector", {
      nodeId: documentNode.root.nodeId,
      selector: 'input[type="file"]',
    });
    if (!inputNode.nodeId)
      throw new Error("Workspace upload input was not found");
    await cdp.send("DOM.setFileInputFiles", {
      nodeId: inputNode.nodeId,
      files: inputFiles,
    });
    const uploadedNames = inputFiles.map((value) => path.basename(value));
    threadId = await waitFor(
      () =>
        evaluate(`(() => {
          const input = document.querySelector('input[type="file"]');
          const names = ${JSON.stringify(uploadedNames)};
          const uploaded = names.every((name) => document.body.innerText.includes(name));
          const id = new URL(location.href).searchParams.get("threadId");
          return uploaded && input && !input.disabled && id ? id : null;
        })()`),
      90_000,
      "workspace file upload",
    );
  }

  let probeSeed = null;
  if (selectedCase.probe_artifact_stage) {
    if (selectedCase.probe_artifact_stage !== "data") {
      throw new Error(
        `Unsupported probe_artifact_stage: ${selectedCase.probe_artifact_stage}`,
      );
    }
    if (!threadId || inputFiles.length === 0) {
      throw new Error("Focused Evidence probes require an uploaded task input");
    }
    const rawSeed = await runProcess(".venv/bin/python", [
      path.join(HERE, "seed_evidence_probe.py"),
      threadId,
      caseId,
      ...inputFiles.map((value) => path.basename(value)),
    ]);
    probeSeed = JSON.parse(rawSeed);
  }

  // Prompt entry via the page.
  await evaluate(`document.querySelector("textarea").focus(); true;`);
  await cdp.send("Input.insertText", { text: prompt });
  await waitFor(
    () =>
      evaluate(`(() => {
        const b = document.querySelector('${SEND_BTN}');
        return Boolean(b && !b.disabled);
      })()`),
    10_000,
    "enabled Send button after prompt entry",
  );
  await evaluate(`document.querySelector('${SEND_BTN}').click(); true;`);
  threadId ||= await waitFor(
    () => evaluate(`new URL(location.href).searchParams.get("threadId")`),
    30_000,
    "frontend thread id",
  );

  // Do not enter the potentially long research poll until the frontend and
  // backend agree on this new thread's task-scoped workspace.
  await waitFor(
    () =>
      fetchJson(
        `${frontendUrl}api/workspace?threadId=${encodeURIComponent(
          threadId,
        )}&path=`,
      ),
    30_000,
    "task workspace binding",
  );

  if (submitOnly) {
    const runs = await waitFor(
      async () => {
        const rows = await fetchJson(`${backendUrl}/threads/${threadId}/runs`);
        return Array.isArray(rows) && rows.length > 0 ? rows : null;
      },
      30_000,
      "submitted LangGraph run",
    );
    const submission = {
      schema_version: "webui-eval-submission-v1",
      case_id: caseId,
      run_label: runLabel,
      submitted_at: new Date().toISOString(),
      thread_id: threadId,
      run_id: runs[0]?.run_id ?? null,
    };
    await writeFile(
      path.join(outputDir, "submission.json"),
      `${JSON.stringify(submission, null, 2)}\n`,
      "utf8",
    );
    process.stdout.write(`${JSON.stringify(submission)}\n`);
    try {
      await cdp.send("Browser.close");
    } catch {}
    cdp.close();
    cdp = null;
    chrome.kill("SIGTERM");
    await delay(500);
    if (ownsChromeProfile && process.env.JW_EVAL_KEEP_PROFILE !== "1") {
      await rm(chromeProfile, { recursive: true, force: true }).catch(() => {});
    }
    process.exit(0);
  }

  // Interrupt/approval handling + terminal detection. Terminal state and the
  // scientific status come from the backend/state APIs, never from DOM text.
  let approvalCount = 0;
  let approvalVisibleSince = null;
  let stageStop = null;
  let stageStopRequested = false;
  const terminal = await waitFor(
    async () => {
      const [thread, state, runs] = await Promise.all([
        fetchJson(`${backendUrl}/threads/${threadId}`),
        fetchJson(`${backendUrl}/threads/${threadId}/state`),
        fetchJson(`${backendUrl}/threads/${threadId}/runs`),
      ]);
      const latestRun = Array.isArray(runs) && runs.length > 0 ? runs[0] : null;
      const evidence = classifyOutcome(thread, state, latestRun);

      if (stopStage === "planning" && !stageStop) {
        const [plans, status] = await Promise.all([
          collectPlannerPlans(threadId),
          readReviewStatus(threadId),
        ]);
        const frozen = plans.find((row) => row.plan?.status === "frozen");
        if (frozen) {
          stageStop = {
            outcome: "planning_frozen",
            terminal_status: "stage_complete",
            has_answer: evidence.has_answer,
            assistant_answer_count: evidence.assistant_answer_count,
            error_summary: null,
            planner_plan_path: frozen.rel_path,
          };
        } else if (
          status?.terminal?.stage === "planning" ||
          (status?.currentStage === "planning" &&
            ["blocked", "human_review"].includes(status?.status))
        ) {
          stageStop = {
            outcome: "planning_blocked",
            terminal_status: status.status || "blocked",
            has_answer: evidence.has_answer,
            assistant_answer_count: evidence.assistant_answer_count,
            error_summary:
              status?.terminal?.reasonCode || "Planning did not freeze",
            planner_plan_path: null,
          };
        } else if (
          latestRun &&
          !["busy", "pending", "running", "queued"].includes(thread.status) &&
          !["pending", "running", "queued"].includes(latestRun.status)
        ) {
          const messages = state?.values?.messages || [];
          const failureMessage = [...messages].reverse().find((message) => {
            const content = String(message?.content || "");
            return (
              content.includes("[RESEARCH REVIEW TERMINAL]") ||
              content.includes("[RESEARCH REVIEW TOOL FAILURE STOP]") ||
              content.includes("[RESEARCH REVIEW BLOCKED]")
            );
          });
          stageStop = {
            outcome: "planning_blocked",
            terminal_status: latestRun.status || "blocked",
            has_answer: false,
            assistant_answer_count: evidence.assistant_answer_count,
            error_summary:
              String(failureMessage?.content || evidence.error_summary || "")
                .replace(/\s+/g, " ")
                .slice(0, 1000) || "Planning terminated without a frozen plan",
            planner_plan_path: null,
          };
        }
      }

      if (stopStage === "evidence" && !stageStop) {
        const [status, assessments] = await Promise.all([
          readReviewStatus(threadId),
          collectAssessments(threadId),
        ]);
        const reviewedStage = (status?.stages || []).find(
          (stage) => stage?.decision && Number(stage?.round || 0) >= 1,
        );
        if (
          Number(status?.reviewInvocations || 0) >= 1 &&
          assessments.length >= 1 &&
          reviewedStage
        ) {
          stageStop = {
            outcome: "evidence_reviewed",
            terminal_status: "stage_complete",
            has_answer: evidence.has_answer,
            assistant_answer_count: evidence.assistant_answer_count,
            error_summary: null,
            reviewed_stage: reviewedStage.stage,
          };
        }
      }

      if (stageStop) {
        const activeStatuses = ["busy", "pending", "running", "queued"];
        const threadIsActive = activeStatuses.includes(thread.status);
        const runIsActive = activeStatuses.includes(latestRun?.status);
        if ((threadIsActive || runIsActive) && !stageStopRequested) {
          const stopped = await evaluate(`(() => {
            const button = document.querySelector('button[aria-label="停止生成"]');
            if (button && !button.disabled) button.click();
            return Boolean(button && !button.disabled);
          })()`);
          stageStopRequested = stopped;
          return null;
        }
        // A thread can briefly stop reporting "busy" before the server-side
        // run cancellation has settled. Do not start the next nominally
        // serial case until both surfaces are terminal.
        if (!threadIsActive && !runIsActive) {
          return { thread, state, runs, latestRun, evidence: stageStop };
        }
        return null;
      }

      if (evidence.outcome === "interrupted_approval") {
        const approvalState = await evaluate(`
          (() => {
            const button = [...document.querySelectorAll("button")]
              .find((e) => ["批准", "Approve"].includes(e.textContent.trim()));
            return { visible: Boolean(button) };
          })()
        `);
        if (approvalCount === 0 && approvalState.visible) {
          await evaluate(`
            (() => {
              const b = [...document.querySelectorAll("button")]
                .find((e) => ["批准", "Approve"].includes(e.textContent.trim()));
              if (b) b.click();
              return Boolean(b);
            })();
          `);
          approvalCount += 1;
          approvalVisibleSince = null;
          await delay(1_000);
          return null;
        }
        approvalVisibleSince ??= Date.now();
        if (Date.now() - approvalVisibleSince >= 30_000) {
          return { thread, state, runs, latestRun, evidence };
        }
        return null;
      }

      if (
        [
          "completed_with_answer",
          "completed_without_answer",
          "provider_error",
          "runtime_error",
          "interrupted",
          "unknown_terminal_state",
        ].includes(evidence.outcome) &&
        !["busy", "pending", "running", "queued"].includes(thread.status)
      ) {
        return { thread, state, runs, latestRun, evidence };
      }
      return null;
    },
    timeoutMs,
    stopStage
      ? `${stopStage} stage outcome`
      : "terminal LangGraph thread state",
    1_000,
  );

  // Structured scientific status + assessment sidecars (read-only).
  const reviewStatus = await readReviewStatus(threadId);
  const assessments = await collectAssessments(threadId);
  const answers = assistantAnswers(terminal.state);
  const usage = observedUsage(terminal.state);

  await delay(1_000);
  const pageUrl = await evaluate("location.href");
  const screenshot = await cdp.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
  });

  const relevantEvents = cdp.events.filter((event) => {
    if (event.method === "Log.entryAdded") return true;
    if (event.method === "Runtime.consoleAPICalled") return true;
    if (event.method === "Network.loadingFailed") return true;
    if (event.method === "Network.responseReceived") {
      const url = event.params?.response?.url ?? "";
      return url.startsWith(backendUrl) || url.startsWith(frontendUrl);
    }
    return false;
  });

  const endedAt = new Date().toISOString();
  const terminalEvidence = terminal.evidence;
  const errorSignalText = JSON.stringify({
    thread: terminal.thread,
    state: terminal.state,
    reviewStatus,
  });
  const stageVerdicts = Array.isArray(reviewStatus.stages)
    ? reviewStatus.stages
        .filter((stage) => stage?.decision)
        .map((stage) => ({
          stage: stage.stage,
          decision: stage.decision,
          round: stage.round,
          issue_count: Array.isArray(stage.issues) ? stage.issues.length : 0,
        }))
    : [];
  const metadata = {
    schema_version: "webui-eval-run-v2",
    case_id: caseId,
    suite: path.basename(suiteFile),
    run_label: runLabel,
    started_at: startedAt,
    ended_at: endedAt,
    latency_seconds: (Date.now() - startedMs) / 1000,
    outcome: terminalEvidence.outcome,
    terminal_status: terminalEvidence.terminal_status,
    has_answer: terminalEvidence.has_answer,
    error_summary: terminalEvidence.error_summary,
    approval_count: approvalCount,
    thread_id: threadId,
    run_id:
      terminal.latestRun?.run_id ?? terminal.state?.metadata?.run_id ?? null,
    controller: {
      provider: modelProvider,
      model: modelName,
      family: controllerFamily,
    },
    generator: {
      provider: producerProvider,
      model: producerModel,
      family: generatorFamily,
    },
    reviewer: {
      provider: reviewerProvider,
      model: reviewerModel,
      family: reviewerFamily,
      heterogeneous,
      human_review_required: humanReviewRequired,
      review_mode: reviewMode,
    },
    input_files: inputFiles.map((value) => path.basename(value)),
    probe_seed: probeSeed,
    stop_stage: stopStage || null,
    planner_plan_path: terminalEvidence.planner_plan_path || null,
    scientific_status: reviewStatus.status ?? null,
    current_stage: reviewStatus.currentStage ?? null,
    stage_verdicts: stageVerdicts,
    assessment_count: assessments.length,
    evidence_review_invocations: Number(reviewStatus.reviewInvocations || 0),
    evidence_action_invocations: Number(reviewStatus.actionInvocations || 0),
    observed_usage: usage,
    error_signals: {
      provider_or_runtime_400:
        /(?:HTTP|status|code|error)[^\n]{0,40}\b400\b/i.test(errorSignalText),
      illegal_route:
        /illegal route|illegal transition|非法路由|非法.*转换/i.test(
          errorSignalText,
        ),
    },
    expected_outcome:
      selectedCase.expected_outcome ||
      selectedCase.expected_conclusion_class ||
      null,
    prompt_source: suiteFile,
    prompt_characters: prompt.length,
  };

  await Promise.all([
    writeFile(path.join(outputDir, "prompt.txt"), `${prompt}\n`, "utf8"),
    writeFile(
      path.join(outputDir, "metadata.json"),
      `${JSON.stringify(metadata, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "thread_terminal.json"),
      `${JSON.stringify(
        { thread: terminal.thread, state: terminal.state, runs: terminal.runs },
        null,
        2,
      )}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "review_status.json"),
      `${JSON.stringify(reviewStatus, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "assessments.json"),
      `${JSON.stringify(assessments, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "assistant_answers.json"),
      `${JSON.stringify(answers, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "network_console_events.json"),
      `${JSON.stringify(relevantEvents, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(outputDir, "screenshot.png"),
      Buffer.from(screenshot.data, "base64"),
    ),
    writeFile(
      path.join(outputDir, "chrome.stdout.log"),
      Buffer.concat(chromeStdout),
    ),
    writeFile(
      path.join(outputDir, "chrome.stderr.log"),
      Buffer.concat(chromeStderr),
    ),
  ]);

  process.stdout.write(
    `${JSON.stringify({
      case_id: caseId,
      run_label: runLabel,
      output_dir: outputDir,
      outcome: terminalEvidence.outcome,
      review_active: reviewStatus.active === true,
      run_status: reviewStatus.status ?? null,
      stages: Array.isArray(reviewStatus.stages)
        ? reviewStatus.stages.length
        : 0,
      assessments: assessments.length,
      review_mode: reviewMode,
      reviewer_family: reviewerFamily,
      latency_seconds: metadata.latency_seconds,
    })}\n`,
  );
  if (
    !["completed_with_answer", "planning_frozen", "evidence_reviewed"].includes(
      terminalEvidence.outcome,
    )
  ) {
    process.exitCode = 2;
  }
} catch (error) {
  const failure = {
    schema_version: "webui-eval-failure-v1",
    case_id: caseId,
    run_label: runLabel,
    failed_at: new Date().toISOString(),
    error_type: error?.constructor?.name || "Error",
    message: String(error?.message || error),
  };
  await writeFile(
    path.join(outputDir, "harness_failure.json"),
    `${JSON.stringify(failure, null, 2)}\n`,
    "utf8",
  ).catch(() => {});
  throw error;
} finally {
  if (cdp) {
    try {
      // Chromium can exit after persisting the final screenshot but before
      // acknowledging Browser.close. Do not leave top-level await unsettled.
      await Promise.race([
        cdp.send("Browser.close").catch(() => {}),
        delay(1_000),
      ]);
    } catch {}
  }
  cdp?.close();
  chrome.kill("SIGTERM");
  await delay(500);
  if (!chrome.killed) chrome.kill("SIGKILL");
  if (ownsChromeProfile && process.env.JW_EVAL_KEEP_PROFILE !== "1") {
    await rm(chromeProfile, { recursive: true, force: true }).catch(() => {});
  }
}
