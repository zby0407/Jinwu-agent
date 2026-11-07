// Preserve durable evidence when saving the last visible partial response.
export function mergeStoppedMessages(persisted, live) {
  const merged = [...(persisted ?? [])];
  const indexes = new Map(merged.map((message, index) => [message.id, index]));
  for (const message of live ?? []) {
    const index = message.id ? indexes.get(message.id) : undefined;
    if (index !== undefined) {
      const saved = merged[index];
      // In particular, a stale streaming error/cancellation must never replace
      // a checkpointed successful tool result with the same message id.
      if (saved.type === "ai" && !saved.content && !saved.tool_calls?.length &&
          message.type === "ai" && !message.tool_calls?.length) merged[index] = message;
      continue;
    }
    if (message.type === "tool") continue;
    if (message.type === "ai" && message.tool_calls?.length) continue;
    if (message.id) indexes.set(message.id, merged.length);
    merged.push(message);
  }
  return merged;
}
