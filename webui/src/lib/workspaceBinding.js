export async function bindThreadWorkspace(
  threadId,
  fetchImpl = globalThis.fetch
) {
  const response = await fetchImpl("/api/workspace/bind", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threadId, projectId: "default" }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.error || "无法绑定任务工作区。");
  }
  return payload?.binding ?? null;
}
