#!/usr/bin/env node
import assert from "node:assert/strict";
import { resolve } from "node:path";
import {
  buildChildArgs,
  trustedChildExtensions,
} from "../.pi/extensions/b3-science/child-policy.ts";

const projectRoot = resolve(process.cwd());
const extensions = trustedChildExtensions(projectRoot);
const tools = [
  "b3_read_project",
  "b3_grep_project",
  "b3_find_project",
  "b3_list_project",
];
const args = buildChildArgs(
  ["pi-cli.js"],
  extensions,
  {
    model: "dashscope/qwen3.7-max-2026-06-08",
    thinking: "medium",
    tools,
    filePath: resolve(projectRoot, ".pi", "agents", "b3-research-planner.md"),
  },
  "Return one JSON object.",
);

assert.equal(args.filter((value) => value === "--extension").length, 2);
assert.equal(args.includes("--no-extensions"), true);
assert.equal(args.includes("--no-builtin-tools"), true);
assert.equal(args.includes("--no-context-files"), true);
assert.equal(args.includes("--no-skills"), true);
assert.equal(args.includes("--no-prompt-templates"), true);
assert.equal(args.includes("--no-session"), true);
assert.equal(args[args.indexOf("--mode") + 1], "json");
assert.equal(args.includes("--print"), false);
assert.equal(args[args.indexOf("--thinking") + 1], "medium");
assert.equal(args[args.indexOf("--tools") + 1], tools.join(","));
assert.equal(args.some((value) => /^(?:read|grep|find|ls|bash|edit|write)$/.test(value)), false);
assert.equal(args.some((value) => /sentinel|api[_-]?key|secret/i.test(value)), false);

let builtinRejected = false;
try {
  buildChildArgs([], extensions, {
    model: "dashscope/qwen3.7-max-2026-06-08",
    thinking: "medium",
    tools: ["read"],
    filePath: "agent.md",
  }, "prompt");
} catch {
  builtinRejected = true;
}
assert.equal(builtinRejected, true);

const highThinkingArgs = buildChildArgs(
  [],
  extensions,
  {
    model: "dashscope/qwen3.7-max-2026-06-08",
    thinking: "high",
    tools,
    filePath: resolve(projectRoot, ".pi", "agents", "b3-research-planner.md"),
  },
  "Return one JSON object.",
);
assert.equal(highThinkingArgs.filter((value) => value === "--extension").length, 2);
assert.equal(highThinkingArgs[highThinkingArgs.indexOf("--thinking") + 1], "high");

let thinkingRejected = false;
try {
  buildChildArgs([], extensions, {
    model: "dashscope/qwen3.7-max-2026-06-08",
    thinking: "max",
    tools,
    filePath: "agent.md",
  }, "prompt");
} catch {
  thinkingRejected = true;
}
assert.equal(thinkingRejected, true);

let unreviewedModelRejected = false;
try {
  buildChildArgs([], extensions, {
    model: "unknown/model",
    thinking: "medium",
    tools,
    filePath: "agent.md",
  }, "prompt");
} catch {
  unreviewedModelRejected = true;
}
assert.equal(unreviewedModelRejected, true);

const optionalQwenArgs = buildChildArgs([], extensions, {
  model: "dashscope/qwen3.7-plus-2026-05-26",
  thinking: "medium",
  tools,
  filePath: "agent.md",
}, "prompt");
assert.equal(optionalQwenArgs.filter((value) => value === "--extension").length, 2);

const optionalFlashArgs = buildChildArgs([], extensions, {
  model: "dashscope/qwen3.6-flash-2026-04-16",
  thinking: "low",
  tools,
  filePath: "agent.md",
}, "prompt");
assert.equal(optionalFlashArgs.filter((value) => value === "--extension").length, 2);

assert.throws(() =>
  buildChildArgs([], extensions, {
    model: "kimi-coding/kimi-for-coding",
    thinking: "medium",
    tools,
    filePath: "agent.md",
  }, "prompt"),
);

process.stdout.write(
  `${JSON.stringify({
    schema_version: "b3-child-policy-verifier-v1",
    passed: true,
    qwen_max_extension_count: 2,
    qwen_max_default: true,
    optional_qwen_plus: true,
    optional_qwen_flash: true,
    kimi_route_rejected: true,
    unreviewed_model_rejected: true,
    builtin_tools_disabled: true,
    discovery_disabled: true,
    json_event_transport: true,
    print_mode_disabled: true,
    role_thinking_enforced: true,
    credential_values_absent: true,
  })}\n`,
);
