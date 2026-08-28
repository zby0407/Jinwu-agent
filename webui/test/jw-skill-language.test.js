import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { readBundledSkills } from "../src/lib/server/builtinSkills.js";

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
