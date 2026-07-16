import { promises as fs } from "fs";
import { basename, extname, join } from "path";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  isCrossOrigin,
  isHiddenEntry,
  safeResolve,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

const MAX_ARTIFACT_FILES = 5000;
const MAX_DEPTH = 12;
const MANIFEST_DIR = ".jinwu";
const MANIFEST_FILE = "artifacts.json";

export type ArtifactKind =
  | "figure"
  | "table"
  | "report"
  | "notebook"
  | "model"
  | "data";

export interface ResearchArtifact {
  id: string;
  label: string;
  number: number;
  kind: ArtifactKind;
  name: string;
  path: string;
  ext: string;
  size: number;
  mtime: number;
}

interface ArtifactRegistryItem {
  id: string;
  number: number;
  kind: ArtifactKind;
  firstSeenAt: string;
}

interface ArtifactRegistry {
  version: 1;
  nextNumbers: Record<ArtifactKind, number>;
  items: Record<string, ArtifactRegistryItem>;
  artifacts?: ResearchArtifact[];
  updatedAt?: string;
}

interface FileCandidate {
  name: string;
  path: string;
  ext: string;
  size: number;
  mtime: number;
  kind: ArtifactKind;
}

const KIND_LABEL: Record<ArtifactKind, string> = {
  figure: "\u56fe",
  table: "\u8868",
  report: "\u62a5\u544a",
  notebook: "Notebook",
  model: "\u6a21\u578b",
  data: "\u6570\u636e",
};

const EMPTY_NEXT_NUMBERS: Record<ArtifactKind, number> = {
  figure: 1,
  table: 1,
  report: 1,
  notebook: 1,
  model: 1,
  data: 1,
};

const EXCLUDED_SEGMENTS = new Set([
  ".git",
  ".jinwu",
  ".langgraph_api",
  ".next",
  "node_modules",
  "src",
  "public",
  "assets",
  "dist",
  "build",
  "coverage",
  "vendor",
  "tests",
  "test",
  "__pycache__",
]);

// The new repository ships backend source, demo data, and proof artifacts in
// the same checkout as the WebUI. Keep packaged files out of this panel so it
// reflects files generated in the active workspace.
const EXCLUDED_REPO_ROOTS = new Set([
  "EvoScientist",
  "b3",
  "docs",
  "scripts_b3",
  "tests",
  "webui",
]);

const RAW_DATA_SEGMENTS = new Set([
  "image",
  "images",
  "fits",
  "fits_600",
  "movie",
  "movies",
  "raw",
  "upload",
  "uploads",
  "input",
  "inputs",
  "dataset",
  "datasets",
]);

const OUTPUT_SEGMENTS = new Set([
  "artifact",
  "artifacts",
  "output",
  "outputs",
  "result",
  "results",
  "figure",
  "figures",
  "plot",
  "plots",
  "chart",
  "charts",
  "table",
  "tables",
  "report",
  "reports",
  "analysis",
]);

const FIGURE_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "svg",
  "webp",
  "bmp",
  "tiff",
  "tif",
  "eps",
]);
const TABLE_EXTS = new Set(["csv", "tsv", "xlsx", "xls", "parquet", "feather"]);
const REPORT_EXTS = new Set(["pdf", "doc", "docx", "odt", "rtf", "tex"]);
const MODEL_EXTS = new Set([
  "pkl",
  "pickle",
  "joblib",
  "onnx",
  "pt",
  "pth",
  "safetensors",
]);
const DATA_EXTS = new Set([
  "json",
  "jsonl",
  "npy",
  "npz",
  "h5",
  "hdf5",
  "sqlite",
  "db",
  "fits",
  "fit",
  "fts",
]);

const OUTPUT_NAME_PATTERN =
  /(artifact|output|result|figure|plot|chart|heatmap|correlation|confusion|distribution|metric|experiment|analysis|summary|finding|report|\u9884\u6d4b|\u5206\u6790|\u7ed3\u679c|\u56fe\u8868|\u62a5\u544a|\u5b9e\u9a8c)/i;

