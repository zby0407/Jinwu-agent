const FILE_PATH_PATTERN =
  /(?:\/(?:[\w\-.]+\/)*|(?:[\w\-.]+\/)+)[\w\-.]+\.(?:md|txt|json|jsonl|ya?ml|csv|tsv|log|py|tsx?|jsx?|html|css|sh|pdf|png|jpe?g|gif|svg|webp|mp3|wav|mp4|mov)\b/gi;
const FILE_NAME_PATTERN =
  /^[\w\-.]+\.(?:md|txt|json|jsonl|ya?ml|csv|tsv|log|py|tsx?|jsx?|html|css|sh|pdf|png|jpe?g|gif|svg|webp|mp3|wav|mp4|mov)$/i;

/** @param {unknown} content */
export function extractLineageText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      part && typeof part === "object" && typeof part.text === "string"
        ? part.text
        : ""
    )
    .join("");
}

/** @param {unknown} value */
export function extractLineageFiles(value) {
  const text =
    typeof value === "string"
      ? value
      : (() => {
          try {
            return JSON.stringify(value ?? "");
          } catch {
            return "";
          }
        })();
  const matches = text.match(FILE_PATH_PATTERN) ?? [];
  const files = new Set(
    matches.map((path) => path.replace(/^["']|["']$/g, ""))
  );
  if (value && typeof value === "object") {
    const seen = new WeakSet();
    const visit = (candidate, key = "") => {
      if (typeof candidate === "string") {
        if (
          /(?:^|_)(?:path|file|filename)$/i.test(key) &&
          FILE_NAME_PATTERN.test(candidate.trim())
        ) {
          files.add(candidate.trim());
        }
        return;
      }
      if (!candidate || typeof candidate !== "object" || seen.has(candidate)) {
        return;
      }
      seen.add(candidate);
      if (Array.isArray(candidate)) {
        for (const item of candidate) visit(item, key);
      } else {
        for (const [childKey, child] of Object.entries(candidate)) {
          visit(child, childKey);
        }
      }
    };
    visit(value);
  }
  return [...files];
}

function parseArgs(raw) {
  if (raw && typeof raw === "object") return raw;
  if (typeof raw !== "string") return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : { input: raw };
  } catch {
    return { input: raw };
  }
}

/** @param {Record<string, any>} message */
export function normalizeLineageToolCalls(message) {
  if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
    return message.tool_calls.map((call) => ({
      id: call?.id ?? "",
      name: call?.name ?? "tool",
      args: parseArgs(call?.args ?? call?.input),
    }));
  }
  const calls = message.additional_kwargs?.tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls.map((call) => ({
    id: call?.id ?? "",
    name: call?.function?.name ?? call?.name ?? "tool",
    args: parseArgs(call?.function?.arguments ?? call?.args ?? call?.input),
  }));
}

function compact(text, limit = 100) {
  const normalized = String(text ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return normalized.length > limit
    ? `${normalized.slice(0, limit - 1)}…`
    : normalized;
}

function resultStatus(message, output) {
  const explicit = String(message.status ?? "").toLowerCase();
  if (explicit === "error" || explicit === "failed") return "failed";
  if (/\b(cancelled|canceled|error|failed)\b|已取消|失败/i.test(output)) {
    return /cancel|已取消/i.test(output) ? "cancelled" : "failed";
  }
  return "complete";
}

/**
 * Convert persisted visible messages into per-user-turn research details.
 * Hidden model reasoning is intentionally not inferred or synthesized.
 * @param {unknown[]} rawMessages
 * @param {Record<string, string>} [stateFiles]
 */
export function buildResearchTurns(rawMessages, stateFiles = {}) {
  const turns = [];
  const callsById = new Map();
  let current = null;

  for (let index = 0; index < rawMessages.length; index += 1) {
    const message = rawMessages[index];
    if (!message || typeof message !== "object") continue;
    const type = message.type;
    const messageId = String(message.id ?? `message-${index}`);
    const text = extractLineageText(message.content).trim();

    if (type === "human") {
      current = {
        id: messageId,
        messageId,
        title: compact(text, 90) || "未命名研究问题",
        prompt: text,
        status: "complete",
        nodes: [],
        files: [],
      };
      turns.push(current);
      continue;
    }
    if (!current) continue;

    if (type === "ai") {
      if (text) {
        current.nodes.push({
          id: messageId,
          kind: "answer",
          messageId,
          title: "主 Agent 回答",
          summary: compact(text, 120),
          detail: text,
          status: "complete",
          files: extractLineageFiles(text),
        });
      }
      for (const call of normalizeLineageToolCalls(message)) {
        const isAgent = call.name === "task";
        const agentType =
          typeof call.args.subagent_type === "string"
            ? call.args.subagent_type
            : "子 Agent";
        const description =
          typeof call.args.description === "string"
            ? call.args.description
            : typeof call.args.prompt === "string"
            ? call.args.prompt
            : "";
        const node = {
          id: call.id || `${messageId}-call-${current.nodes.length}`,
          kind: isAgent ? "agent" : "tool",
          messageId,
          toolCallId: call.id,
          name: call.name,
          title: isAgent ? agentType : call.name,
          summary: compact(description || JSON.stringify(call.args), 120),
          args: call.args,
          detail: "",
          status: "running",
          files: extractLineageFiles(call.args),
        };
        current.nodes.push(node);
        if (call.id) callsById.set(call.id, node);
      }
      continue;
    }

    if (type === "tool") {
      const callId = String(message.tool_call_id ?? "");
      const existing = callsById.get(callId);
      if (existing) {
        existing.detail = text;
        existing.status = resultStatus(message, text);
        existing.files = [
          ...new Set([...existing.files, ...extractLineageFiles(text)]),
        ];
      } else {
        current.nodes.push({
          id: messageId,
          kind: "tool",
          messageId,
          toolCallId: callId,
          name: message.name ?? "tool",
          title: message.name ?? "工具结果",
          summary: compact(text, 120),
          detail: text,
          status: resultStatus(message, text),
          files: extractLineageFiles(text),
        });
      }
    }
  }

  const persistedFiles = Object.keys(stateFiles ?? {});
  if (turns.length > 0 && persistedFiles.length > 0) {
    const lastTurn = turns.at(-1);
    const referenced = new Set(
      turns.flatMap((turn) => turn.nodes.flatMap((node) => node.files))
    );
    for (const path of persistedFiles) {
      if (!referenced.has(path)) lastTurn.files.push(path);
    }
  }

  for (const turn of turns) {
    turn.files = [
      ...new Set([
        ...turn.files,
        ...turn.nodes.flatMap((node) => node.files ?? []),
      ]),
    ];
    const statuses = turn.nodes.map((node) => node.status);
    turn.status = statuses.includes("running")
      ? "running"
      : statuses.includes("failed")
      ? "failed"
      : statuses.includes("cancelled")
      ? "cancelled"
      : "complete";
  }
  return turns;
}

/**
 * Collect the latest state for every route in the SDK branch tree.
 * @param {{type?: string, items?: any[]}|null|undefined} tree
 */
export function collectResearchRoutes(tree) {
  const latestByPath = new Map();
  const visit = (item) => {
    if (!item || typeof item !== "object") return;
    if (item.type === "node") {
      const path = Array.isArray(item.path) ? item.path.join(">") : "";
      latestByPath.set(path, item.value);
      return;
    }
    for (const child of item.items ?? []) visit(child);
  };
  visit(tree);
  const paths = [...latestByPath.keys()];
  const leafPaths = paths.filter(
    (path) =>
      !paths.some(
        (candidate) =>
          candidate !== path &&
          (path === "" || candidate.startsWith(`${path}>`))
      )
  );
  return leafPaths.map((path) => {
    const state = latestByPath.get(path);
    return {
      path,
      checkpointId: state?.checkpoint?.checkpoint_id ?? null,
      createdAt: state?.created_at ?? null,
      messages: Array.isArray(state?.values?.messages)
        ? state.values.messages
        : [],
    };
  });
}

/**
 * Append a checkpoint page without duplicating an overlapping cursor item.
 * @template T
 * @param {T[]} existing
 * @param {T[]} page
 * @param {(item: T) => string | null | undefined} checkpointIdOf
 */
export function mergeCheckpointHistory(existing, page, checkpointIdOf) {
  const seen = new Set(existing.map(checkpointIdOf).filter(Boolean));
  const uniquePage = page.filter((item) => {
    const id = checkpointIdOf(item);
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return { merged: [...existing, ...uniquePage], added: uniquePage.length };
}
