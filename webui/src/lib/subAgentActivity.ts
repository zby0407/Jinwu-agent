// Live sub-agent activity captured from subgraph stream events.
//
// With `streamSubgraphs: true`, useStream's `onUpdateEvent(data, { namespace })`
// fires for sub-agent (subgraph) node outputs. `namespace` looks like
// ["tools:<id>"]; `data` is `{ <nodeName>: { messages: [...] } }`. We turn those
// node outputs into a flat list of steps to render inside the sub-agent block.
//
// This data is LIVE-ONLY: it isn't persisted to thread state, so it's gone after
// a page reload (the block falls back to its INPUT/OUTPUT). Async sub-agents that
// run as separate deployed graphs aren't subgraphs of this run and won't appear.

export type SubAgentStep =
  | {
      kind: "tool_call";
      id: string;
      name: string;
      args: Record<string, unknown>;
    }
  | { kind: "tool_result"; toolCallId: string; name: string; text: string }
  | { kind: "text"; text: string };

function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === "object" && "text" in part
          ? String((part as { text?: unknown }).text ?? "")
          : ""
      )
      .join("");
  }
  return "";
}

/** Parse one subgraph update payload into renderable steps (empty nodes skipped). */
export function extractSubAgentSteps(data: unknown): SubAgentStep[] {
  const steps: SubAgentStep[] = [];
  if (!data || typeof data !== "object") return steps;
  for (const node of Object.values(data as Record<string, unknown>)) {
    const msgs = (node as { messages?: unknown })?.messages;
    if (!Array.isArray(msgs)) continue;
    for (const raw of msgs) {
      const m = raw as {
        type?: string;
        content?: unknown;
        name?: string;
        tool_call_id?: string;
        tool_calls?: { id?: string; name?: string; args?: unknown }[];
      };
      if (m.type === "ai" && m.tool_calls && m.tool_calls.length > 0) {
        for (const tc of m.tool_calls) {
          steps.push({
            kind: "tool_call",
            id: tc.id ?? "",
            name: tc.name ?? "tool",
            args:
              tc.args && typeof tc.args === "object"
                ? (tc.args as Record<string, unknown>)
                : {},
          });
        }
      } else if (m.type === "ai") {
        const text = extractText(m.content).trim();
        if (text) steps.push({ kind: "text", text });
      } else if (m.type === "tool") {
        steps.push({
          kind: "tool_result",
          toolCallId: m.tool_call_id ?? "",
          name: m.name ?? "tool",
          text: extractText(m.content).trim(),
        });
      }
    }
  }
  return steps;
}

/** Normalize a single AI message's tool calls (top-level `tool_calls` or the
 *  OpenAI-style `additional_kwargs.tool_calls`) into {id, name, args}. */
function normalizeToolCalls(m: {
  tool_calls?: { id?: string; name?: string; args?: unknown }[];
  additional_kwargs?: {
    tool_calls?: {
      id?: string;
      function?: { name?: string; arguments?: unknown };
    }[];
  };
}): { id: string; name: string; args: Record<string, unknown> }[] {
  const out: { id: string; name: string; args: Record<string, unknown> }[] = [];
  if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
    for (const tc of m.tool_calls) {
      out.push({
        id: tc.id ?? "",
        name: tc.name ?? "tool",
        args:
          tc.args && typeof tc.args === "object"
            ? (tc.args as Record<string, unknown>)
            : {},
      });
    }
    return out;
  }
  const ak = m.additional_kwargs?.tool_calls;
  if (Array.isArray(ak)) {
    for (const tc of ak) {
      let args: Record<string, unknown> = {};
      const raw = tc.function?.arguments;
      if (raw && typeof raw === "object") args = raw as Record<string, unknown>;
      else if (typeof raw === "string") {
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === "object") args = parsed;
        } catch {
          args = { input: raw };
        }
      }
      out.push({ id: tc.id ?? "", name: tc.function?.name ?? "tool", args });
    }
  }
  return out;
}

/**
 * Parse a PERSISTED flat messages array (e.g. an async sub-agent's own thread
 * state from `threads.getState`) into the same renderable steps as the live
 * stream parser — so the Agents board can show tool calls (with args + paired
 * results) and markdown text in order, exactly like the main agent. Human
 * messages are skipped (the first one is the task prompt, shown separately).
 */
export function messagesToSubAgentSteps(messages: unknown[]): SubAgentStep[] {
  const steps: SubAgentStep[] = [];
  for (const raw of messages) {
    const m = raw as {
      type?: string;
      content?: unknown;
      name?: string;
      tool_call_id?: string;
      tool_calls?: { id?: string; name?: string; args?: unknown }[];
      additional_kwargs?: {
        tool_calls?: {
          id?: string;
          function?: { name?: string; arguments?: unknown };
        }[];
      };
    };
    if (m.type === "ai") {
      // Reasoning text first, then the tool calls it issued (natural order).
      const text = extractText(m.content).trim();
      if (text) steps.push({ kind: "text", text });
      for (const tc of normalizeToolCalls(m)) {
        steps.push({
          kind: "tool_call",
          id: tc.id,
          name: tc.name,
          args: tc.args,
        });
      }
    } else if (m.type === "tool") {
      steps.push({
        kind: "tool_result",
        toolCallId: m.tool_call_id ?? "",
        name: m.name ?? "tool",
        text: extractText(m.content).trim(),
      });
    }
    // human messages: skipped (prompt is extracted separately)
  }
  return steps;
}

/** The last assistant text a sub-agent produced — used to bind it to a task block. */
export function lastTextOf(steps: SubAgentStep[]): string {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === "text") return s.text;
  }
  return "";
}