function segmentsOf(relPath: string): string[] {
  return relPath
    .replaceAll("\\", "/")
    .split("/")
    .slice(0, -1)
    .map((segment) => segment.toLowerCase());
}

function hasSegment(relPath: string, values: Set<string>): boolean {
  return segmentsOf(relPath).some((segment) => values.has(segment));
}

function isExcludedRepoPath(relPath: string): boolean {
  const [root] = relPath.replaceAll("\\", "/").split("/");
  return EXCLUDED_REPO_ROOTS.has(root);
}

function classifyArtifact(relPath: string, ext: string): ArtifactKind | null {
  if (isExcludedRepoPath(relPath)) return null;

  const lowerExt = ext.toLowerCase();
  const name = basename(relPath);
  const inOutputDirectory = hasSegment(relPath, OUTPUT_SEGMENTS);
  const inRawDataDirectory = hasSegment(relPath, RAW_DATA_SEGMENTS);
  const outputLikeName = OUTPUT_NAME_PATTERN.test(name);

  if (TABLE_EXTS.has(lowerExt)) {
    return inRawDataDirectory && !inOutputDirectory ? null : "table";
  }
  if (FIGURE_EXTS.has(lowerExt)) {
    return inRawDataDirectory && !inOutputDirectory ? null : "figure";
  }
  if (REPORT_EXTS.has(lowerExt)) {
    return inRawDataDirectory && !inOutputDirectory ? null : "report";
  }
  if (lowerExt === "ipynb") return "notebook";
  if (MODEL_EXTS.has(lowerExt)) return "model";
  if (lowerExt === "md" || lowerExt === "markdown" || lowerExt === "html") {
    return inOutputDirectory || outputLikeName ? "report" : null;
  }
  if (DATA_EXTS.has(lowerExt)) {
    return inOutputDirectory || outputLikeName ? "data" : null;
  }
  return null;
}

async function collectArtifacts(
  workspaceDir: string,
  relDir: string,
  depth: number,
  out: FileCandidate[]
): Promise<void> {
  if (depth > MAX_DEPTH || out.length >= MAX_ARTIFACT_FILES) return;

  let dir: string;
  try {
    dir = await safeResolve(workspaceDir, relDir || "");
  } catch {
    return;
  }

  let dirents;
  try {
    dirents = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }

  for (const dirent of dirents) {
    if (out.length >= MAX_ARTIFACT_FILES) break;
    const lowerName = dirent.name.toLowerCase();
    if (isHiddenEntry(dirent.name) || EXCLUDED_SEGMENTS.has(lowerName)) {
      continue;
    }
    if (dirent.isSymbolicLink()) continue;

    const childRel = relDir ? `${relDir}/${dirent.name}` : dirent.name;
    if (isExcludedRepoPath(childRel)) continue;

    if (dirent.isDirectory()) {
      await collectArtifacts(workspaceDir, childRel, depth + 1, out);
      continue;
    }
    if (!dirent.isFile()) continue;

    const ext = extname(dirent.name).slice(1).toLowerCase();
    const kind = classifyArtifact(childRel, ext);
    if (!kind) continue;

    try {
      const stat = await fs.stat(await safeResolve(workspaceDir, childRel));
      out.push({
        name: dirent.name,
        path: childRel,
        ext,
        size: stat.size,
        mtime: stat.mtimeMs,
        kind,
      });
    } catch {
      // The agent may replace a file while this scan is running.
    }
  }
}

function freshRegistry(): ArtifactRegistry {
  return {
    version: 1,
    nextNumbers: { ...EMPTY_NEXT_NUMBERS },
    items: {},
  };
}

