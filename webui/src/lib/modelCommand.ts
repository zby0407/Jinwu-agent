// `/model` command for the chat composer.
//
// The actual model switching is handled server-side by the backend's
// `EvoScientist/middleware/configurable_model.py` middleware, which reads
// `model` / `model_provider` from each run's `RunnableConfig.configurable`.
// Note: backend path kept unchanged for compatibility.
// We just need to inject those fields into `stream.submit({ config: ... })`
// and persist the choice per-thread (in thread metadata, not localStorage —
// the choice should follow the conversation, not the browser tab).
//
// Listing available models: the backend's authoritative registry lives in
// `EvoScientist/llm/models.py` and is exposed at `GET /api/models` (mounted
// via langgraph.json). `useAvailableModels` fetches that endpoint at runtime;
// `COMMON_MODELS` below is a fallback for older deployments or network
// failures. Names outside the list are NOT rejected — `/model <name>` passes
// through verbatim and the middleware accepts anything `init_chat_model`
// recognises, so power users aren't blocked by our curation.

/** Thread metadata key carrying the per-thread model override. Mirrors the
 *  `idea_spark_*` keys we already write — the langgraph thread record is the
 *  authoritative store, so the choice survives reload, thread switch, and
 *  cross-device opens of the same thread. */
export const MODEL_OVERRIDE_METADATA_KEY = "model_override";

export interface ModelOverride {
  /** Short name as used by the backend's `MODELS` registry, e.g.
   *  "claude-sonnet-4-6" or "deepseek-v4-flash". */
  model: string;
  /** Provider routing hint. Optional — when omitted the backend uses the
   *  default provider for that short name. */
  model_provider?: string;
}

export interface CommonModelEntry extends ModelOverride {
  /** Human-readable label for the picker. Falls back to `model` when absent. */
  label?: string;
}

/**
 * Seed list for the `/model` picker. Not exhaustive — the backend's
 * `EvoScientist/llm/models.py` (backend path) has the full registry. This list is the
 * "I want to switch fast, don't make me look it up" subset; users can still
 * type any name the backend knows via `/model <name> [provider]`.
 */
export const COMMON_MODELS: ReadonlyArray<CommonModelEntry> = [
  { model: "claude-sonnet-4-6", model_provider: "anthropic" },
  { model: "claude-haiku-4-5", model_provider: "anthropic" },
  { model: "claude-opus-4-8", model_provider: "anthropic" },
  { model: "gpt-5.5-pro", model_provider: "openai" },
  { model: "gpt-5.5", model_provider: "openai" },
  { model: "gpt-5-mini", model_provider: "openai" },
  { model: "gemini-2.5-pro", model_provider: "google-genai" },
  { model: "gemini-3.5-flash", model_provider: "google-genai" },
  { model: "deepseek-v3.2", model_provider: "deepseek" },
];

export type ModelCommand =
  | { kind: "show" }
  | { kind: "reset" }
  | { kind: "set"; model: string; provider?: string };

/**
 * Parse a chat-composer input as a `/model` command.
 *
 * Recognised forms:
 *   - `/model`              → kind: "show" (open the picker)
 *   - `/model reset`        → kind: "reset" (clear thread override)
 *   - `/model <name>`       → kind: "set", model=<name>
 *   - `/model <name> <prov>`→ kind: "set", model=<name>, provider=<prov>
 *
 * Returns `null` for anything else, including a string that starts with
 * `/model` but has more than 2 args — we'd rather let the agent see a
 * misformatted command than guess. Trailing whitespace is tolerated.
 */
export function parseModelCommand(input: string): ModelCommand | null {
  const trimmed = input.trim();
  if (trimmed !== "/model" && !trimmed.startsWith("/model ")) return null;
  const rest = trimmed.slice("/model".length).trim();
  if (!rest) return { kind: "show" };
  const parts = rest.split(/\s+/);
  if (parts[0].toLowerCase() === "reset" && parts.length === 1) {
    return { kind: "reset" };
  }
  if (parts.length > 2) return null;
  return { kind: "set", model: parts[0], provider: parts[1] };
}
