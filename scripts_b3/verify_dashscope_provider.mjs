#!/usr/bin/env node
import assert from "node:assert/strict";
import registerDashScopeProvider, {
  configuredQwenModelId,
  configuredTemperature,
  validatedDashScopeBaseUrl,
} from "../.pi/extensions/dashscope-provider.ts";

const original = {
  B3_QWEN_BASE_URL: process.env.B3_QWEN_BASE_URL,
  B3_QWEN_MODEL: process.env.B3_QWEN_MODEL,
  B3_QWEN_TEMPERATURE: process.env.B3_QWEN_TEMPERATURE,
  B3_AGENT_MODEL: process.env.B3_AGENT_MODEL,
  DASHSCOPE_API_KEY: process.env.DASHSCOPE_API_KEY,
  QWEN_API_KEY: process.env.QWEN_API_KEY,
};

function restoreEnvironment() {
  for (const [name, value] of Object.entries(original)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

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
  delete process.env.B3_QWEN_BASE_URL;
  delete process.env.B3_QWEN_MODEL;
  delete process.env.B3_QWEN_TEMPERATURE;
  delete process.env.B3_AGENT_MODEL;
  delete process.env.DASHSCOPE_API_KEY;
  delete process.env.QWEN_API_KEY;
  assert.equal(
    validatedDashScopeBaseUrl(),
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
  );
  assert.equal(configuredQwenModelId(), "qwen3.7-max-2026-06-08");
  assert.equal(configuredTemperature(), undefined);

  for (const endpoint of [
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1/",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://example.maas.aliyuncs.com/compatible-mode/v1",
  ]) {
    process.env.B3_QWEN_BASE_URL = endpoint;
    assert.match(validatedDashScopeBaseUrl(), /^https:\/\//);
  }
  for (const endpoint of [
    "http://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://example.com/compatible-mode/v1",
    "https://user:password@dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1?token=secret",
    "https://dashscope.aliyuncs.com/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1//",
    "https://dashscope.aliyuncs.com:8443/compatible-mode/v1",
  ]) {
    process.env.B3_QWEN_BASE_URL = endpoint;
    mustReject(`endpoint ${endpoint}`, validatedDashScopeBaseUrl);
  }

  process.env.B3_AGENT_MODEL = "dashscope/qwen3.7-max-2026-06-08";
  for (const model of [
    "qwen-max",
    "qwen3.7-plus/../../evil",
    "qwen3.7-plus-latest",
    "qwen3.7-plus-2026-05-26",
    "qwen3.6-flash-latest",
    "qwen3.7-max-2026-02-30",
  ]) {
    process.env.B3_QWEN_MODEL = model;
    mustReject(`model ${model}`, configuredQwenModelId);
  }

  process.env.B3_QWEN_MODEL = "qwen3.7-plus-2026-05-26";
  mustReject("divergent B3 agent and provider routes", configuredQwenModelId);

  for (const value of ["nan", "-0.1", "2.1", "1;drop"]) {
    process.env.B3_QWEN_TEMPERATURE = value;
    mustReject(`temperature ${value}`, configuredTemperature);
  }
  process.env.B3_QWEN_TEMPERATURE = "0.2";
  assert.equal(configuredTemperature(), 0.2);

  delete process.env.B3_QWEN_BASE_URL;
  delete process.env.B3_QWEN_MODEL;
  delete process.env.B3_AGENT_MODEL;
  process.env.DASHSCOPE_API_KEY = "sentinel-must-not-be-copied";
  let registration;
  let payloadHandler;
  registerDashScopeProvider({
    registerProvider(name, config) {
      registration = { name, config };
    },
    on(name, handler) {
      if (name === "before_provider_request") payloadHandler = handler;
    },
  });
  assert.equal(registration.name, "dashscope");
  assert.equal(registration.config.models[0].id, "qwen3.7-max-2026-06-08");
  assert.match(registration.config.models[0].name, /Qwen3\.7 Max/);
  assert.equal(registration.config.models[1].id, "qwen3.7-plus-2026-05-26");
  assert.match(registration.config.models[1].name, /Qwen3\.7 Plus/);
  assert.equal(registration.config.models[2].id, "qwen3.6-flash-2026-04-16");
  assert.match(registration.config.models[2].name, /Qwen3\.6 Flash/);
  assert.equal(registration.config.apiKey, "$DASHSCOPE_API_KEY");
  assert.notEqual(registration.config.apiKey, process.env.DASHSCOPE_API_KEY);
  assert.equal(
    registration.config.models[0].compat.maxTokensField,
    "max_completion_tokens",
  );
  assert.equal(registration.config.models[0].compat.thinkingFormat, "qwen");
  assert.deepEqual(
    payloadHandler({ payload: { model: "qwen3.7-max-2026-06-08", messages: [] } }),
    { model: "qwen3.7-max-2026-06-08", messages: [], temperature: 0.2 },
  );

  process.env.B3_AGENT_MODEL = "dashscope/qwen3.7-plus-2026-05-26";
  process.env.B3_QWEN_MODEL = "qwen3.7-plus-2026-05-26";
  let optionalRegistration;
  registerDashScopeProvider({
    registerProvider(name, config) {
      optionalRegistration = { name, config };
    },
    on() {},
  });
  assert.equal(optionalRegistration.name, "dashscope");

  process.env.B3_AGENT_MODEL = "kimi-coding/kimi-for-coding";
  delete process.env.B3_QWEN_MODEL;
  mustReject("removed Kimi route", () =>
    registerDashScopeProvider({ registerProvider() {}, on() {} }),
  );

  process.stdout.write(
    `${JSON.stringify({
      schema_version: "b3-dashscope-provider-verifier-v1",
      passed: true,
      official_endpoint_allowlist: true,
      model_allowlist: true,
      resolved_default_registration: true,
      divergent_routes_rejected: true,
      qwen_max_default: true,
      optional_qwen_plus: true,
      optional_qwen_flash: true,
      kimi_route_rejected: true,
      credential_reference_only: true,
      qwen_completion_token_field: true,
      fixed_temperature_payload: true,
    })}\n`,
  );
} finally {
  restoreEnvironment();
}
