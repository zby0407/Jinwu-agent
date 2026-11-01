"use client";

/**
 * GenuiFence: renders a ```dsh-ui fenced block from an assistant reply as an
 * interactive GenUI component tree, using the embeddable half of
 * @changfenhuang/dsh-genui (no DSH plugin runtime needed).
 *
 * Resolution mirrors the plugin's own fence pipeline:
 * - while streaming: only render when the whole body parses as JSON (the
 *   partial parser lives in package internals, not the embed entry) — until
 *   then the stock code block stays visible, which is exactly what a
 *   half-written fence should look like;
 * - settled: parse, then run processGenuiSpec (alias normalization → repair
 *   → validation). A non-renderable result keeps the code block and shows a
 *   one-line diagnostic, so a malformed fence is never a silent failure.
 *
 * onAction flows through GenuiActionContext (see GenuiProvider): GenUI
 * components fire (action, payload) and the provider turns that into a
 * follow-up chat message.
 */

import React, { useEffect, useMemo } from "react";
import {
  ErrorBoundary,
  GenuiBlock,
  isRenderableProcess,
  processGenuiSpec,
  setGenuiAssetBase,
  type GenuiSpec,
} from "@changfenhuang/dsh-genui/embed";
import { CodeBlock } from "./CodeBlock";

/** Point the lazy engine loader (mermaid/three/echarts/icons/maplibre) at the
 * same-origin copies served from public/genui-assets (see
 * scripts/sync-genui-assets.mjs). Safe to call repeatedly. */
let assetBaseConfigured = false;
function ensureAssetBase(): void {
  if (assetBaseConfigured) return;
  assetBaseConfigured = true;
  try {
    setGenuiAssetBase("/genui-assets/");
  } catch {
    // SSR or a hostile DOM: the loader falls back to display-only rendering.
  }
}

/** djb2 content fingerprint — same algorithm as dsh-genui's
 * interaction-store, so state keys are stable across re-renders and refresh. */
function fingerprint(raw: string): string {
  let h = 5381;
  for (let i = 0; i < raw.length; i++) {
    h = ((h << 5) + h + raw.charCodeAt(i)) >>> 0;
  }
  return h.toString(36);
}

/** Tolerant language-tag check mirroring the plugin's fence matcher: exact
 * `dsh-ui`, case-insensitive, ignoring surrounding whitespace and a trailing
 * punctuation char (e.g. the historical `dsh-ui>` typo). */
export function isDshUiLanguageTag(tag: string): boolean {
  return /^[ \t]*dsh-ui[ \t]*[>.,;:'"！？]?\s*$/i.test(tag);
}

interface GenuiFenceProps {
  /** Raw fence body (the JSON between the ```dsh-ui and ``` lines). */
  raw: string;
  /** True while the owning message is still streaming. */
  isStreaming?: boolean;
  /** Durable-state key parts: session (threadId) + fence slot identity. The
   * content fingerprint is appended here so edited content gets fresh state. */
  sessionId?: string;
  fenceKey?: string | number;
}

interface Resolution {
  spec: GenuiSpec | null;
  diagnostic: string | null;
}

/** Resolve a fence body against the embed pipeline. `settled` enables the
 * same "unrenderable body must say why" contract the plugin ships. */
function resolveSpec(raw: string, settled: boolean): Resolution {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return {
      spec: null,
      // While streaming an incomplete body is expected, not an error.
      diagnostic: settled ? "dsh-ui 围栏不是合法 JSON（结构错误不会被修复）" : null,
    };
  }
  const processed = processGenuiSpec(value);
  if (processed.spec !== null && isRenderableProcess(processed)) {
    return { spec: processed.spec, diagnostic: null };
  }
  const errors = processed.errors.slice(0, 3).join("；");
  return {
    spec: null,
    diagnostic: settled
      ? `dsh-ui 围栏无法渲染${errors ? `：${errors}` : ""}`
      : null,
  };
}

export const GenuiFence = React.memo<GenuiFenceProps>(
  ({ raw, isStreaming = false, sessionId, fenceKey }) => {
    ensureAssetBase();
    const settled = !isStreaming;
    // Re-resolve on every body change; processGenuiSpec is pure and bounded.
    const { spec, diagnostic } = useMemo(
      () => resolveSpec(raw, settled),
      [raw, settled]
    );

    const stateKey = useMemo(
      () =>
        sessionId === undefined || fenceKey === undefined
          ? undefined
          : `f:${sessionId}:${String(fenceKey)}:${fingerprint(raw)}`,
      [sessionId, fenceKey, raw]
    );

    // Streaming bodies that don't parse yet keep the code block mounted, so
    // the transition to a rendered tree happens in place without layout jumps.
    if (spec === null) {
      return (
        <>
          {diagnostic && (
            <div
              role="alert"
              className="my-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
            >
              {diagnostic}
            </div>
          )}
          <CodeBlock language="dsh-ui" value={raw} />
        </>
      );
    }

    return (
      <div className="my-4 last:mb-0">
        <ErrorBoundary label="dsh-ui 渲染失败">
          <GenuiBlock
            spec={spec}
            stateKey={stateKey}
            animateEntrance={isStreaming}
          />
        </ErrorBoundary>
      </div>
    );
  }
);

GenuiFence.displayName = "GenuiFence";

/**
 * One-shot client initializer: configures the asset base before any fence
 * mounts. Mounted once near the chat root (dynamic import keeps the genui
 * bundle out of the initial page load path).
 */
export function GenuiClientBootstrap(): null {
  useEffect(() => {
    ensureAssetBase();
  }, []);
  return null;
}

/**
 * Compatibility shim for older callers: resolve a settled fence body to a
 * spec without rendering. Exported for tests.
 */
export function resolveGenuiFenceSpec(raw: string): GenuiSpec | null {
  return resolveSpec(raw, true).spec;
}
