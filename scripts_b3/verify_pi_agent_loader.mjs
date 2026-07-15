#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  B3_AGENT_NAMES,
  loadProjectAgent,
} from "../.pi/extensions/b3-science/agents.ts";

const root = resolve(process.cwd());
const expectedTools = {
  "b3-research-planner": [
    "b3_read_project",
    "b3_grep_project",
    "b3_find_project",
    "b3_list_project",
    "b3_discover_tools",
    "b3_inspect_tool",
    "b3_run_tool",
    "b3_verify_result",
    "b3_trace_artifact",
  ],
  "b3-experiment": [
    "b3_read_project",
    "b3_grep_project",
    "b3_find_project",
    "b3_list_project",
    "b3_run_registered_experiment",
    "b3_read_run_state",
    "b3_discover_tools",
    "b3_inspect_tool",
    "b3_run_tool",
    "b3_verify_result",
    "b3_trace_artifact",
  ],
  "b3-hypothesis": [
    "b3_read_project",
    "b3_grep_project",
    "b3_find_project",
    "b3_list_project",
    "b3_read_run_state",
    "b3_discover_tools",
    "b3_inspect_tool",
    "b3_run_tool",
    "b3_verify_result",
    "b3_trace_artifact",
  ],
};
const expectedThinking = {
  "b3-research-planner": "medium",
  "b3-experiment": "low",
  "b3-hypothesis": "high",
};

const previousAgentModel = process.env.B3_AGENT_MODEL;
const previousQwenModel = process.env.B3_QWEN_MODEL;
delete process.env.B3_AGENT_MODEL;
delete process.env.B3_QWEN_MODEL;

const projectSettings = JSON.parse(
  readFileSync(join(root, ".pi", "settings.json"), "utf8"),
);
assert.equal(projectSettings.defaultProvider, "dashscope");
assert.equal(projectSettings.defaultModel, "qwen3.7-max-2026-06-08");
assert.equal(projectSettings.defaultThinkingLevel, "high");
assert.deepEqual(projectSettings.enabledModels, [
  "dashscope/qwen3.7-max-2026-06-08",
  "dashscope/qwen3.7-plus-2026-05-26",
  "dashscope/qwen3.6-flash-2026-04-16",
]);

const reports = {};
for (const name of B3_AGENT_NAMES) {
  const agent = loadProjectAgent(root, name);
  assert.equal(agent.name, name);
  assert.equal(agent.model, "dashscope/qwen3.7-max-2026-06-08");
  assert.equal(agent.thinking, expectedThinking[name]);
  assert.deepEqual(agent.tools, expectedTools[name]);
  reports[name] = {
    model: agent.model,
    thinking: agent.thinking,
    tools: agent.tools,
  };
}

try {
  process.env.B3_AGENT_MODEL = "dashscope/qwen3.7-max-2026-06-08";
  process.env.B3_QWEN_MODEL = "qwen3.7-max-2026-06-08";
  assert.equal(
    loadProjectAgent(root, "b3-research-planner").model,
    "dashscope/qwen3.7-max-2026-06-08",
  );
  process.env.B3_AGENT_MODEL = "dashscope/qwen3.7-plus-2026-05-26";
  process.env.B3_QWEN_MODEL = "qwen3.7-plus-2026-05-26";
  assert.equal(
    loadProjectAgent(root, "b3-research-planner").model,
    "dashscope/qwen3.7-plus-2026-05-26",
  );
  process.env.B3_AGENT_MODEL = "dashscope/qwen3.6-flash-2026-04-16";
  process.env.B3_QWEN_MODEL = "qwen3.6-flash-2026-04-16";
  assert.equal(
    loadProjectAgent(root, "b3-research-planner").model,
    "dashscope/qwen3.6-flash-2026-04-16",
  );
  process.env.B3_AGENT_MODEL = "kimi-coding/kimi-for-coding";
  delete process.env.B3_QWEN_MODEL;
  assert.throws(() => loadProjectAgent(root, "b3-research-planner"));
  process.env.B3_AGENT_MODEL = "unreviewed/provider-model";
  assert.throws(() => loadProjectAgent(root, "b3-research-planner"));
} finally {
  if (previousAgentModel === undefined) delete process.env.B3_AGENT_MODEL;
  else process.env.B3_AGENT_MODEL = previousAgentModel;
  if (previousQwenModel === undefined) delete process.env.B3_QWEN_MODEL;
  else process.env.B3_QWEN_MODEL = previousQwenModel;
}

let unknownRejected = false;
try {
  loadProjectAgent(root, "../secret");
} catch {
  unknownRejected = true;
}
assert.equal(unknownRejected, true);

const sandbox = mkdtempSync(join(tmpdir(), "b3-agent-loader-"));
try {
  const targetRoot = join(sandbox, ".pi", "agents");
  mkdirSync(targetRoot, { recursive: true });
  const source = join(root, ".pi", "agents", "b3-research-planner.md");
  const target = join(targetRoot, "b3-research-planner.md");
  copyFileSync(source, target);
  const tampered = readFileSync(target, "utf8").replace(
    "tools: b3_read_project, b3_grep_project, b3_find_project, b3_list_project",
    "tools: read, grep, find, ls",
  );
  writeFileSync(target, tampered, "utf8");
  let tamperRejected = false;
  try {
    loadProjectAgent(sandbox, "b3-research-planner");
  } catch {
    tamperRejected = true;
  }
  assert.equal(tamperRejected, true);
} finally {
  if (!sandbox.startsWith(resolve(tmpdir()))) {
    throw new Error("Refusing to clean an unexpected agent-loader path");
  }
  rmSync(sandbox, { recursive: true, force: true });
}

const thinkingSandbox = mkdtempSync(join(tmpdir(), "b3-agent-thinking-loader-"));
try {
  const targetRoot = join(thinkingSandbox, ".pi", "agents");
  mkdirSync(targetRoot, { recursive: true });
  const source = join(root, ".pi", "agents", "b3-research-planner.md");
  const target = join(targetRoot, "b3-research-planner.md");
  copyFileSync(source, target);
  const tampered = readFileSync(target, "utf8").replace(
    "thinking: medium",
    "thinking: max",
  );
  writeFileSync(target, tampered, "utf8");
  assert.throws(() => loadProjectAgent(thinkingSandbox, "b3-research-planner"));
} finally {
  if (!thinkingSandbox.startsWith(resolve(tmpdir()))) {
    throw new Error("Refusing to clean an unexpected thinking-loader path");
  }
  rmSync(thinkingSandbox, { recursive: true, force: true });
}

process.stdout.write(
  `${JSON.stringify({
    schema_version: "b3-agent-loader-verifier-v1",
    passed: true,
    agents: reports,
    unknown_name_rejected: true,
    tampered_allowlist_rejected: true,
    qwen_max_default_validated: true,
    optional_qwen_plus_validated: true,
    optional_qwen_flash_validated: true,
    kimi_route_rejected: true,
    project_settings_validated: true,
    role_thinking_levels_validated: true,
    tampered_thinking_rejected: true,
    unreviewed_model_rejected: true,
  })}\n`,
);
