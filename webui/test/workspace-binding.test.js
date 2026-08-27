import assert from "node:assert/strict";
import test from "node:test";

import { bindThreadWorkspace } from "../src/lib/workspaceBinding.js";

test("binds a new thread before workspace-backed status polling", async () => {
  const calls = [];
  const binding = await bindThreadWorkspace("thread-1", async (...args) => {
    calls.push(args);
    return {
      ok: true,
      async json() {
        return { binding: { workspace: "/workspace/run-1" } };
      },
    };
  });

  assert.deepEqual(calls, [
    [
      "/api/workspace/bind",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId: "thread-1", projectId: "default" }),
      },
    ],
  ]);
  assert.deepEqual(binding, { workspace: "/workspace/run-1" });
});

test("surfaces a workspace binding rejection", async () => {
  await assert.rejects(
    bindThreadWorkspace("thread-1", async () => ({
      ok: false,
      async json() {
        return { error: "binding rejected" };
      },
    })),
    /binding rejected/
  );
});
