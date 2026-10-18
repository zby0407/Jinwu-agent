import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  readBundledSkills,
  readSkillTopology,
  skillProjectRoots,
} from "../src/lib/server/builtinSkills.js";

test("standalone skills resolve source outside the research workspace", async () => {
  const base = await mkdtemp(join(tmpdir(), "jw-skills-roots-"));
  const source = join(base, "checkout");
  const runtime = join(base, "research", "runtime");
  const skillDir = join(source, "jw/subagents/solar/skills/solar-cycle");
  await mkdir(skillDir, { recursive: true });
  await mkdir(runtime, { recursive: true });
  await writeFile(
    join(skillDir, "SKILL.md"),
    "---\nname: solar-cycle\ndescription: cycle research\n---\nLocal skill body.",
    "utf8"
  );
  await writeFile(
    join(source, "jw/subagents/skill_registry.json"),
    JSON.stringify({
      primary_agents: ["solar-data"],
      agent_profiles: { "solar-data": { title: "Data" } },
      agents: { "solar-data": ["solar-cycle"] },
    }),
    "utf8"
  );

  for (const cwd of [
    source,
    join(source, "webui"),
    join(source, "webui/dist"),
  ]) {
    const roots = skillProjectRoots({
      cwd,
      workspaceDir: runtime,
      sourceDir: "",
    });
    let found;
    for (const root of roots) {
      const skills = await readBundledSkills(root);
      if (skills.length) {
        found = { root, skills, topology: await readSkillTopology(root) };
        break;
      }
    }
    assert.equal(found.root, source);
    assert.equal(found.skills[0].name, "solar-cycle");
    assert.deepEqual(found.skills[0].assignment.agents, ["solar-data"]);
    assert.equal(found.topology.primaryAgents[0].title, "Data");
    assert.match(
      await readFile(join(found.skills[0].dir, "SKILL.md"), "utf8"),
      /Local skill body/
    );
    assert.deepEqual(await readBundledSkills(runtime), []);
  }
});

test("explicit source supports detached WebUI and precedes workspace", () => {
  const root = resolve(tmpdir(), "jw-source");
  const runtime = resolve(tmpdir(), "jw-runtime");
  const roots = skillProjectRoots({
    cwd: resolve(tmpdir(), "npm-webui/dist"),
    sourceDir: ` ${root} `,
    workspaceDir: runtime,
  });
  assert.equal(roots[0], root);
  assert.equal(roots[1], runtime);
});

test("blank overrides do not hide development roots and roots are deduplicated", () => {
  const cwd = resolve(tmpdir(), "jw-source/webui");
  const roots = skillProjectRoots({ cwd, workspaceDir: " ", sourceDir: "" });
  assert.equal(roots[0], resolve(cwd, ".."));
  assert.ok(roots.includes(cwd));
  const duplicate = skillProjectRoots({
    cwd,
    sourceDir: cwd,
    workspaceDir: cwd,
  });
  assert.equal(duplicate.filter((root) => root === cwd).length, 1);
});
