// Conversation-compaction (context summarization) support.
//
// When the backend (deepagents SummarizationMiddleware) compacts the
// conversation to stay under the context window, it does NOT rewrite the
// persisted `messages` — the full history stays intact. Instead it records the
// summary in a private state field `_summarization_event`:
//
//   {
//     cutoff_index: number,          // summarized messages[0:cutoff_index]
//     summary_message: HumanMessage, // additional_kwargs.lc_source="summarization"
//     file_path: string | null,      // offloaded full history (e.g. /conversation_history/<tid>.md)
//   }
//
// langgraph dev exposes this underscore-prefixed key over the SDK (verified on
// the live backend), so the UI can surface the summary as a tidy collapsible
// block instead of letting it flash by mid-stream and vanish.
//
// The summary_message content is wrapped in one of two templates (deepagents
// `_build_new_messages_with_path`):
//
//   "You are in the middle of a conversation that has been summarized.\n\n
//    The full conversation history has been saved to {path} ...\n\n
//    A condensed summary follows:\n\n<summary>\n{body}\n</summary>"
//
//   or, when offload failed:
//
//   "Here is a summary of the conversation to date:\n\n{body}"
//
// `{body}` is the LLM-authored summary (## SESSION INTENT / ## SUMMARY /
// ## ARTIFACTS / ## NEXT STEPS).

import type { Message } from "@langchain/langgraph-sdk";
import { extractStringFromMessageContent } from "@/app/utils/utils";

export interface SummarizationEvent {
  cutoffIndex: number;
  /** The raw summary message content (wrapper + body). */
  content: string;
  /** Path where the full history was offloaded, or null. */
  filePath: string | null;
}

/**
 * The marker deepagents/langchain stamps on the summary HumanMessage's
 * `additional_kwargs`. Most reliable signal when the summary lands in
 * `messages` (langchain `before_model` path) rather than `_summarization_event`.
 */
function isSummarizationMarkerMessage(message: Message): boolean {
  if (message.type !== "human") return false;
  const ak = (message as { additional_kwargs?: Record<string, unknown> })
    .additional_kwargs;
  return ak?.["lc_source"] === "summarization";
}

/**
 * Content-pattern fallback: true when the text looks like a compaction summary.
 * Matches the wrapper templates OR the default summary section structure
 * (so it also catches a transient mid-stream summary that carries no marker).
 */
export function isSummarizationContent(
  text: string | null | undefined
): boolean {
  if (!text) return false;
  if (
    /\bconversation that has been summarized\b/.test(text) ||
    /\bHere is a summary of the conversation to date\b/.test(text) ||
    /<summary>[\s\S]*<\/summary>/.test(text)
  ) {
    return true;
  }
  // The summary always OPENS with "## SESSION INTENT" (first section of the
  // DEFAULT_SUMMARY_PROMPT). Match on that lead header alone so detection fires
  // the moment it streams in — requiring all sections (… NEXT STEPS) let the
  // partially-streamed summary leak into the transcript until the last header
  // arrived. "SESSION INTENT" is summarization-specific vocabulary; normal
  // assistant answers don't emit it, so a single-header match is safe.
  return /##\s+SESSION INTENT\b/.test(text);
}

/**
 * True when a message is a compaction summary.
 *
 * Human messages are matched ONLY by the `lc_source` marker (the persisted
 * summary carries it) — never by content — so a user who happens to type
 * "## SESSION INTENT" is not silently dropped from the transcript. The transient
 * mid-stream summary leak is an AI message with no marker, so it is matched by
 * content via the shared extractor (which also flattens array/content-block
 * message shapes that a bare `typeof content === "string"` check would miss).
 */
export function isSummarizationMessage(message: Message): boolean {
  if (message.type === "human") return isSummarizationMarkerMessage(message);
  if (message.type !== "ai") return false;
  return isSummarizationContent(extractStringFromMessageContent(message));
}

/**
 * Strip the wrapper templates and return just the LLM-authored summary body
 * (## SESSION INTENT …). Falls back to the trimmed input when no wrapper is
 * recognized.
 */
export function extractSummaryBody(raw: string): string {
  if (!raw) return "";
  // Prefer the explicit <summary>…</summary> region.
  const tagged = raw.match(/<summary>\s*([\s\S]*?)\s*<\/summary>/);
  if (tagged) return tagged[1].trim();
  // "Here is a summary of the conversation to date:\n\n{body}"
  const lead = raw.match(
    /Here is a summary of the conversation to date:\s*([\s\S]*)$/
  );
  if (lead) return lead[1].trim();
  return raw.trim();
}

/**
 * Validate + normalize the raw `_summarization_event` from thread state.
 * Returns null when absent or malformed.
 */
export function parseSummarizationEvent(
  raw: unknown
): SummarizationEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const ev = raw as Record<string, unknown>;
  const sm = ev["summary_message"] as
    | { content?: unknown; additional_kwargs?: Record<string, unknown> }
    | undefined;
  const content = typeof sm?.content === "string" ? sm.content : "";
  if (!content) return null;
  const cutoff = ev["cutoff_index"];
  const filePath = ev["file_path"];
  return {
    cutoffIndex: typeof cutoff === "number" && cutoff >= 0 ? cutoff : 0,
    content,
    filePath: typeof filePath === "string" ? filePath : null,
  };
}
