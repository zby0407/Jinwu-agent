// 维护者专用真实会话验收脚本。它以 RPC 模式启动 Pi；不是科学假设 Agent 的
// 产品入口，也从不直接调用 Python 核心。
import { createWriteStream, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";

const prompt = process.env.PI_LIVE_PROMPT ?? process.argv[2];
const [sessionDir, eventPath, errorPath] = process.argv.slice(3);
const piCli = process.env.PI_CLI_JS;
if (!prompt || !sessionDir || !eventPath || !errorPath || !piCli) {
  throw new Error(
    "usage: PI_CLI_JS=<cli.js> node pi_rpc_live_runner.mjs <prompt> <sessionDir> <events> <stderr>",
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
    "scientific-hypothesis-live-acceptance",
  ],
  {
    cwd: resolve("E:/2026tzb/dist/科学假设agent"),
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
// 假设任务是纯推理，正常应在二十分钟内完成；超时先请求正常中止，
// 让扩展钩子留下正式结果，再强杀。
const gracefulStopTimeout = setTimeout(() => {
  errors.write("live acceptance requested a graceful stop after 25 minutes\n");
  child.stdin.write(JSON.stringify({ id: "timeout-abort", type: "abort" }) + "\n");
}, 25 * 60 * 1000);
const hardStopTimeout = setTimeout(() => {
  errors.write("live acceptance did not settle during the stop grace period\n");
  child.kill();
  finish(124);
}, 30 * 60 * 1000);

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
