import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  buildPrimaryAgentGroups,
  readBundledSkills,
  readSkillTopology,
} from "../src/lib/server/builtinSkills.js";

const solarPromptFiles = [
  "planner",
  "data",
  "hypothesis",
  "experiment",
  "evidence",
  "knowledge",
].map((role) => `../../jw/subagents/solar/solar_${role}.yaml`);

test("six JW solar prompts expose a Chinese-first execution contract", async () => {
  const prompts = await Promise.all(
    solarPromptFiles.map((file) =>
      readFile(new URL(file, import.meta.url), "utf8")
    )
  );

  for (const prompt of prompts) {
    assert.match(prompt, /JW 中文执行摘要/);
    assert.match(prompt, /读者可见输出使用中文/);
    assert.match(prompt, /保留 English technical terms/);
  }
});

test("skills assigned to JW agents have Chinese discovery descriptions", async () => {
  const projectRoot = fileURLToPath(new URL("../..", import.meta.url));
  const skills = await readBundledSkills(projectRoot);
  const assigned = skills.filter(
    (skill) => skill.assignment.shared || skill.assignment.agents.length > 0
  );

  assert.ok(assigned.length > 0);
  for (const skill of assigned) {
    assert.doesNotMatch(skill.name, /^math-modeling-/);
    assert.match(skill.description, /[\u3400-\u9fff]/, skill.name);
  }
});

test("new JW reasoning skills use Chinese-first discovery triggers", async () => {
  const files = [
    "../../jw/subagents/core/skills/jw-integration-and-final-answer/SKILL.md",
    "../../jw/subagents/solar/skills/solar-cycle-forecast-validation/SKILL.md",
    "../../jw/subagents/solar/skills/solar-hypothesis-portfolio/SKILL.md",
    "../../jw/subagents/solar/skills/solar-mechanism-causal-order/SKILL.md",
    "../../jw/subagents/solar/skills/solar-interaction-regime-testing/SKILL.md",
  ];
  const markdown = await Promise.all(
    files.map((file) => readFile(new URL(file, import.meta.url), "utf8"))
  );
  for (const skill of markdown) {
    assert.match(skill, /^description: 当.+时使用。?$/m);
  }
});

test("JW topology provides Chinese profiles for six primary and support agents", async () => {
  const projectRoot = fileURLToPath(new URL("../..", import.meta.url));
  const topology = await readSkillTopology(projectRoot);

  assert.equal(topology.mainAgent.name, "JW");
  assert.match(topology.mainAgent.title, /[\u3400-\u9fff]/);
  assert.equal(topology.primaryAgents.length, 6);
  assert.deepEqual(
    topology.primaryAgents.map((agent) => agent.name),
    [
      "solar-planner",
      "solar-data",
      "solar-hypothesis",
      "solar-experiment",
      "solar-evidence",
      "solar-knowledge",
    ]
  );
  for (const agent of [...topology.primaryAgents, ...topology.supportAgents]) {
    assert.match(agent.name, /^[a-z][a-z0-9-]+$/);
    assert.match(agent.title, /[\u3400-\u9fff]/);
    assert.match(agent.description, /[\u3400-\u9fff]/);
  }
});

test("primary agent groups follow registry order and keep roles without exclusive skills", () => {
  const profiles = [
    { name: "solar-planner", title: "规划", description: "规划研究" },
    { name: "solar-data", title: "数据", description: "处理数据" },
  ];
  const skillsByAgent = new Map([
    ["solar-data", [{ name: "solar-cycle" }]],
    ["unlisted-agent", [{ name: "hidden" }]],
  ]);

  const groups = buildPrimaryAgentGroups(profiles, skillsByAgent);

  assert.deepEqual(
    groups.map((group) => [group.name, group.skills.length]),
    [
      ["solar-planner", 0],
      ["solar-data", 1],
    ]
  );
});

test("cycle forecasting validation is a dedicated data, experiment, and evidence capability", async () => {
  const projectRoot = fileURLToPath(new URL("../..", import.meta.url));
  const registry = JSON.parse(
    await readFile(new URL("../../jw/subagents/skill_registry.json", import.meta.url), "utf8")
  );
  const skill = await readFile(
    new URL(
      "../../jw/subagents/solar/skills/solar-cycle-forecast-validation/SKILL.md",
      import.meta.url
    ),
    "utf8"
  );

  assert.match(skill, /逐预测时点|as-of/i);
  assert.match(skill, /rolling-origin/i);
  assert.match(skill, /基线/);
  assert.match(skill, /不确定性/);
  assert.match(skill, /困难周期|失败周期/);
  assert.deepEqual(
    ["solar-data", "solar-experiment", "solar-evidence"].map((agent) => [
      agent,
      registry.agents[agent].includes("solar-cycle-forecast-validation"),
    ]),
    [
      ["solar-data", true],
      ["solar-experiment", true],
      ["solar-evidence", true],
    ]
  );

  const assignedAgents = Object.entries(registry.agents)
    .filter(([, names]) => names.includes("solar-cycle-forecast-validation"))
    .map(([agent]) => agent)
    .sort();
  assert.deepEqual(assignedAgents, [
    "solar-data",
    "solar-evidence",
    "solar-experiment",
  ]);

  const bundled = await readBundledSkills(projectRoot);
  assert.ok(bundled.some((entry) => entry.name === "solar-cycle-forecast-validation"));
});

