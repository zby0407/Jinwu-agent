import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  importLocalSkills,
  readBundledSkills,
  readLocalSkillCandidates,
} from "../src/lib/server/builtinSkills.js";

test("reads JW bundled skills from core and domain skill roots", async () => {
  const root = await mkdtemp(join(tmpdir(), "jw-builtin-skills-"));
  await mkdir(join(root, "jw/subagents/core/skills/scientific-writing"), {
    recursive: true,
  });
  await mkdir(join(root, "jw/subagents/solar/skills/solar-cycle"), {
    recursive: true,
  });
  await writeFile(
    join(root, "jw/subagents/core/skills/scientific-writing/SKILL.md"),
    "---\nname: scientific-writing\ndescription: 写作\n---\n"
  );
  await writeFile(
    join(root, "jw/subagents/solar/skills/solar-cycle/SKILL.md"),
    "---\nname: solar-cycle\ndescription: 周期\n---\n"
  );

  const skills = await readBundledSkills(root);

  assert.deepEqual(
    skills.map(({ name, source }) => ({ name, source })),
    [
      { name: "scientific-writing", source: "builtin" },
      { name: "solar-cycle", source: "builtin" },
    ]
  );
});

test("lists and imports a mapped local skill with a project-specific name", async () => {
  const root = await mkdtemp(join(tmpdir(), "jw-skill-import-"));
  const local = await mkdtemp(join(tmpdir(), "jw-local-skills-"));
  await mkdir(join(root, "jw/subagents/solar/skills/solar-cycle"), {
    recursive: true,
  });
  await writeFile(
    join(root, "jw/subagents/solar/skills/solar-cycle/SKILL.md"),
    "---\nname: solar-cycle\ndescription: cycle\n---\n"
  );
  await writeFile(
    join(root, "jw/subagents/skill_registry.json"),
    JSON.stringify({
      version: 1,
      shared: [],
      agents: {},
      adaptations: {
        "math-modeling-figure-production": {
          name: "solar-evidence-figure-production",
          reviewed: true,
          adapted_copy_only: true,
          target_agents: ["solar-data"],
        },
      },
    })
  );
  await mkdir(join(local, "math-modeling-figure-production"), {
    recursive: true,
  });
  await writeFile(
    join(local, "math-modeling-figure-production/SKILL.md"),
    "---\nname: math-modeling-figure-production\ndescription: old\n---\n\nold body"
  );
  const previous = process.env.JW_SKILLS_IMPORT_DIRS;
  process.env.JW_SKILLS_IMPORT_DIRS = local;
  try {
    const candidates = await readLocalSkillCandidates(root);
    assert.equal(candidates[0].adaptedName, "solar-evidence-figure-production");
    const imported = await importLocalSkills(root, [candidates[0].name]);
    assert.equal(imported[0].name, "solar-evidence-figure-production");
    const adapted = await readFile(
      join(
        root,
        "jw/subagents/solar/skills/solar-evidence-figure-production/SKILL.md"
      ),
      "utf8"
    );
    assert.match(adapted, /name: solar-evidence-figure-production/);
    assert.match(adapted, /JW 太阳周期证据图生产/);
  } finally {
    if (previous === undefined) delete process.env.JW_SKILLS_IMPORT_DIRS;
    else process.env.JW_SKILLS_IMPORT_DIRS = previous;
  }
});
