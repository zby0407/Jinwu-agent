export interface ModelInfo {
  name: string;
  provider: string | null;
}

/**
 * Turn a raw model id from message metadata into something readable.
 *
 *   formatModel("anthropic/claude-4.8-opus-20260528", "anthropic")
 *     -> { name: "claude-4.8-opus", provider: "Anthropic" }
 *
 * Returns null when there's no usable model name.
 */
export function formatModel(
  modelName: unknown,
  modelProvider?: unknown
): ModelInfo | null {
  if (typeof modelName !== "string" || !modelName.trim()) return null;
  let name = modelName.trim();

  // Drop a "provider/" prefix if present (e.g. "anthropic/claude-…").
  const slash = name.lastIndexOf("/");
  const prefix = slash >= 0 ? name.slice(0, slash) : "";
  if (slash >= 0) name = name.slice(slash + 1);

  // Drop a trailing YYYYMMDD date stamp (e.g. "-20260528").
  name = name.replace(/-\d{8}$/, "");

  const providerRaw =
    (typeof modelProvider === "string" && modelProvider.trim()) || prefix;
  const provider = providerRaw
    ? providerRaw.charAt(0).toUpperCase() + providerRaw.slice(1)
    : null;

  return { name, provider };
}
