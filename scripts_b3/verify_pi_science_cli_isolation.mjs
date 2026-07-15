#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import {
  isolatedScienceCliArgs,
  projectPython,
  scienceCliEnvironment,
} from "../.pi/extensions/b3-science/science-cli-runtime.ts";

const projectRoot = realpathSync(resolve(process.cwd()));
const python = projectPython(projectRoot);
const pythonRelative = relative(projectRoot, python);
assert.equal(isAbsolute(pythonRelative), false);
assert.equal(pythonRelative.startsWith(".."), false);
assert.equal(statSync(python).isFile(), true);

const tempRoot = mkdtempSync(join(tmpdir(), "b3-python-isolation-"));
const sentinel = join(tempRoot, "pythonpath-imported.txt");
const malicious = join(tempRoot, "psutil.py");
writeFileSync(
  malicious,
  [
    "from pathlib import Path",
    `Path(${JSON.stringify(sentinel)}).write_text('executed', encoding='utf-8')`,
    "__version__ = 'forged'",
  ].join("\n"),
  "utf8",
);

const previous = {
  activeAgent: process.env.B3_ACTIVE_AGENT,
  pythonPath: process.env.PYTHONPATH,
  pythonHome: process.env.PYTHONHOME,
  path: process.env.PATH,
  apiKey: process.env.DASHSCOPE_API_KEY,
};
try {
  process.env.B3_ACTIVE_AGENT = "b3-research-planner";
  process.env.PYTHONPATH = tempRoot;
  process.env.PYTHONHOME = tempRoot;
  process.env.PATH = tempRoot;
  process.env.DASHSCOPE_API_KEY = "sentinel-must-not-reach-science-cli";
  assert.throws(() => projectPython(tempRoot), /\.venv Python is missing/);
  const cliEnvironment = scienceCliEnvironment();
  assert.equal(cliEnvironment.PYTHONPATH, undefined);
  assert.equal(cliEnvironment.PYTHONHOME, undefined);
  assert.equal(cliEnvironment.PATH, undefined);
  assert.equal(cliEnvironment.DASHSCOPE_API_KEY, undefined);
  assert.match(cliEnvironment.B3_TOOL_RECEIPT_HMAC_KEY ?? "", /^[0-9a-f]{64}$/);
  const completed = spawnSync(
    python,
    isolatedScienceCliArgs(projectRoot, [
      "discover-tools",
      "--agent",
      "b3-research-planner",
      "--limit",
      "1",
    ]),
    {
      cwd: projectRoot,
      shell: false,
      windowsHide: true,
      encoding: "utf8",
      env: cliEnvironment,
    },
  );
  assert.equal(completed.status, 0, completed.stderr);
  const payload = JSON.parse(completed.stdout);
  assert.equal(payload.agent, "b3-research-planner");
  assert.equal(payload.execution_trust, "parent_bound_agent");
  assert.equal(existsSync(sentinel), false, "PYTHONPATH module was imported");
} finally {
  for (const [name, value] of Object.entries({
    B3_ACTIVE_AGENT: previous.activeAgent,
    PYTHONPATH: previous.pythonPath,
    PYTHONHOME: previous.pythonHome,
    PATH: previous.path,
    DASHSCOPE_API_KEY: previous.apiKey,
  })) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  const realTemp = realpathSync(tempRoot);
  const realSystemTemp = realpathSync(tmpdir());
  assert.equal(relative(realSystemTemp, realTemp).startsWith(".."), false);
  rmSync(realTemp, { recursive: true, force: true });
}

process.stdout.write(
  JSON.stringify(
    {
      passed: true,
      trusted_project_venv: true,
      isolated_mode: true,
      missing_venv_fails_closed: true,
      environment_allowlist: true,
      python_environment_injection_rejected: true,
    },
    null,
    2,
  ) + "\n",
);
