import assert from "node:assert/strict";
import test from "node:test";

import { bundledSkillsAsCatalog } from "../src/lib/server/builtinSkills.js";

test("uses bundled JW skills as the official catalog fallback", () => {
  const catalog = bundledSkillsAsCatalog([
    {
      name: "solar-cycle",
      title: "solar-cycle",
      description: "太阳活动周研究",
      dir: "/repo/jw/subagents/solar/skills/solar-cycle",
      source: "builtin",
    },
  ]);

  assert.deepEqual(catalog, [
    {
      name: "solar-cycle",
      title: "solar-cycle",
      description: "太阳活动周研究",
      fileCount: 1,
      installed: true,
      updateAvailable: false,
    },
  ]);
});
