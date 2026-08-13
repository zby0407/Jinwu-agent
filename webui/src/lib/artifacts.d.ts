export type ArtifactCategory =
  | "documents"
  | "figures"
  | "data"
  | "code"
  | "other";
export type ArtifactSource =
  | "outputs"
  | "legacy"
  | "experiment-report"
  | "experiment-public";

export interface ArtifactCandidate {
  path: string;
  name: string;
  ext: string;
  size: number;
  mtime: number;
  category: ArtifactCategory;
  source: ArtifactSource;
}

export function normalizeArtifactPath(value: unknown): string | null;
export function artifactCategory(ext: unknown): ArtifactCategory;
export function artifactSource(path: unknown): ArtifactSource | null;
export function sortAndDedupeArtifacts(
  entries: Array<Partial<ArtifactCandidate> & { path?: unknown }>,
  limit?: number
): ArtifactCandidate[];