test("hypothesis portfolio separates evidence rank from experiment priority", async () => {
  const registry = JSON.parse(
    await readFile(new URL("../../jw/subagents/skill_registry.json", import.meta.url), "utf8")
  );
  const skill = await readFile(
    new URL(
      "../../jw/subagents/solar/skills/solar-hypothesis-portfolio/SKILL.md",
      import.meta.url
    ),
    "utf8"
  );

  assert.match(skill, /支持度/);
  assert.match(skill, /研究优先级|实验优先级/);
  assert.match(skill, /依赖/);
  assert.match(skill, /value of information|信息价值/i);
  assert.match(skill, /零假设|负结果|证据不足/);
  assert.match(skill, /H1[\s\S]*历史描述性支持[\s\S]*样本外/);
  assert.match(skill, /H2[\s\S]*极区场[\s\S]*(数据覆盖|覆盖范围)/);
  assert.match(skill, /H3[\s\S]*正交互[\s\S]*负交互[\s\S]*证据不足/);
  assert.deepEqual(
    Object.entries(registry.agents)
      .filter(([, names]) => names.includes("solar-hypothesis-portfolio"))
      .map(([agent]) => agent)
      .sort(),
    ["solar-evidence", "solar-hypothesis", "solar-planner"]
  );
});

test("mechanism validation preserves solar causal order and observable semantics", async () => {
  const registry = JSON.parse(
    await readFile(new URL("../../jw/subagents/skill_registry.json", import.meta.url), "utf8")
  );
  const skill = await readFile(
    new URL(
      "../../jw/subagents/solar/skills/solar-mechanism-causal-order/SKILL.md",
      import.meta.url
    ),
    "utf8"
  );

  assert.match(skill, /Babcock.?Leighton/i);
  assert.match(skill, /时间顺序/);
  assert.match(skill, /轴向二极矩/);
  assert.match(skill, /带符号|无符号/);
  assert.match(skill, /共同驱动|竞争机制/);
  assert.match(skill, /相关[\s\S]*(不能|不得)[\s\S]*因果/);
  assert.deepEqual(
    Object.entries(registry.agents)
      .filter(([, names]) => names.includes("solar-mechanism-causal-order"))
      .map(([agent]) => agent)
      .sort(),
    ["solar-evidence", "solar-experiment", "solar-hypothesis"]
  );
});

test("interaction testing supports direction-free and regime alternatives", async () => {
  const registry = JSON.parse(
    await readFile(new URL("../../jw/subagents/skill_registry.json", import.meta.url), "utf8")
  );
  const skill = await readFile(
    new URL(
      "../../jw/subagents/solar/skills/solar-interaction-regime-testing/SKILL.md",
      import.meta.url
    ),
    "utf8"
  );

  assert.match(skill, /effect modification/i);
  assert.match(skill, /正交互/);
  assert.match(skill, /负交互/);
  assert.match(skill, /加性模型/);
  assert.match(skill, /非线性|regime/i);
  assert.match(skill, /识别性|identifiability/i);
  assert.match(skill, /低功效|功效不足/);
  assert.match(skill, /rolling-origin/i);
  assert.deepEqual(
    Object.entries(registry.agents)
      .filter(([, names]) => names.includes("solar-interaction-regime-testing"))
      .map(([agent]) => agent)
      .sort(),
    ["solar-evidence", "solar-experiment", "solar-hypothesis"]
  );
});

test("JW owns integration and release skills while specialists keep role-scoped skills", async () => {
  const projectRoot = fileURLToPath(new URL("../..", import.meta.url));
  const registry = JSON.parse(
    await readFile(new URL("../../jw/subagents/skill_registry.json", import.meta.url), "utf8")
  );
  const skill = await readFile(
    new URL(
      "../../jw/subagents/core/skills/jw-integration-and-final-answer/SKILL.md",
      import.meta.url
    ),
    "utf8"
  );

  assert.equal(registry.main_agent, "JW");
  assert.deepEqual(registry.shared, ["verification-before-completion"]);
  assert.match(skill, /声明级|claim-level/i);
  assert.match(skill, /blocked/);
  assert.match(skill, /最终回答/);
  assert.match(skill, /发布|release/i);
  assert.deepEqual(registry.agents.JW, [
    "jw-integration-and-final-answer",
    "scientific-writing",
    "writing-reader-facing-content",
    "jw-release-export-qa",
    "find-skills",
  ]);

  const forbiddenPrimary = new Set([
    "find-skills",
    "scientific-writing",
    "writing-reader-facing-content",
    "jw-release-export-qa",
    "solar-flare-forecasting",
  ]);
  for (const agent of registry.primary_agents) {
    assert.equal(
      registry.agents[agent].some((name) => forbiddenPrimary.has(name)),
      false,
      agent
    );
  }
  assert.ok(registry.agents["solar-knowledge"].includes("solar-cycle"));

  const bundled = await readBundledSkills(projectRoot);
  const flare = bundled.find((entry) => entry.name === "solar-flare-forecasting");
  assert.equal(flare.assignment.conditional, true);
  assert.deepEqual(flare.assignment.conditionalAgents, [
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
    "solar-evidence",
    "solar-knowledge",
  ]);

  const [coreBundle, solarBundle, marketplace] = await Promise.all([
    readFile(new URL("../../jw/subagents/core/bundle.yaml", import.meta.url), "utf8"),
    readFile(new URL("../../jw/subagents/solar/bundle.yaml", import.meta.url), "utf8"),
    readFile(new URL("../src/app/components/SkillsMarketplace.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(coreBundle, /jw-integration-and-final-answer/);
  for (const name of [
    "solar-cycle-forecast-validation",
    "solar-hypothesis-portfolio",
    "solar-mechanism-causal-order",
    "solar-interaction-regime-testing",
  ]) {
    assert.match(solarBundle, new RegExp(name));
  }
  assert.match(marketplace, /conditionalAgents/);
});
