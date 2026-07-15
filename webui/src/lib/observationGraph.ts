// Client-side types and force-simulation helpers for the observation graph.

export interface ObsNode {
  id: string;
  path: string;
  summary: string;
  memory_type: string;
  scope: string;
  created_at: string;
  degree: number;
}

export interface ObsEdge {
  source: string;
  target: string;
  relation: string;
}

export interface ObsGraphData {
  nodes: ObsNode[];
  edges: ObsEdge[];
}

export interface NodePos {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

function stableJitter(id: string, axis: number): number {
  let hash = 2166136261 ^ axis;
  for (let i = 0; i < id.length; i++) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) / 0xffffffff - 0.5) * 4;
}

export function initPositions(
  nodes: ObsNode[],
  w: number,
  h: number
): NodePos[] {
  const count = Math.max(nodes.length, 1);
  // Larger initial radius for many nodes so repulsion can spread them cleanly.
  const r = Math.min(w, h) * Math.max(0.28, 0.12 * Math.sqrt(count));
  return nodes.map((n, i) => {
    const angle = (i / count) * 2 * Math.PI;
    // Tiny deterministic jitter so perfectly-overlapping nodes diverge without
    // making the graph jump to a different layout on every open.
    return {
      id: n.id,
      x: w / 2 + r * Math.cos(angle) + stableJitter(n.id, 0),
      y: h / 2 + r * Math.sin(angle) + stableJitter(n.id, 1),
      vx: 0,
      vy: 0,
    };
  });
}

/** One physics tick. Returns updated positions and total kinetic energy. */
export function tickSimulation(
  positions: NodePos[],
  edges: ObsEdge[],
  w: number,
  h: number
): { positions: NodePos[]; energy: number } {
  const REPULSION = 6000;
  const SPRING_K = 0.04;
  const IDEAL_LENGTH = 180;
  const GRAVITY = 0.02;
  const DAMPING = 0.78;
  const MAX_SPEED = 12;
  const cx = w / 2;
  const cy = h / 2;

  const fx = new Float64Array(positions.length);
  const fy = new Float64Array(positions.length);
  const posIdx = new Map(positions.map((p, i) => [p.id, i]));

  // O(n²) charge repulsion — fine for the expected <200 nodes.
  for (let i = 0; i < positions.length; i++) {
    for (let j = i + 1; j < positions.length; j++) {
      const dx = positions[i].x - positions[j].x;
      const dy = positions[i].y - positions[j].y;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const f = REPULSION / (dist * dist);
      const ux = dx / dist;
      const uy = dy / dist;
      fx[i] += f * ux;
      fy[i] += f * uy;
      fx[j] -= f * ux;
      fy[j] -= f * uy;
    }
  }

  // Spring attraction on edges.
  for (const edge of edges) {
    const si = posIdx.get(edge.source);
    const ti = posIdx.get(edge.target);
    if (si == null || ti == null) continue;
    const dx = positions[ti].x - positions[si].x;
    const dy = positions[ti].y - positions[si].y;
    const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
    const f = SPRING_K * (dist - IDEAL_LENGTH);
    const ux = dx / dist;
    const uy = dy / dist;
    fx[si] += f * ux;
    fy[si] += f * uy;
    fx[ti] -= f * ux;
    fy[ti] -= f * uy;
  }

  // Gravity towards centre.
  for (let i = 0; i < positions.length; i++) {
    fx[i] += GRAVITY * (cx - positions[i].x);
    fy[i] += GRAVITY * (cy - positions[i].y);
  }

  // Integrate, clamping per-tick speed so nodes glide instead of darting.
  let energy = 0;
  const next: NodePos[] = positions.map((p, i) => {
    let vx = (p.vx + fx[i]) * DAMPING;
    let vy = (p.vy + fy[i]) * DAMPING;
    const speed = Math.sqrt(vx * vx + vy * vy);
    if (speed > MAX_SPEED) {
      vx = (vx / speed) * MAX_SPEED;
      vy = (vy / speed) * MAX_SPEED;
    }
    energy += vx * vx + vy * vy;
    return { id: p.id, x: p.x + vx, y: p.y + vy, vx, vy };
  });

  return { positions: next, energy };
}

// ---------------------------------------------------------------------------
// Visual helpers
// ---------------------------------------------------------------------------

export function relationColor(relation: string): string {
  switch (relation) {
    case "complements":
      return "#10b981"; // emerald
    case "contradicts":
      return "#f43f5e"; // rose
    case "supersedes":
      return "#f59e0b"; // amber
    default:
      return "#94a3b8"; // slate
  }
}

export function nodeRadius(degree: number): number {
  return Math.max(9, Math.min(22, 9 + degree * 2.5));
}

export function nodeColor(memory_type: string): string {
  return memory_type === "procedural" ? "#0ea5e9" : "#6366f1";
}

export function relationLabel(relation: string): string {
  switch (relation) {
    case "complements":
      return "Complements";
    case "contradicts":
      return "Contradicts";
    case "supersedes":
      return "Supersedes";
    default:
      return relation;
  }
}
