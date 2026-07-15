#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import {
  authorizeProjectPath,
  isProtectedProjectRelativePath,
} from "../.pi/extensions/b3-science/project-paths.ts";
import { findB3ProjectRoot } from "../.pi/extensions/b3-science/project-root.ts";

const sandbox = mkdtempSync(join(tmpdir(), "b3-project-paths-"));
const nestedDecoy = mkdtempSync(join(process.cwd(), ".b3-nested-root-"));
const project = join(sandbox, "project");
const outside = join(sandbox, "outside");

function mustReject(label, operation) {
  let rejected = false;
  try {
    operation();
  } catch {
    rejected = true;
  }
  assert.equal(rejected, true, `${label} was not rejected`);
}

try {
  mkdirSync(project, { recursive: true });
  mkdirSync(outside, { recursive: true });
  mkdirSync(join(project, "tests"), { recursive: true });
  mkdirSync(join(project, "b3", "evals"), { recursive: true });
  mkdirSync(join(project, "b3", "proofs"), { recursive: true });
  mkdirSync(join(project, "b3", "agent_runs", "_tool_receipts"), { recursive: true });
  mkdirSync(join(project, ".ssh"), { recursive: true });
  mkdirSync(join(project, ".codex"), { recursive: true });
  mkdirSync(join(project, ".pi"), { recursive: true });
  mkdirSync(join(project, "config"), { recursive: true });
  mkdirSync(join(project, "scripts_b3"), { recursive: true });
  writeFileSync(join(project, "safe.txt"), "safe\n", "utf8");
  writeFileSync(join(project, ".env"), "TOKEN=do-not-read\n", "utf8");
  writeFileSync(join(project, "tests", "oracle.json"), "{}\n", "utf8");
  writeFileSync(join(project, "b3", "evals", "golden.json"), "{}\n", "utf8");
  writeFileSync(
    join(project, "b3", "proofs", "pi_science_agents_eval.json"),
    "{}\n",
    "utf8",
  );
  writeFileSync(
    join(project, "b3", "agent_runs", "_tool_receipts", "receipt.json"),
    "{}\n",
    "utf8",
  );
  writeFileSync(join(project, ".ssh", "id_ed25519"), "private\n", "utf8");
  writeFileSync(join(project, ".codex", "state.json"), "{}\n", "utf8");
  writeFileSync(join(project, ".pi", "control.md"), "control\n", "utf8");
  writeFileSync(join(project, "user.config"), "machine-local\n", "utf8");
  writeFileSync(join(project, "server.out.log"), "local log\n", "utf8");
  writeFileSync(join(project, "config", "credentials.json"), "{}\n", "utf8");
  writeFileSync(
    join(project, "scripts_b3", "evaluate_pi_science_agents.py"),
    "# protected evaluator\n",
    "utf8",
  );
  writeFileSync(join(outside, "secret.txt"), "outside\n", "utf8");
  symlinkSync(outside, join(project, "escape-link"), process.platform === "win32" ? "junction" : "dir");

  mkdirSync(join(nestedDecoy, "scripts_b3"), { recursive: true });
  mkdirSync(join(nestedDecoy, ".pi", "extensions"), { recursive: true });
  writeFileSync(join(nestedDecoy, "scripts_b3", "science_agent_cli.py"), "# decoy\n", "utf8");
  assert.equal(findB3ProjectRoot(nestedDecoy), realpathSync(process.cwd()));

  const safe = authorizeProjectPath(project, "safe.txt", "file");
  assert.equal(safe.relativePath, "safe.txt");
  assert.equal(isAbsolute(safe.absolutePath), true);
  assert.equal(isProtectedProjectRelativePath("docs/method.md"), false);
  assert.equal(isProtectedProjectRelativePath("."), false);
  assert.equal(isProtectedProjectRelativePath(".env.production"), true);
  assert.equal(isProtectedProjectRelativePath(".pi/agents/b3.md"), true);
  assert.equal(isProtectedProjectRelativePath("user.config"), true);
  assert.equal(isProtectedProjectRelativePath("server.out.log"), true);

  mustReject("absolute path", () =>
    authorizeProjectPath(project, resolve(outside, "secret.txt"), "file"),
  );
  mustReject("parent traversal", () =>
    authorizeProjectPath(project, "../outside/secret.txt", "file"),
  );
  mustReject("environment file", () =>
    authorizeProjectPath(project, ".env", "file"),
  );
  mustReject("test oracle", () =>
    authorizeProjectPath(project, "tests/oracle.json", "file"),
  );
  mustReject("evaluation oracle", () =>
    authorizeProjectPath(project, "b3/evals/golden.json", "file"),
  );
  mustReject("evaluation proof", () =>
    authorizeProjectPath(project, "b3/proofs/pi_science_agents_eval.json", "file"),
  );
  mustReject("tool receipt", () =>
    authorizeProjectPath(
      project,
      "b3/agent_runs/_tool_receipts/receipt.json",
      "file",
    ),
  );
  mustReject("ssh credential", () =>
    authorizeProjectPath(project, ".ssh/id_ed25519", "file"),
  );
  mustReject("machine-local config", () =>
    authorizeProjectPath(project, "user.config", "file"),
  );
  mustReject("hidden control state", () =>
    authorizeProjectPath(project, ".codex/state.json", "file"),
  );
  mustReject("Pi control plane", () =>
    authorizeProjectPath(project, ".pi/control.md", "file"),
  );
  mustReject("local log", () =>
    authorizeProjectPath(project, "server.out.log", "file"),
  );
  mustReject("credential filename", () =>
    authorizeProjectPath(project, "config/credentials.json", "file"),
  );
  mustReject("evaluation harness", () =>
    authorizeProjectPath(project, "scripts_b3/evaluate_pi_science_agents.py", "file"),
  );
  mustReject("link escape", () =>
    authorizeProjectPath(project, "escape-link/secret.txt", "file"),
  );

  process.stdout.write(
    `${JSON.stringify({
      schema_version: "b3-project-path-verifier-v1",
      passed: true,
      anchored_root_rejects_nested_decoy: true,
      rejected: [
        "absolute_path",
        "parent_traversal",
        "environment_file",
        "test_oracle",
        "evaluation_oracle",
        "evaluation_proof",
        "tool_receipt",
        "ssh_credential",
        "machine_local_config",
        "hidden_control_state",
        "pi_control_plane",
        "local_log",
        "credential_filename",
        "evaluation_harness",
        "link_or_junction_escape",
      ],
    })}\n`,
  );
} finally {
  if (!sandbox.startsWith(resolve(tmpdir()))) {
    throw new Error("Refusing to clean an unexpected verifier path");
  }
  rmSync(sandbox, { recursive: true, force: true });
  if (!nestedDecoy.startsWith(resolve(process.cwd()))) {
    throw new Error("Refusing to clean an unexpected nested verifier path");
  }
  rmSync(nestedDecoy, { recursive: true, force: true });
}
