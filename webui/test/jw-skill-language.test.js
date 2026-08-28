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

test("JW topology provides Chinese profiles for six primary and support agents", async () => {
  const projectRoot = fileURLToPath(new URL("../..", import.meta.url));
  const topology = await readSkillTopology(projectRoot);

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
