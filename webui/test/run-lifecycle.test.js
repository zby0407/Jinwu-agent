import assert from "node:assert/strict";
import test from "node:test";

import {
  isSubAgentRunning,
  isTerminalRunStatus,
  resumableRunStorageKey,
} from "../src/lib/runLifecycle.js";

test("classifies only settled LangGraph run statuses as terminal", () => {
  assert.equal(isTerminalRunStatus("pending"), false);
  assert.equal(isTerminalRunStatus("running"), false);
  assert.equal(isTerminalRunStatus("success"), true);
  assert.equal(isTerminalRunStatus("error"), true);
  assert.equal(isTerminalRunStatus("timeout"), true);
  assert.equal(isTerminalRunStatus("interrupted"), true);
});

test("matches the SDK resumable stream storage key", () => {
  assert.equal(
    resumableRunStorageKey("thread-123"),
    "lg:stream:thread-123"
  );
});

test("stops historical sub-agent spinners when the parent run settles", () => {
  assert.equal(isSubAgentRunning("pending", true), true);
  assert.equal(isSubAgentRunning("pending", false), false);
  assert.equal(isSubAgentRunning("success", true), false);
});
