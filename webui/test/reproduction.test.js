import assert from "node:assert/strict";
import test from "node:test";

import {
  REPRODUCTION_SUITE_ID,
  launchSolarH1H2,
} from "../src/lib/reproduction.js";

const launch = {
  schema_version: "jw-reproduction-launch-v1",
  suite_id: "solar-h1-h2-v1",
  batch_id: "batch-1",
  status: "submitted",
  model: { name: "qwen3.7-max", provider: "dashscope" },
  runs: [
    {
      case_id: "H1",
      thread_id: "thread-1",
      run_id: "run-1",
      workspace: "C:/workspace/h1",
      prompt_sha256: "a".repeat(64),
    },
    {
      case_id: "H2",
      thread_id: "thread-2",
      run_id: "run-2",
      workspace: "C:/workspace/h2",
      prompt_sha256: "b".repeat(64),
    },
  ],
  errors: [],
};

test("launchSolarH1H2 sends the fixed endpoint, intent and body", async () => {
  let request;
  const result = await launchSolarH1H2({
    deploymentUrl: "http://localhost:6174/",
    apiKey: "local-key",
    fetchImpl: async (url, init) => {
      request = { url, init };
      return { ok: true, status: 201, json: async () => launch };
    },
  });
  assert.equal(
    request.url,
    "http://localhost:6174/api/reproductions/solar-h1-h2"
  );
  assert.equal(request.init.method, "POST");
  assert.equal(
    request.init.headers["X-JW-Reproduction-Intent"],
    REPRODUCTION_SUITE_ID
  );
  assert.equal(request.init.headers["X-Api-Key"], "local-key");
  assert.deepEqual(JSON.parse(request.init.body), { trigger: "webui" });
  assert.equal(result.status, "submitted");
});

test("launchSolarH1H2 preserves a valid partial response", async () => {
  const result = await launchSolarH1H2({
    deploymentUrl: "http://localhost:6174",
    fetchImpl: async () => ({
      ok: true,
      status: 207,
      json: async () => ({
        ...launch,
        status: "partial",
        runs: launch.runs.slice(0, 1),
        errors: [{ case_id: "H2", stage: "submit", message: "failed" }],
      }),
    }),
  });
  assert.equal(result.status, "partial");
  assert.equal(result.runs.length, 1);
  assert.equal(result.errors[0].case_id, "H2");
});

test("launchSolarH1H2 surfaces backend and schema errors", async () => {
  await assert.rejects(
    launchSolarH1H2({
      deploymentUrl: "http://localhost:6174",
      fetchImpl: async () => ({
        ok: false,
        status: 403,
        json: async () => ({ error: "dangerous mode" }),
      }),
    }),
    /dangerous mode/
  );
  await assert.rejects(
    launchSolarH1H2({
      deploymentUrl: "http://localhost:6174",
      fetchImpl: async () => ({
        ok: true,
        status: 201,
        json: async () => ({}),
      }),
    }),
    /响应格式无效/
  );
});
