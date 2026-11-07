import { test } from "node:test";
import assert from "node:assert/strict";
import { continuedTaskTurn } from "../src/lib/taskTurn.js";

test("approval carries the original explicit turn identity across reloads", () => {
  const config = { configurable: { project_id: "default", task_turn_started_at_ms: 9999 } };
  const durable = JSON.parse(JSON.stringify({ task_turn_started_at_ms: 1234 }));
  assert.deepEqual(continuedTaskTurn(config, durable.task_turn_started_at_ms), {
    configurable: { project_id: "default", task_turn_started_at_ms: 1234 },
  });
  assert.equal(config.configurable.task_turn_started_at_ms, 9999);
});

test("missing or invalid continuation never creates a fresh turn exemption", () => {
  for (const started of [undefined, null, NaN, Infinity, -1, 0, "1234", true]) {
    assert.deepEqual(continuedTaskTurn({ configurable: { task_turn_started_at_ms: 9999 } }, started), { configurable: {} });
  }
  // An old token stays old; the backend compares it with the stop marker.
  assert.equal(continuedTaskTurn({}, 1).configurable.task_turn_started_at_ms, 1);
});
