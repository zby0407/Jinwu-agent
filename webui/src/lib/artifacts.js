const CATEGORY_EXTENSIONS = {
  documents: new Set([
    "pdf",
    "tex",
    "bib",
    "md",
    "markdown",
    "txt",
    "doc",
    "docx",
    "rtf",
    "odt",
  ]),
  figures: new Set([
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "webp",
    "bmp",
    "tif",
    "tiff",
    "eps",
  ]),
  data: new Set([
    "json",
    "jsonl",
    "csv",
    "tsv",
    "xlsx",
    "xls",
    "parquet",
    "pkl",
    "npy",
    "npz",
    "h5",
    "hdf5",
    "db",
    "sqlite",
    "yaml",
    "yml",
    "xml",
  ]),
  code: new Set([
    "py",
    "ipynb",
    "js",
    "jsx",
    "ts",
    "tsx",
    "sh",
    "bash",
    "r",
    "jl",
    "cpp",
    "cc",
    "c",
    "h",
    "hpp",
    "java",
    "go",
    "rs",
    "m",
    "rb",
  ]),
};

const ROOT_ARTIFACT_DIRS = new Set([
  "outputs",
  "artifacts",
  "reports",
  "results",
]);
const EXCLUDED_SEGMENTS = new Set([
  "inputs",
  "work",
  "receipts",
  "attempts",
  "stage_artifacts",
  "research_review",
  "large_tool_results",
  "conversation_history",
]);
const EXCLUDED_FILES = new Set([
  "worker_result.json",
  "task.json",
  "input_manifest.json",
  "context_snapshot.json",
  "state.json",
  "request.json",
  "record.json",
  "entry_result.json",
]);

export function normalizeArtifactPath(value) {
  if (typeof value !== "string" || /[\0-\x1f\x7f]/.test(value)) return null;
  const normalized = value.replaceAll("\\", "/").replace(/^\.\/+/, "");
  if (!normalized || normalized.startsWith("/") || normalized.endsWith("/"))
    return null;
  const segments = normalized.split("/");
  if (
    segments.some((segment) => !segment || segment === "." || segment === "..")
  )
    return null;
  return segments.join("/");
}

export function artifactCategory(ext) {
  const normalized = String(ext || "")
    .toLowerCase()
    .replace(/^\./, "");
  for (const [category, extensions] of Object.entries(CATEGORY_EXTENSIONS)) {
    if (extensions.has(normalized)) return category;
  }
  return "other";
}

export function artifactSource(path) {
  const normalized = normalizeArtifactPath(path);
  if (!normalized) return null;
  const segments = normalized.split("/");
  const lower = segments.map((segment) => segment.toLowerCase());
  const fileName = lower.at(-1);
  if (
    lower.some((segment) => EXCLUDED_SEGMENTS.has(segment)) ||
    EXCLUDED_FILES.has(fileName) ||
    lower.some((segment) => segment.startsWith("."))
  ) {
    return null;
  }

  if (ROOT_ARTIFACT_DIRS.has(lower[0])) {
    return lower[0] === "outputs" ? "outputs" : "legacy";
  }
  if (lower.length >= 4 && lower[0] === "experiment" && lower[1] === "runs") {
    if (lower.length === 4 && fileName === "report.md")
      return "experiment-report";
    if (lower[3] === "public" && lower.length > 4) return "experiment-public";
  }
  return null;
}

export function sortAndDedupeArtifacts(
  entries,
  limit = Number.POSITIVE_INFINITY
) {
  const byPath = new Map();
  for (const entry of entries) {
    const path = normalizeArtifactPath(entry?.path);
    const source = path && artifactSource(path);
    if (!path || !source) continue;
    const ext = String(entry.ext || path.split(".").pop() || "").toLowerCase();
    const normalized = {
      ...entry,
      path,
      name: path.split("/").pop(),
      ext,
      category: artifactCategory(ext),
      source,
    };
    const previous = byPath.get(path);
    if (
      !previous ||
      Number(normalized.mtime || 0) >= Number(previous.mtime || 0)
    ) {
      byPath.set(path, normalized);
    }
  }
  return [...byPath.values()]
    .sort(
      (a, b) =>
        Number(b.mtime || 0) - Number(a.mtime || 0) ||
        a.path.localeCompare(b.path)
    )
    .slice(0, Math.max(0, Math.floor(Number(limit) || 0)));
}
