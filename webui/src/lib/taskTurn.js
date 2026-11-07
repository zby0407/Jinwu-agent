/** Continue the same user turn without minting a new cancellation exemption. */
export function continuedTaskTurn(config, started) {
  const configurable = { ...(config?.configurable ?? {}) };
  delete configurable.task_turn_started_at_ms;
  if (typeof started === "number" && Number.isFinite(started) && started > 0) {
    configurable.task_turn_started_at_ms = started;
  }
  return { ...config, configurable };
}
