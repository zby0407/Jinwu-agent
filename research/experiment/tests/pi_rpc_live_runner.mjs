// Maintainer-only live acceptance harness. It starts Pi in RPC mode; it is not
// an Automatic Experiment product entry and never calls the Python core directly.
import { createWriteStream, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";

const args = process.argv.slice(2);
const useEnvironmentPrompt = args[0] === "--env-prompt";
const prompt = useEnvironmentPrompt ? process.env.PI_LIVE_PROMPT : args[0];
const [sessionDir, eventPath, errorPath] = useEnvironmentPrompt
  ? args.slice(1)
  : args.slice(1);
const piCli = process.env.PI_CLI_JS;
if (!prompt || !sessionDir || !eventPath || !errorPath || !piCli) {
  throw new Error(
    "usage: node pi_rpc_live_runner.mjs <prompt|--env-prompt> <sessionDir> <events> <stderr>; PI_CLI_JS is required and --env-prompt reads PI_LIVE_PROMPT",
  );
}
mkdirSync(sessionDir, { recursive: true });
mkdirSync(dirname(eventPath), { recursive: true });
const events = createWriteStream(eventPath, { encoding: "utf8" });
const errors = createWriteStream(errorPath, { encoding: "utf8" });
const child = spawn(
  process.execPath,
  [
    resolve(piCli),
    "--mode",
    "rpc",
    "--approve",
    "--no-builtin-tools",
    "--provider",
    "dashscope",
    "--model",
    "qwen3.7-max-2026-06-08",
    "--thinking",
    "high",
    "--session-dir",
    resolve(sessionDir),
    "--name",
    "automatic-experiment-live-acceptance",
  ],
  {
    cwd: resolve("E:/2026tzb/dist/自动实验agent"),
    shell: false,
    windowsHide: true,
    env: process.env,
    stdio: ["pipe", "pipe", "pipe"],
  },
);

let buffer = "";
let agentEnded = false;
let stateRequested = false;
let settled = false;
let inputClosed = false;
let gracefulStopRequested = false;
const closeInput = () => {
  if (inputClosed) return;
  inputClosed = true;
  child.stdin.end();
};
const finish = (code) => {
  if (settled) return;
  settled = true;
  clearTimeout(gracefulStopTimeout);
  clearTimeout(hardStopTimeout);
  closeInput();
  events.end();
  errors.end();
  process.exitCode = code;
};
// The product's default experiment budget is 30 minutes. Give Pi time to turn
// that budget stop into a formal report; if the full interaction still runs
// unusually long, ask Pi to abort normally so extension hooks can finalize it.
const gracefulStopTimeout = setTimeout(() => {
  gracefulStopRequested = true;
  errors.write("live acceptance requested a graceful stop after 40 minutes\n");
  child.stdin.write(
    JSON.stringify({ id: "timeout-abort", type: "abort" }) + "\n",
  );
}, 40 * 60 * 1000);
const hardStopTimeout = setTimeout(() => {
  errors.write("live acceptance did not settle during the stop grace period\n");
  child.kill();
  finish(124);
}, 45 * 60 * 1000);

child.stdout.setEncoding("utf8");
child.stdout.on("data", (chunk) => {
  buffer += chunk;
  while (true) {
    const newline = buffer.indexOf("\n");
    if (newline < 0) break;
    let line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    if (line.endsWith("\r")) line = line.slice(0, -1);
    if (!line) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      errors.write(`invalid RPC JSON: ${line}\n`);
      child.kill();
      finish(2);
      return;
    }
    // Pi session JSONL is the authoritative full transcript. Keep this RPC
    // sidecar compact by omitting cumulative streaming deltas.
    if (event.type !== "message_update") {
      events.write(line + "\n");
    }
    if (
      event.type === "response" &&
      event.command === "prompt" &&
      event.success === false
    ) {
      errors.write(`prompt rejected: ${line}\n`);
      child.kill();
      finish(3);
      return;
    }
    if (event.type === "agent_end") {
      agentEnded = true;
    } else if (event.type === "auto_retry_start") {
      // Pi emits agent_end for the failed attempt before announcing its built-in
      // retry. Do not close RPC input until the whole retry sequence settles.
      agentEnded = false;
      stateRequested = false;
    } else if (event.type === "agent_settled" && agentEnded && !stateRequested) {
      stateRequested = true;
      child.stdin.write(
        JSON.stringify({ id: "final-state", type: "get_state" }) + "\n",
      );
    } else if (
      agentEnded &&
      event.type === "response" &&
      event.id === "final-state"
    ) {
      closeInput();
    } else if (
      gracefulStopRequested &&
      event.type === "response" &&
      event.id === "timeout-abort" &&
      event.success === false
    ) {
      errors.write(`graceful stop rejected: ${line}\n`);
    }
  }
});
child.stderr.on("data", (chunk) => errors.write(chunk));
child.on("error", (error) => {
  errors.write(`${error.stack || error.message}\n`);
  finish(4);
});
child.on("exit", (code, signal) => {
  if (!agentEnded) {
    errors.write(
      `Pi exited before agent_end: code=${String(code)} signal=${String(signal)}\n`,
    );
    finish(code ?? 5);
    return;
  }
  finish(code ?? 0);
});

child.stdin.write(
  JSON.stringify({ id: "live-prompt", type: "prompt", message: prompt }) + "\n",
);
