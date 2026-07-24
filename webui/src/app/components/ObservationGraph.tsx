"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  initPositions,
  nodeColor,
  nodeRadius,
  relationColor,
  relationLabel,
  tickSimulation,
  type NodePos,
  type ObsEdge,
  type ObsGraphData,
  type ObsNode,
} from "@/lib/observationGraph";
import { X, ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
import { MarkdownContent } from "@/app/components/MarkdownContent";

// ---------------------------------------------------------------------------
// Force simulation hook
// ---------------------------------------------------------------------------

const MAX_TICKS = 600;
const ENERGY_THRESHOLD = 0.3;
const GRAPH_BUTTON_CLASS =
  "rounded border border-border bg-background p-1.5 text-muted-foreground shadow-sm transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

const ALL_RELATIONS = ["complements", "contradicts", "supersedes"] as const;
type RelationType = (typeof ALL_RELATIONS)[number];

function stripFrontMatter(content: string): string {
  return content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();
}

function useForceSimulation(
  nodes: ObsNode[],
  edges: ObsEdge[],
  w: number,
  h: number
) {
  const [positions, setPositions] = useState<NodePos[]>([]);
  const posRef = useRef<NodePos[]>([]);
  const rafRef = useRef<number | null>(null);
  const tickRef = useRef(0);
  const wRef = useRef(w);
  const hRef = useRef(h);

  useEffect(() => {
    wRef.current = w;
    hRef.current = h;
  }, [w, h]);

  // Re-initialise only when the graph data changes — NOT on resize, so opening
  // or dragging the detail panel never restarts the layout.
  useEffect(() => {
    if (nodes.length === 0 || wRef.current === 0 || hRef.current === 0) {
      setPositions([]);
      posRef.current = [];
      return;
    }
    const init = initPositions(nodes, wRef.current, hRef.current);
    posRef.current = init;
    setPositions(init);
    tickRef.current = 0;

    if (rafRef.current) cancelAnimationFrame(rafRef.current);

    let frame = 0;
    const animate = () => {
      const { positions: next, energy } = tickSimulation(
        posRef.current,
        edges,
        wRef.current,
        hRef.current
      );
      posRef.current = next;
      tickRef.current++;
      frame++;

      if (frame % 2 === 0) {
        setPositions([...next]);
      }

      if (tickRef.current < MAX_TICKS && energy > ENERGY_THRESHOLD) {
        rafRef.current = requestAnimationFrame(animate);
      } else {
        setPositions([...posRef.current]);
      }
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [nodes, edges]);

  return positions;
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

interface DetailPanelProps {
  node: ObsNode;
  edges: ObsEdge[];
  nodeMap: Map<string, ObsNode>;
  onClose: () => void;
  onNavigate: (id: string) => void;
}

function NodeDetailPanel({
  node,
  edges,
  nodeMap,
  onClose,
  onNavigate,
}: DetailPanelProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(true);
  const [contentError, setContentError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingContent(true);
    setContent(null);
    setContentError(null);
    fetch(`/api/memory?path=${encodeURIComponent(node.path)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (res) => {
        const data = (await res.json().catch(() => ({}))) as {
          content?: string;
          error?: string;
        };
        if (!res.ok) {
          throw new Error(data.error || "Failed to load observation.");
        }
        setContent(data.content ?? null);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setContent(null);
        setContentError(
          error instanceof Error ? error.message : "Failed to load observation."
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingContent(false);
      });

    return () => controller.abort();
  }, [node.path]);

  const relatedEdges = (() => {
    const seen = new Set<string>();
    return edges.filter((e) => {
      if (e.source !== node.id && e.target !== node.id) return false;
      const otherId = e.source === node.id ? e.target : e.source;
      const key = `${otherId}::${e.relation}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  })();
  const displayContent = content ? stripFrontMatter(content) : "";

  const formatDate = (iso: string) => {
    try {
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return iso;
      return date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden border-l border-border bg-background shadow-xl md:shadow-none">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {node.memory_type} · {node.scope}
          </p>
          <h3 className="mt-0.5 line-clamp-3 text-sm font-semibold leading-snug text-foreground">
            {node.summary}
          </h3>
          {node.created_at && (
            <p className="mt-1 text-xs text-muted-foreground">
              {formatDate(node.created_at)}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="mt-0.5 inline-flex size-8 flex-shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Close observation details"
          title="Close observation details"
        >
          <X
            className="h-4 w-4"
            aria-hidden="true"
          />
        </button>
      </div>

      <div
        className="flex-1 overflow-y-auto"
        aria-live={loadingContent ? "polite" : "off"}
      >
        {/* Relations */}
        {relatedEdges.length > 0 && (
          <div className="border-b border-border px-4 py-3">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Relations
            </p>
            <ul className="space-y-1.5">
              {relatedEdges.map((e, idx) => {
                const otherId = e.source === node.id ? e.target : e.source;
                const other = nodeMap.get(otherId);
                return (
                  <li
                    key={idx}
                    className="flex items-start gap-2"
                  >
                    <span
                      className="mt-0.5 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white"
                      style={{ background: relationColor(e.relation) }}
                    >
                      {relationLabel(e.relation)}
                    </span>
                    {other ? (
                      <button
                        type="button"
                        onClick={() => onNavigate(otherId)}
                        className="min-w-0 rounded-sm text-left text-xs text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {other.summary}
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        {otherId}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Content */}
        <div className="px-4 py-3">
          {loadingContent ? (
            <p className="text-xs text-muted-foreground">Loading…</p>
          ) : contentError ? (
            <p
              role="alert"
              className="text-xs text-destructive"
            >
              {contentError}
            </p>
          ) : displayContent ? (
            <MarkdownContent
              content={displayContent}
              className="text-xs leading-relaxed [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-sm [&_p]:mb-3"
            />
          ) : (
            <p className="text-xs text-muted-foreground">
              This observation has no body content.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SVG graph
// ---------------------------------------------------------------------------

interface GraphCanvasProps {
  data: ObsGraphData;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  w: number;
  h: number;
}

function GraphCanvas({ data, selectedId, onSelect, w, h }: GraphCanvasProps) {
  const physicsEdges = useMemo(() => {
    const seen = new Set<string>();
    return data.edges.filter((e) => {
      const key = [e.source, e.target].sort().join("::");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [data.edges]);

  const positions = useForceSimulation(data.nodes, physicsEdges, w, h);

  // Zoom / pan state.
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [scale, setScale] = useState(1);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    tx: number;
    ty: number;
  } | null>(null);

  const [activeFilters, setActiveFilters] = useState<Set<string>>(
    new Set(ALL_RELATIONS)
  );
  const [activeTypes, setActiveTypes] = useState<Set<string>>(
    new Set(["semantic", "procedural"])
  );

  const posMap = useMemo(
    () => new Map(positions.map((p) => [p.id, p])),
    [positions]
  );
  const nodeMap = useMemo(
    () => new Map(data.nodes.map((n) => [n.id, n])),
    [data.nodes]
  );

  const viewRef = useRef({ tx, ty, scale });
  viewRef.current = { tx, ty, scale };

  const zoomAt = useCallback((px: number, py: number, factor: number) => {
    const { tx, ty, scale } = viewRef.current;
    const newScale = Math.max(0.2, Math.min(5, scale * factor));
    const k = newScale / scale;
    setScale(newScale);
    setTx(px - k * (px - tx));
    setTy(py - k * (py - ty));
  }, []);

  const handleWheel = useCallback(
    (e: React.WheelEvent<SVGSVGElement>) => {
      e.preventDefault();
      const rect = e.currentTarget.getBoundingClientRect();
      zoomAt(
        e.clientX - rect.left,
        e.clientY - rect.top,
        e.deltaY < 0 ? 1.1 : 0.9
      );
    },
    [zoomAt]
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (
        e.target === e.currentTarget ||
        (e.target as Element).tagName === "svg"
      ) {
        dragRef.current = { startX: e.clientX, startY: e.clientY, tx, ty };
        (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
      }
    },
    [tx, ty]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (!dragRef.current) return;
      setTx(dragRef.current.tx + e.clientX - dragRef.current.startX);
      setTy(dragRef.current.ty + e.clientY - dragRef.current.startY);
    },
    []
  );

  const handlePointerUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  const resetView = () => {
    setTx(0);
    setTy(0);
    setScale(1);
  };

  const toggleFilter = (rel: string) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(rel)) {
        if (next.size === 1) return prev;
        next.delete(rel);
      } else {
        next.add(rel);
      }
      return next;
    });
  };

  const toggleType = (type: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        if (next.size === 1) return prev;
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  if (w === 0 || h === 0) return null;

  const typeOf = (memory_type: string) =>
    memory_type === "procedural" ? "procedural" : "semantic";

  const visibleNodeIds = new Set(
    data.nodes
      .filter((n) => activeTypes.has(typeOf(n.memory_type)))
      .map((n) => n.id)
  );

  const visibleEdges = data.edges.filter(
    (e) =>
      visibleNodeIds.has(e.source) &&
      visibleNodeIds.has(e.target) &&
      (activeFilters.has(e.relation) ||
        !ALL_RELATIONS.includes(e.relation as RelationType))
  );

  const relations = ["complements", "contradicts", "supersedes", "default"];

  return (
    <>
      {/* Toolbar */}
      <div className="absolute left-3 top-3 z-10 flex gap-1">
        <button
          type="button"
          onClick={() => zoomAt(w / 2, h / 2, 1.25)}
          className={GRAPH_BUTTON_CLASS}
          aria-label="Zoom in observation graph"
          title="Zoom in"
        >
          <ZoomIn
            className="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </button>
        <button
          type="button"
          onClick={() => zoomAt(w / 2, h / 2, 0.8)}
          className={GRAPH_BUTTON_CLASS}
          aria-label="Zoom out observation graph"
          title="Zoom out"
        >
          <ZoomOut
            className="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </button>
        <button
          type="button"
          onClick={resetView}
          className={GRAPH_BUTTON_CLASS}
          aria-label="Reset observation graph view"
          title="Reset view"
        >
          <Maximize2
            className="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </button>
      </div>

      <div className="absolute right-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] select-none flex-col gap-1 rounded border border-border bg-background/90 px-2.5 py-2 text-[11px] shadow-sm backdrop-blur-sm">
        {(["complements", "contradicts", "supersedes"] as const).map((r) => {
          const active = activeFilters.has(r);
          return (
            <button
              key={r}
              type="button"
              onClick={() => toggleFilter(r)}
              aria-pressed={active}
              title={`${active ? "Hide" : "Show"} ${r} edges`}
              className={`flex items-center gap-1.5 text-left transition-opacity ${
                active ? "opacity-100" : "opacity-35"
              }`}
            >
              <span
                className="inline-block h-2 w-6 flex-shrink-0 rounded-sm"
                style={{ background: relationColor(r) }}
              />
              <span className="capitalize text-muted-foreground">
                {relationLabel(r)}
              </span>
            </button>
          );
        })}
        <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1">
          {(
            [
              ["semantic", "Semantic"],
              ["procedural", "Procedural"],
            ] as const
          ).map(([type, label]) => {
            const active = activeTypes.has(type);
            return (
              <button
                key={type}
                type="button"
                onClick={() => toggleType(type)}
                aria-pressed={active}
                title={`${
                  active ? "Hide" : "Show"
                } ${label.toLowerCase()} nodes`}
                className={`flex items-center gap-1.5 text-left transition-opacity ${
                  active ? "opacity-100" : "opacity-35"
                }`}
              >
                <span
                  className="inline-block h-3 w-3 flex-shrink-0 rounded-full"
                  style={{ background: nodeColor(type) }}
                />
                <span className="text-muted-foreground">{label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <svg
        ref={svgRef}
        width={w}
        height={h}
        className="absolute inset-0 cursor-grab active:cursor-grabbing"
        style={{ userSelect: "none" }}
        role="img"
        aria-label="Observation relationship graph"
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onClick={(e) => {
          if (
            e.target === e.currentTarget ||
            (e.target as Element).tagName === "svg"
          ) {
            onSelect(null);
          }
        }}
      >
        <defs>
          {relations.map((rel) => (
            <marker
              key={rel}
              id={`arrow-${rel}`}
              viewBox="0 0 10 10"
              markerWidth="7"
              markerHeight="7"
              refX="10"
              refY="5"
              orient="auto"
              markerUnits="userSpaceOnUse"
            >
              <path
                d="M0,1.5 L10,5 L0,8.5 z"
                fill={relationColor(rel)}
              />
            </marker>
          ))}
        </defs>

        <g transform={`translate(${tx},${ty}) scale(${scale})`}>
          {/* Edges */}
          {visibleEdges.map((edge, idx) => {
            const s = posMap.get(edge.source);
            const t = posMap.get(edge.target);
            if (!s || !t) return null;
            const sNode = nodeMap.get(edge.source);
            const tNode = nodeMap.get(edge.target);
            const sr = sNode ? nodeRadius(sNode.degree) : 9;
            const tr = tNode ? nodeRadius(tNode.degree) : 9;

            // Offset endpoints to the circle edge so arrow doesn't overlap.
            const dx = t.x - s.x;
            const dy = t.y - s.y;
            const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
            const ux = dx / dist;
            const uy = dy / dist;
            const x1 = s.x + ux * sr;
            const y1 = s.y + uy * sr;
            const x2 = t.x - ux * (tr + 2);
            const y2 = t.y - uy * (tr + 2);

            const rel =
              edge.relation in { complements: 1, contradicts: 1, supersedes: 1 }
                ? edge.relation
                : "default";
            const color = relationColor(edge.relation);
            const gradId = `edge-grad-${idx}`;
            // complements is a mutual (undirected) relation — no single
            // arrow direction, so fade both ends symmetrically and drop the
            // arrowhead. Directional relations fade only into the arrow.
            const symmetric = edge.relation === "complements";

            const lineLen = Math.max(dist - sr - (tr + 2), 1);
            const fadePct = Math.min(45, (7 / lineLen) * 100);

            return (
              <g key={idx}>
                <linearGradient
                  id={gradId}
                  gradientUnits="userSpaceOnUse"
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                >
                  {symmetric ? (
                    <>
                      <stop
                        offset="0%"
                        stopColor={color}
                        stopOpacity={0}
                      />
                      <stop
                        offset={`${fadePct}%`}
                        stopColor={color}
                        stopOpacity={0.7}
                      />
                      <stop
                        offset={`${100 - fadePct}%`}
                        stopColor={color}
                        stopOpacity={0.7}
                      />
                      <stop
                        offset="100%"
                        stopColor={color}
                        stopOpacity={0}
                      />
                    </>
                  ) : (
                    <>
                      <stop
                        offset="0%"
                        stopColor={color}
                        stopOpacity={0.7}
                      />
                      <stop
                        offset={`${100 - fadePct}%`}
                        stopColor={color}
                        stopOpacity={0.7}
                      />
                      <stop
                        offset="100%"
                        stopColor={color}
                        stopOpacity={0}
                      />
                    </>
                  )}
                </linearGradient>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={`url(#${gradId})`}
                  strokeWidth={1.5}
                  strokeDasharray={
                    edge.relation === "contradicts" ? "5,3" : undefined
                  }
                  markerEnd={symmetric ? undefined : `url(#arrow-${rel})`}
                />
              </g>
            );
          })}

          {/* Nodes */}
          {data.nodes.map((node) => {
            const pos = posMap.get(node.id);
            if (!pos) return null;
            if (!visibleNodeIds.has(node.id)) return null;
            const r = nodeRadius(node.degree);
            const isSelected = node.id === selectedId;
            const label =
              node.summary.length > 28
                ? node.summary.slice(0, 26) + "…"
                : node.summary;

            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={`Select observation: ${node.summary}`}
                className="outline-none [&:focus-visible]:outline-none [&:focus]:outline-none"
                style={{ cursor: "pointer" }}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(node.id === selectedId ? null : node.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(node.id === selectedId ? null : node.id);
                  }
                }}
              >
                {/* Selection ring — brand accent with a soft halo */}
                {isSelected && (
                  <>
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r={r + 7}
                      fill="none"
                      style={{ stroke: "var(--brand)" }}
                      strokeWidth={2}
                      opacity={0.3}
                    />
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r={r + 4}
                      fill="none"
                      style={{ stroke: "var(--brand)" }}
                      strokeWidth={2.5}
                    />
                  </>
                )}
                <circle
                  cx={pos.x}
                  cy={pos.y}
                  r={r}
                  fill={nodeColor(node.memory_type)}
                  stroke="#fff"
                  strokeWidth={1.5}
                  opacity={0.9}
                />
                <text
                  x={pos.x}
                  y={pos.y + r + 12}
                  textAnchor="middle"
                  fontSize={10}
                  fill="currentColor"
                  className="select-none fill-foreground/70"
                >
                  {label}
                </text>
                <title>{node.summary}</title>
              </g>
            );
          })}
        </g>
      </svg>
    </>
  );
}

// ---------------------------------------------------------------------------
// Empty / loading / error states
// ---------------------------------------------------------------------------

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

interface ObservationGraphProps {
  data: ObsGraphData | null;
  loading: boolean;
  error: string | null;
  highlightNodeId?: string | null;
}

export function ObservationGraph({
  data,
  loading,
  error,
  highlightNodeId,
}: ObservationGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isDesktop, setIsDesktop] = useState(false);
  const [detailWidth, setDetailWidth] = useState(360);
  const resizeRef = useRef<{ startX: number; startW: number } | null>(null);

  useEffect(() => {
    if (highlightNodeId != null) {
      setSelectedId(highlightNodeId);
    }
  }, [highlightNodeId]);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const update = () => setIsDesktop(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  const onResizeDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      resizeRef.current = { startX: e.clientX, startW: detailWidth };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [detailWidth]
  );

  const onResizeMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!resizeRef.current) return;
    const dx = e.clientX - resizeRef.current.startX;
    const max =
      typeof window !== "undefined"
        ? Math.max(640, window.innerWidth * 0.6)
        : 640;
    setDetailWidth(Math.min(max, Math.max(280, resizeRef.current.startW - dx)));
  }, []);

  const onResizeUp = useCallback(() => {
    resizeRef.current = null;
  }, []);

  // Measure container.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setDims({
        w: entry.contentRect.width,
        h: entry.contentRect.height,
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const nodeMap = new Map((data?.nodes ?? []).map((n) => [n.id, n]));
  const selectedNode = selectedId ? nodeMap.get(selectedId) ?? null : null;

  const showDetail = selectedNode !== null;

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {/* Toolbar — no "Observations" title; the parent tab already says that */}
      <div className="flex items-center border-b border-border px-4 py-2">
        <span className="text-xs text-muted-foreground">
          {data
            ? `${data.nodes.length} nodes · ${data.edges.length} edges`
            : loading
            ? "Loading…"
            : ""}
        </span>
      </div>

      {/* Body */}
      <div className="relative flex min-h-0 flex-1">
        {/* Graph area — always the first child so it never remounts */}
        <div
          ref={containerRef}
          className="relative min-w-0 flex-1 overflow-hidden bg-muted/20"
        >
          {loading && <EmptyState message="Loading observations…" />}
          {!loading && error && <EmptyState message={`Error: ${error}`} />}
          {!loading && !error && data && data.nodes.length === 0 && (
            <EmptyState message="No observations yet." />
          )}
          {!loading &&
            !error &&
            data &&
            data.nodes.length > 0 &&
            dims.w > 0 && (
              <GraphCanvas
                data={data}
                selectedId={selectedId}
                onSelect={setSelectedId}
                w={dims.w}
                h={dims.h}
              />
            )}
        </div>

        {/* Detail panel — resizable side panel on desktop, overlay on mobile */}
        {showDetail && isDesktop && (
          <div
            role="separator"
            aria-orientation="vertical"
            onPointerDown={onResizeDown}
            onPointerMove={onResizeMove}
            onPointerUp={onResizeUp}
            className="hover:bg-[var(--brand)]/40 w-1 flex-shrink-0 cursor-col-resize bg-border transition-colors"
          />
        )}
        {showDetail && (
          <div
            className={
              isDesktop
                ? "flex-shrink-0 overflow-hidden"
                : "absolute inset-0 z-20 overflow-hidden"
            }
            style={isDesktop ? { width: detailWidth } : undefined}
          >
            <NodeDetailPanel
              node={selectedNode!}
              edges={data?.edges ?? []}
              nodeMap={nodeMap}
              onClose={() => setSelectedId(null)}
              onNavigate={(id) => setSelectedId(id)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
