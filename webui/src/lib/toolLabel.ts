/**
 * Targeted tool-call label overrides.
 *
 * We deliberately do NOT reformat tool calls in general (no `ls(path)` /
 * `execute(cmd)` argument summaries). Only a few specific names are remapped;
 * every other tool keeps its raw name.
 *
 * Overrides:
 *   - read_file / write_file / edit_file on a `/memories/...` path →
 *     "Reading memory" / "Updating memory".
 *   - read_file on a `/skills/<name>/...` path → "Skill: <name>".
 *   - think_tool → "Reflection".
 *   - the tool-selector's internal ToolSelectionResponse → "Adaptive tool
 *     selection".
 */

/** True when a virtual path targets the global memories directory. */
function isMemoryPath(path: string): boolean {
  const p = (path || "").trim();
  return p === "/memories" || p.startsWith("/memories/");
}

/** If a virtual path targets a skill (`/skills/<name>/...`), return `<name>`.
 *  An empty first segment (e.g. `/skills//x`) is treated as no match. */
function skillNameFromPath(path: string): string {
  const p = (path || "").trim();
  if (!p.startsWith("/skills/")) return "";
  const [skill] = p.slice("/skills/".length).split("/");
  return skill || "";
}

/** Coerce tool args to an object — they may stream in as a JSON string. */
function asArgsObject(args: unknown): Record<string, unknown> {
  if (args && typeof args === "object") return args as Record<string, unknown>;
  if (typeof args === "string") {
    try {
      const parsed = JSON.parse(args);
      if (parsed && typeof parsed === "object") {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Partial / non-JSON args while still streaming — treat as empty.
    }
  }
  return {};
}

export function formatToolLabel(name: string, args?: unknown): string {
  if (!name) return "Unknown Tool";

  // The tool-selector middleware's internal structured-output call.
  if (name === "ToolSelectionResponse") return "Adaptive tool selection";

  const nameLower = name.toLowerCase();

  if (nameLower === "think_tool") return "Reflection";

  if (
    nameLower === "read_file" ||
    nameLower === "write_file" ||
    nameLower === "edit_file"
  ) {
    const a = asArgsObject(args);
    const path = String(a.path ?? a.file_path ?? "");
    if (isMemoryPath(path)) {
      return nameLower === "read_file" ? "Reading memory" : "Updating memory";
    }
    if (nameLower === "read_file") {
      const skill = skillNameFromPath(path);
      if (skill) return `Skill: ${skill}`;
    }
  }

  // Everything else keeps its raw tool name.
  return name;
}
