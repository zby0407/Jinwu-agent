// Tests for the shared skill on/off state (DATA_DIR/skill_settings.json).
//
// The repo's other server-lib tests import plain `.js` modules; this one has to
// load a `.ts` module, and Node's built-in type stripping still needs the exact
// extension in the specifier. The resolver hook below only kicks in when the
// plain resolution fails, so it just adds `.ts` candidates for the source tree
// (both `./jwPaths` and `./jwPaths.js` styles) and nothing else.

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      if (!specifier.startsWith(".") || !context.parentURL) throw error;
      const url = new URL(specifier, context.parentURL);
      const candidates = url.pathname.endsWith(".js")
        ? [`${url.href.slice(0, -3)}.ts`]
        : [`${url.href}.ts`, `${url.href}/index.ts`];
      for (const candidate of candidates) {
        if (existsSync(new URL(candidate)))
          return nextResolve(candidate, context);
      }
      throw error;
    }
  },
});

const { readDisabledSkills, setSkillEnabled, skillSettingsPath } = await import(
  new URL("../src/lib/server/skillSettings.ts", import.meta.url).href
);

/** Point the shared state file at a throwaway DATA_DIR. */
async function useDataDir(t, prefix = "jw-skill-settings-") {
  const dir = await mkdtemp(join(tmpdir(), prefix));
  const previous = process.env.JW_DATA_DIR;
  process.env.JW_DATA_DIR = dir;
  delete process.env.agent_DATA_DIR;
  t.after(() => {
    if (previous === undefined) delete process.env.JW_DATA_DIR;
    else process.env.JW_DATA_DIR = previous;
  });
  return dir;
}

test("resolves the state file inside the configured DATA_DIR", async (t) => {
  const dir = await useDataDir(t);
  assert.equal(skillSettingsPath(), join(dir, "skill_settings.json"));
});

test("missing state file means every skill is enabled", async (t) => {
  await useDataDir(t);
  const disabled = await readDisabledSkills();
  assert.ok(disabled instanceof Set);
  assert.equal(disabled.size, 0);
  assert.equal(disabled.has("solar-cycle"), false);
});

test("disabling a skill persists it to the shared file", async (t) => {
  const dir = await useDataDir(t);
  await setSkillEnabled("solar-cycle", false);

  const disabled = await readDisabledSkills();
  assert.deepEqual([...disabled], ["solar-cycle"]);

  const raw = await readFile(join(dir, "skill_settings.json"), "utf8");
  assert.deepEqual(JSON.parse(raw), {
    version: 1,
    disabled: ["solar-cycle"],
  });
  // Byte contract with Python's writer: 2-space indent, sort_keys, trailing \n.
  assert.equal(
    raw,
    '{\n  "disabled": [\n    "solar-cycle"\n  ],\n  "version": 1\n}\n'
  );
});

test("re-enabling a skill removes it again", async (t) => {
  const dir = await useDataDir(t);
  await setSkillEnabled("solar-cycle", false);
  await setSkillEnabled("paper-writing", false);
  // Sorted + de-duplicated, as Python writes it.
  assert.deepEqual(
    [...(await readDisabledSkills())],
    ["paper-writing", "solar-cycle"]
  );

  await setSkillEnabled("solar-cycle", true);
  assert.deepEqual([...(await readDisabledSkills())], ["paper-writing"]);
  await setSkillEnabled("paper-writing", true);
  assert.equal((await readDisabledSkills()).size, 0);

  const raw = JSON.parse(
    await readFile(join(dir, "skill_settings.json"), "utf8")
  );
  assert.deepEqual(raw, { version: 1, disabled: [] });
});

test("a corrupt state file is tolerated, not fatal", async (t) => {
  const dir = await useDataDir(t);
  await writeFile(join(dir, "skill_settings.json"), "{oops");

  assert.equal((await readDisabledSkills()).size, 0);
  // Writing over a corrupt file still works and yields valid JSON.
  await setSkillEnabled("solar-cycle", false);
  assert.deepEqual([...(await readDisabledSkills())], ["solar-cycle"]);
});

test("a malformed disabled field never breaks the read", async (t) => {
  const dir = await useDataDir(t);
  const path = join(dir, "skill_settings.json");
  for (const broken of [
    '{"version":1,"disabled":"solar-cycle"}',
    '{"version":1,"disabled":null}',
    "[]",
    "not json",
  ]) {
    await writeFile(path, broken);
    assert.equal((await readDisabledSkills()).size, 0);
  }
  // Mixed arrays drop the bad entries but keep the valid names — Python
  // (`_disabled_from_payload`) behaves the same, so both sides agree.
  await writeFile(path, '{"version":1,"disabled":["a",1," "]}');
  assert.deepEqual([...(await readDisabledSkills())], ["a"]);
});

test("unknown fields survive a write, version stays 1", async (t) => {
  const dir = await useDataDir(t);
  await writeFile(
    join(dir, "skill_settings.json"),
    JSON.stringify({ version: 1, custom: true, disabled: ["a"] })
  );

  assert.deepEqual([...(await readDisabledSkills())], ["a"]);
  await setSkillEnabled("b", false);

  const raw = JSON.parse(
    await readFile(join(dir, "skill_settings.json"), "utf8")
  );
  assert.deepEqual(raw, { version: 1, custom: true, disabled: ["a", "b"] });
});

test("creates a missing DATA_DIR and leaves no temp files behind", async (t) => {
  const root = await useDataDir(t);
  const nested = join(root, "nested", "data");
  process.env.JW_DATA_DIR = nested;

  await setSkillEnabled("solar-cycle", false);
  assert.deepEqual([...(await readDisabledSkills())], ["solar-cycle"]);

  const leftovers = (await readdir(nested)).filter((entry) =>
    entry.endsWith(".tmp")
  );
  assert.deepEqual(leftovers, []);
});

test("the writer is atomic — the previous file is never partially visible", async (t) => {
  const dir = await useDataDir(t);
  const path = join(dir, "skill_settings.json");
  await mkdir(dir, { recursive: true });
  await writeFile(path, JSON.stringify({ version: 1, disabled: ["a"] }));

  const writes = [
    setSkillEnabled("b", false),
    setSkillEnabled("c", false),
    setSkillEnabled("a", true),
  ];
  await Promise.all(writes);

  // Whatever order the concurrent writers won in, the file is valid JSON.
  const raw = JSON.parse(await readFile(path, "utf8"));
  assert.equal(raw.version, 1);
  assert.ok(Array.isArray(raw.disabled));
});