async function readRegistry(manifestPath: string): Promise<ArtifactRegistry> {
  try {
    const parsed = JSON.parse(
      await fs.readFile(manifestPath, "utf8")
    ) as Partial<ArtifactRegistry>;
    if (parsed.version !== 1 || !parsed.items || !parsed.nextNumbers) {
      return freshRegistry();
    }
    return {
      version: 1,
      items: parsed.items,
      nextNumbers: {
        ...EMPTY_NEXT_NUMBERS,
        ...parsed.nextNumbers,
      },
      artifacts: Array.isArray(parsed.artifacts) ? parsed.artifacts : undefined,
      updatedAt: parsed.updatedAt,
    };
  } catch {
    return freshRegistry();
  }
}

async function writeRegistry(
  workspaceDir: string,
  registry: ArtifactRegistry
): Promise<void> {
  const metadataDir = join(workspaceDir, MANIFEST_DIR);
  await fs.mkdir(metadataDir, { recursive: true });
  const target = join(metadataDir, MANIFEST_FILE);
  const temporary = join(
    metadataDir,
    `${MANIFEST_FILE}.${process.pid}.${Date.now()}.tmp`
  );
  await fs.writeFile(
    temporary,
    `${JSON.stringify(registry, null, 2)}\n`,
    "utf8"
  );
  await fs.rename(temporary, target);
}

async function buildArtifactResponse() {
  const workspaceDir = await getWorkspaceDir();
  const candidates: FileCandidate[] = [];
  await collectArtifacts(workspaceDir, "", 0, candidates);
  candidates.sort((a, b) => a.mtime - b.mtime || a.path.localeCompare(b.path));

  const manifestPath = join(workspaceDir, MANIFEST_DIR, MANIFEST_FILE);
  const registry = await readRegistry(manifestPath);
  let registryChanged = false;

  for (const candidate of candidates) {
    if (registry.items[candidate.path]) continue;
    const number = registry.nextNumbers[candidate.kind] ?? 1;
    registry.nextNumbers[candidate.kind] = number + 1;
    registry.items[candidate.path] = {
      id: `${candidate.kind}-${String(number).padStart(4, "0")}`,
      number,
      kind: candidate.kind,
      firstSeenAt: new Date().toISOString(),
    };
    registryChanged = true;
  }

  const artifacts: ResearchArtifact[] = candidates.map((candidate) => {
    const registered = registry.items[candidate.path];
    return {
      ...candidate,
      id: registered.id,
      number: registered.number,
      label: `${KIND_LABEL[registered.kind]} ${registered.number}`,
      kind: registered.kind,
    };
  });

  const kindOrder: ArtifactKind[] = [
    "figure",
    "table",
    "report",
    "notebook",
    "model",
    "data",
  ];
  artifacts.sort(
    (a, b) =>
      kindOrder.indexOf(a.kind) - kindOrder.indexOf(b.kind) ||
      a.number - b.number
  );

  const snapshotChanged =
    JSON.stringify(registry.artifacts ?? []) !== JSON.stringify(artifacts);
  if (registryChanged || snapshotChanged) {
    registry.artifacts = artifacts;
    registry.updatedAt = new Date().toISOString();
    await writeRegistry(workspaceDir, registry);
  }

  return {
    workspace: workspaceDir,
    manifest: `${MANIFEST_DIR}/${MANIFEST_FILE}`,
    truncated: candidates.length >= MAX_ARTIFACT_FILES,
    artifacts,
  };
}

let scanQueue: Promise<unknown> = Promise.resolve();

export async function GET(request: NextRequest) {
  if (isCrossOrigin(request)) {
    return NextResponse.json(
      { error: "Cross-origin artifact access is not allowed." },
      { status: 403 }
    );
  }

  const scan = scanQueue.then(() => buildArtifactResponse());
  scanQueue = scan.catch(() => undefined);
  try {
    return NextResponse.json(await scan);
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to index artifacts.",
      },
      { status: 400 }
    );
  }
}
