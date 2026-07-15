import type { Message } from "@langchain/langgraph-sdk";

export function stringifyUnknown(value: unknown, space = 2): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "undefined";

  try {
    // Track the ancestor chain rather than every object already visited: the
    // same object appearing twice as a sibling is repetition, not a cycle.
    // The replacer's `this` is the object the current key belongs to, which
    // is what lets us unwind back to the right depth.
    const ancestors: object[] = [];
    const serialized = JSON.stringify(
      value,
      function (this: unknown, _key, nestedValue) {
        if (typeof nestedValue === "bigint") {
          return nestedValue.toString();
        }
        if (nestedValue && typeof nestedValue === "object") {
          while (
            ancestors.length > 0 &&
            ancestors[ancestors.length - 1] !== this
          ) {
            ancestors.pop();
          }
          if (ancestors.includes(nestedValue)) return "[Circular]";
          ancestors.push(nestedValue);
        }
        return nestedValue;
      },
      space
    );
    return serialized ?? String(value);
  } catch {
    return String(value);
  }
}

export function extractStringFromMessageContent(message: Message): string {
  return typeof message.content === "string"
    ? message.content
    : Array.isArray(message.content)
    ? message.content
        .filter(
          (c: unknown) =>
            (typeof c === "object" &&
              c !== null &&
              "type" in c &&
              (c as { type: string }).type === "text") ||
            typeof c === "string"
        )
        .map((c: unknown) =>
          typeof c === "string"
            ? c
            : typeof c === "object" && c !== null && "text" in c
            ? (c as { text?: string }).text || ""
            : ""
        )
        .join("")
    : "";
}

export function extractSubAgentContent(data: unknown): string {
  if (typeof data === "string") {
    return data;
  }

  if (data && typeof data === "object") {
    const dataObj = data as Record<string, unknown>;

    // An empty string must not win over the later fallbacks, or a task whose
    // `description` is blank renders nothing instead of its `prompt`.
    const firstNonEmpty = (key: string): string | null => {
      const candidate = dataObj[key];
      return typeof candidate === "string" && candidate.trim() !== ""
        ? candidate
        : null;
    };

    // Try to extract description first
    const description = firstNonEmpty("description");
    if (description !== null) return description;

    // Then try prompt
    const prompt = firstNonEmpty("prompt");
    if (prompt !== null) return prompt;

    // For output objects, try result
    const result = firstNonEmpty("result");
    if (result !== null) return result;

    // Fallback to JSON stringification
    return stringifyUnknown(data);
  }

  // Fallback for any other type
  return stringifyUnknown(data);
}
