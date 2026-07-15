// Detect file paths the agent emits as inline code and turn them into
// click-to-open links in chat. Intentionally conservative: matches only
// strings that BOTH contain `/` AND end with a known file extension, so
// inline code like `.md`, `field_name`, or a JSON key doesn't get
// underlined. Bare prose paths are out of scope (too noisy without the
// backtick signal).
//
// Dispatch flows through a custom DOM event so MarkdownContent doesn't need
// a context dependency — ChatInterface (or any parent) attaches the
// listener that opens the right dialog/view.

const KNOWN_EXTS = new Set<string>([
  "md",
  "txt",
  "json",
  "jsonl",
  "yaml",
  "yml",
  "csv",
  "tsv",
  "log",
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "html",
  "css",
  "sh",
  "pdf",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "svg",
  "webp",
  "mp3",
  "wav",
  "mp4",
  "mov",
]);

export type FileLinkKind = "workspace" | "memory";

export interface FileLink {
  kind: FileLinkKind;
  /** Path as displayed in the chat — preserves the leading slash. */
  display: string;
  /** Path passed to the open handler. Memory paths keep the `/memories/` prefix
   *  (the API expects virtual paths); workspace paths are stripped of any
   *  leading slash so they resolve relative to the workspace root. */
  path: string;
}

/**
 * Classify an inline-code string as a file link, or return null if it doesn't
 * look like one. Whitespace, scheme URLs (http:, mailto:), and strings without
 * both a `/` and a recognised extension are rejected.
 */
export function detectFileLink(text: string): FileLink | null {
  const s = text.trim();
  if (!s || /\s/.test(s)) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return null;
  if (!s.includes("/")) return null;
  const dot = s.lastIndexOf(".");
  if (dot <= s.lastIndexOf("/")) return null;
  const ext = s.slice(dot + 1).toLowerCase();
  if (!KNOWN_EXTS.has(ext)) return null;
  if (s.startsWith("/memories/")) {
    // The memory API resolves paths relative to the memory root, so strip the
    // virtual `/memories/` prefix the agent emits. Display keeps the prefix
    // since that's the form the user reads in chat.
    return {
      kind: "memory",
      display: s,
      path: s.replace(/^\/memories\/+/, ""),
    };
  }
  // Workspace API resolves relative to the workspace root. Strip both a
  // leading `/` (the absolute form the agent sometimes emits) and a leading
  // `./` (the explicit-relative form, e.g. `./attention.pdf`) so the server's
  // hidden-entry guard doesn't reject the `.` segment.
  const workspacePath = s.replace(/^\/+/, "").replace(/^\.\/+/, "");
  return { kind: "workspace", display: s, path: workspacePath };
}

export const FILE_LINK_EVENT = "evosci:open-file";

export type FileLinkEventDetail = FileLink;

export function dispatchFileLink(detail: FileLink): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<FileLinkEventDetail>(FILE_LINK_EVENT, { detail })
  );
}

/** Hash href prefix used by the rehype plugin below to mark bare-text path
 *  matches. The `MarkdownContent` <a> renderer keys on this to decide whether
 *  to dispatch a file-open event or render a normal external link. */
export const FILE_LINK_HREF_PREFIX = "#evosci-file:";

const EXT_GROUP = Array.from(KNOWN_EXTS).join("|");
// Match a path-shaped substring. Two accepted shapes (both guarantee at
// least one `/`, so plain `field_name.md` mid-sentence isn't picked up):
//   (a) leading `/`, then zero or more dir segments, then filename.ext
//       — covers `/final_report.md` and `/memories/.../notes.md`.
//   (b) no leading slash but at least one dir segment, then filename.ext
//       — covers `outputs/figure.png`.
// `\b` after the extension keeps trailing punctuation (".") out of the match.
const PATH_REGEX = new RegExp(
  `(?:\\/(?:[\\w\\-.]+\\/)*|(?:[\\w\\-.]+\\/)+)[\\w\\-.]+\\.(?:${EXT_GROUP})\\b`,
  "g"
);

const SKIP_PARENT_TAGS = new Set([
  "a",
  "code",
  "pre",
  "script",
  "style",
  "kbd",
  "samp",
]);

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

/**
 * Rehype plugin: replace bare-text occurrences of file paths with `<a>` nodes
 * whose href encodes the original path. The plugin runs BEFORE rehypeSanitize
 * so the resulting elements pass through the sanitizer normally — the
 * `#evosci-file:` href is just a hash link, which the default schema allows.
 * Element-tagged children (existing `<code>` etc.) are left untouched.
 */
export function rehypePathLinks() {
  const transform = (node: HastNode): void => {
    if (!node.children || node.children.length === 0) return;
    const out: HastNode[] = [];
    for (const child of node.children) {
      if (
        child.type === "element" &&
        SKIP_PARENT_TAGS.has(child.tagName ?? "")
      ) {
        out.push(child);
        continue;
      }
      if (child.type === "element") {
        transform(child);
        out.push(child);
        continue;
      }
      if (child.type !== "text" || typeof child.value !== "string") {
        out.push(child);
        continue;
      }
      const value = child.value;
      PATH_REGEX.lastIndex = 0;
      let m: RegExpExecArray | null;
      let cursor = 0;
      let replaced = false;
      while ((m = PATH_REGEX.exec(value)) !== null) {
        replaced = true;
        if (m.index > cursor) {
          out.push({ type: "text", value: value.slice(cursor, m.index) });
        }
        out.push({
          type: "element",
          tagName: "a",
          properties: {
            href: `${FILE_LINK_HREF_PREFIX}${encodeURIComponent(m[0])}`,
          },
          children: [{ type: "text", value: m[0] }],
        });
        cursor = m.index + m[0].length;
      }
      if (!replaced) {
        out.push(child);
        continue;
      }
      if (cursor < value.length) {
        out.push({ type: "text", value: value.slice(cursor) });
      }
    }
    node.children = out;
  };
  return (tree: HastNode) => transform(tree);
}
