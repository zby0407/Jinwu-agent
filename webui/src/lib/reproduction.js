export const REPRODUCTION_SUITE_ID = "solar-h1-h2-v1";
export const REPRODUCTION_SCHEMA_VERSION = "jw-reproduction-launch-v1";

function validRun(value) {
  return (
    value &&
    typeof value === "object" &&
    (value.case_id === "H1" || value.case_id === "H2") &&
    typeof value.thread_id === "string" &&
    typeof value.run_id === "string" &&
    typeof value.workspace === "string" &&
    typeof value.prompt_sha256 === "string"
  );
}

export async function launchSolarH1H2({
  deploymentUrl,
  apiKey = "",
  fetchImpl = globalThis.fetch,
}) {
  const url = deploymentUrl.replace(/\/$/, "");
  const headers = {
    "Content-Type": "application/json",
    "X-JW-Reproduction-Intent": REPRODUCTION_SUITE_ID,
  };
  if (apiKey) headers["X-Api-Key"] = apiKey;

  const response = await fetchImpl(`${url}/api/reproductions/solar-h1-h2`, {
    method: "POST",
    headers,
    body: JSON.stringify({ trigger: "webui" }),
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`复现接口返回了非 JSON 响应（HTTP ${response.status}）`);
  }
  if (!response.ok && response.status !== 207) {
    throw new Error(body?.error || `复现调度失败（HTTP ${response.status}）`);
  }
  if (
    !body ||
    body.schema_version !== REPRODUCTION_SCHEMA_VERSION ||
    body.suite_id !== REPRODUCTION_SUITE_ID ||
    !["submitted", "partial", "failed"].includes(body.status) ||
    !Array.isArray(body.runs) ||
    !body.runs.every(validRun) ||
    !Array.isArray(body.errors)
  ) {
    throw new Error("复现接口响应格式无效");
  }
  return body;
}
