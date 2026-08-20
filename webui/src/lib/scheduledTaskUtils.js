const LIMIT_PATTERNS = [
  /model call limits? exceeded/i,
  /run limit\s*\(\d+\/\d+\)/i,
  /模型调用(?:次数)?.*(?:达到|超过).*(?:上限|限制)/i,
];

const TIMEOUT_PATTERNS = [/timed?\s*out/i, /timeout/i, /超时/];
const INTERRUPT_PATTERNS = [/\binterrupt(?:ed)?\b/i, /用户(?:停止|中断)/];

export function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function createTaskKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `scheduled-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function messageText(message) {
  if (!message || typeof message !== "object") return "";
  const content = message.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (typeof part === "string") return part;
      if (!part || typeof part !== "object") return "";
      return typeof part.text === "string" ? part.text : "";
    })
    .filter(Boolean)
    .join("\n");
}

function messageRole(message) {
  if (!message || typeof message !== "object") return "";
  return String(message.role ?? message.type ?? "").toLowerCase();
}

export function initialScheduledPrompt(values) {
  const messages = Array.isArray(values?.messages) ? values.messages : [];
  const firstHuman = messages.find((message) => {
    const role = messageRole(message);
    return role === "user" || role === "human";
  });
  return messageText(firstHuman).trim();
}

export function scheduledPromptFromRun(run) {
  if (!run || typeof run !== "object") return "";
  const metadataPrompt = run.metadata?.prompt;
  if (typeof metadataPrompt === "string" && metadataPrompt.trim()) {
    return metadataPrompt.trim();
  }
  const messages = run.kwargs?.input?.messages;
  if (!Array.isArray(messages)) return "";
  const firstHuman = messages.find((message) => {
    const role = messageRole(message);
    return role === "user" || role === "human";
  });
  return messageText(firstHuman).trim();
}

export function finalScheduledFeedback(values) {
  const messages = Array.isArray(values?.messages) ? values.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const role = messageRole(messages[index]);
    if (role === "assistant" || role === "ai") {
      const text = messageText(messages[index]).trim();
      if (text) return text;
    }
  }
  return "";
}

export function classifyScheduledRunStatus(runStatus, feedback = "") {
  const normalized = String(runStatus ?? "").toLowerCase();
  if (normalized === "pending" || normalized === "queued") return "pending";
  if (normalized === "running") return "running";
  if (normalized === "timeout" || TIMEOUT_PATTERNS.some((re) => re.test(feedback))) {
    return "timeout";
  }
  if (
    normalized === "interrupted" ||
    normalized === "cancelled" ||
    INTERRUPT_PATTERNS.some((re) => re.test(feedback))
  ) {
    return "interrupted";
  }
  if (
    normalized === "error" ||
    normalized === "failed" ||
    LIMIT_PATTERNS.some((re) => re.test(feedback))
  ) {
    return "failed";
  }
  if (normalized === "success") return "completed";
  return "unknown";
}

export function legacyTaskKeyForPrompt(tasks, prompt) {
  const exact = tasks.filter((task) => task.prompt === prompt);
  return exact.length === 1 ? exact[0].task_key : null;
}

export function needsScheduledTaskMigration(task) {
  return Boolean(
    !task.task_key ||
      !task.timezone ||
      task.on_run_completed !== "keep" ||
      typeof task.enabled !== "boolean"
  );
}
