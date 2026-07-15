"use client";

import React, { useEffect, useId, useState } from "react";
import { CodeBlock } from "./CodeBlock";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { useTheme } from "@/providers/ThemeProvider";

interface MermaidDiagramProps {
  code: string;
  /** While true, skip rendering — fall back to the source view. */
  isStreaming?: boolean;
}

// Mermaid is a singleton with shared internal DOM state. Concurrent render()
// calls clobber each other and silently drop SVGs, so we serialize every
// call through this promise chain. Also caches the mermaid import + the
// one-time initialize() so they don't run per-diagram.
type MermaidModule = typeof import("mermaid").default;
type MermaidTheme = "default" | "dark";
let mermaidLoader: Promise<MermaidModule> | null = null;
let renderChain: Promise<unknown> = Promise.resolve();

function loadMermaid(): Promise<MermaidModule> {
  if (!mermaidLoader) {
    mermaidLoader = import("mermaid").then((mod) => mod.default);
  }
  return mermaidLoader;
}

function initializeMermaid(mermaid: MermaidModule, theme: MermaidTheme) {
  mermaid.initialize({
    startOnLoad: false,
    theme,
    // "strict" forbids raw HTML in node labels — safer for AI-generated
    // diagram source rendered alongside user content.
    securityLevel: "strict",
  });
}

async function renderMermaid(
  id: string,
  code: string,
  theme: MermaidTheme
): Promise<string | null> {
  const next = renderChain.then(async () => {
    const mermaid = await loadMermaid();
    initializeMermaid(mermaid, theme);
    // parse() with suppressErrors gives a boolean instead of the "red bomb"
    // error-SVG that render() produces on bad input.
    const parsed = await mermaid.parse(code, { suppressErrors: true });
    if (!parsed) return null;
    const { svg } = await mermaid.render(id, code);
    return svg;
  });
  // Keep the chain alive even if this link throws — otherwise one bad
  // diagram would freeze every subsequent render call.
  renderChain = next.catch(() => undefined);
  return next;
}

export const MermaidDiagram = React.memo<MermaidDiagramProps>(
  ({ code, isStreaming = false }) => {
    // useId is React 19's stable-across-renders unique id. Mermaid needs a
    // CSS-safe id; useId returns strings like ":r3:" so strip the colons.
    const reactId = useId().replace(/:/g, "");
    const { resolvedTheme } = useTheme();
    const mermaidTheme: MermaidTheme =
      resolvedTheme === "dark" ? "dark" : "default";
    const [svg, setSvg] = useState<string | null>(null);

    useEffect(() => {
      // Skip while the message is still streaming — `code` changes on every
      // token, and queuing a render per change would back up mermaid's
      // serialized chain and freeze the UI. Renders kick off once streaming
      // ends (this effect re-runs because isStreaming is in the deps).
      if (isStreaming) return;
      let cancelled = false;
      setSvg(null);
      renderMermaid(`mermaid-${reactId}`, code, mermaidTheme)
        .then((result) => {
          if (!cancelled) setSvg(result);
        })
        .catch(() => {
          if (!cancelled) setSvg(null);
        });
      return () => {
        cancelled = true;
      };
    }, [code, reactId, isStreaming, mermaidTheme]);

    // During streaming the source grows token-by-token. Avoid the syntax
    // highlighter here — it re-tokenizes the full string on every keystroke
    // and, multiplied across several diagrams, freezes the main thread.
    if (isStreaming) {
      return (
        <pre className="my-4 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-tertiary)]">
          <code>{code}</code>
        </pre>
      );
    }
    if (!svg) {
      return (
        <CodeBlock
          language="mermaid"
          value={code}
        />
      );
    }
    return <MermaidPreview svg={svg} />;
  }
);
MermaidDiagram.displayName = "MermaidDiagram";

const MermaidPreview = React.memo<{ svg: string }>(({ svg }) => {
  const [open, setOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const zoomPercent = Math.round(zoom * 100);

  useEffect(() => {
    if (open) setZoom(1);
  }, [open]);

  const zoomOut = () => setZoom((value) => Math.max(0.75, value - 0.25));
  const zoomIn = () => setZoom((value) => Math.min(2.5, value + 0.25));

  return (
    <>
      <button
        type="button"
        aria-label="Open diagram in full view"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="my-4 block max-w-full cursor-zoom-in overflow-x-auto rounded-md bg-[var(--color-surface)] p-4 text-left transition-shadow hover:ring-2 hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      <Dialog
        open={open}
        onOpenChange={setOpen}
      >
        <DialogContent className="max-h-[calc(100svh-1rem)] w-[calc(100vw-1rem)] max-w-[calc(100vw-1rem)] overflow-auto p-3 sm:max-h-[92vh] sm:w-[min(1200px,95vw)] sm:max-w-[95vw] sm:p-4">
          <DialogTitle className="sr-only">Mermaid diagram</DialogTitle>
          <DialogDescription className="sr-only">
            Full-size preview of the generated Mermaid diagram.
          </DialogDescription>
          <div className="sticky top-0 z-10 -mx-1 -mt-1 mb-2 flex items-center justify-end gap-1 bg-background/95 pb-2 pr-8 backdrop-blur">
            <button
              type="button"
              onClick={zoomOut}
              disabled={zoom <= 0.75}
              aria-label="Zoom out"
              className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ZoomOut
                className="size-4"
                aria-hidden="true"
              />
            </button>
            <span
              className="min-w-12 text-center text-xs font-medium tabular-nums text-muted-foreground"
              aria-live="polite"
            >
              {zoomPercent}%
            </span>
            <button
              type="button"
              onClick={zoomIn}
              disabled={zoom >= 2.5}
              aria-label="Zoom in"
              className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ZoomIn
                className="size-4"
                aria-hidden="true"
              />
            </button>
            <button
              type="button"
              onClick={() => setZoom(1)}
              aria-label="Reset zoom"
              className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RotateCcw
                className="size-4"
                aria-hidden="true"
              />
            </button>
          </div>
          <div className="min-w-full overflow-auto rounded-md bg-[var(--color-surface)] p-3">
            <div
              className="[&_svg]:!h-auto [&_svg]:!w-full [&_svg]:!max-w-none"
              style={{ width: `${zoomPercent}%` }}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
});
MermaidPreview.displayName = "MermaidPreview";
