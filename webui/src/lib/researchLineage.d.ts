export type ResearchNodeStatus =
  | "running"
  | "complete"
  | "failed"
  | "cancelled";

export type ResearchArtifactCategory =
  | "docs"
  | "figures"
  | "data"
  | "code"
  | "other";

export interface ResearchArtifact {
  path: string;
  category: ResearchArtifactCategory;
  importance: "core" | "detail";
  sourceNodeIds: string[];
}

export interface ResearchNode {
  id: string;
  kind: "answer" | "agent" | "tool";
  messageId: string;
  toolCallId?: string;
  name?: string;
  title: string;
  summary: string;
  detail: string;
  args?: Record<string, unknown>;
  status: ResearchNodeStatus;
  files: string[];
}

export interface ResearchTurn {
  id: string;
  messageId: string;
  title: string;
  prompt: string;
  status: ResearchNodeStatus;
  nodes: ResearchNode[];
  files: string[];
  finalAnswer: ResearchNode | null;
  keyNodes: ResearchNode[];
  artifacts: ResearchArtifact[];
}

export interface ResearchRoute {
  path: string;
  checkpointId: string | null;
  createdAt: string | null;
  messages: unknown[];
}

export function extractLineageText(content: unknown): string;
export function extractLineageFiles(value: unknown): string[];
export function normalizeLineageToolCalls(
  message: Record<string, unknown>
): Array<{ id: string; name: string; args: Record<string, unknown> }>;
export function classifyResearchArtifact(
  path: string,
  evidence?: {
    referencedByFinalAnswer?: boolean;
    sourceNodeIds?: string[];
  }
): ResearchArtifact;
export function buildResearchTurns(
  messages: unknown[],
  stateFiles?: Record<string, string>
): ResearchTurn[];
export function collectResearchRoutes(tree: unknown): ResearchRoute[];
export function mergeCheckpointHistory<T>(
  existing: T[],
  page: T[],
  checkpointIdOf: (item: T) => string | null | undefined
): { merged: T[]; added: number };
