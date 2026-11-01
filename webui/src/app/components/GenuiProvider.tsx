"use client";

/**
 * GenuiProvider: mounts the GenuiActionContext provider for the chat page so
 * interactive GenUI components (buttons, selects, submits…) can send their
 * actions back into the conversation as follow-up messages — mirroring how
 * the dsh-genui plugin relays `[genui-action]` through the DSH conversation.
 *
 * Without a provider the components render display-only (actions disabled),
 * so this wrapper is what makes the UI actually interactive.
 */

import React, { useCallback, useEffect, useRef } from "react";
import { GenuiActionContext, setGenuiAssetBase } from "@changfenhuang/dsh-genui/embed";

/** v2 action handler shape (mirrors GenuiActionHandler from the embed entry,
 * which does not re-export the type itself). */
type GenuiActionHandler = (action: string, payload: Record<string, unknown>) => void;

/** Configure the lazy engine asset base once per browser session. */
let assetBaseReady = false;
function configureAssetBase(): void {
  if (assetBaseReady || typeof document === "undefined") return;
  assetBaseReady = true;
  try {
    setGenuiAssetBase("/genui-assets/");
  } catch {
    // Ignore: display-only fallback covers a failed configuration.
  }
}

interface GenuiProviderProps {
  /** Deliver a GenUI action as a chat follow-up on the CURRENT thread. */
  onAction?: (message: string, action: string, payload: Record<string, unknown>) => void;
  children: React.ReactNode;
}

/** Format a GenUI action the way the dsh-genui plugin formats its
 * `[genui-action]` relays, so the model sees a recognizable, parseable
 * convention (Chinese prompt, same as the plugin's own message shape). */
export function formatGenuiActionMessage(
  action: string,
  payload: Record<string, unknown>
): string {
  const hasPayload = payload !== undefined && Object.keys(payload).length > 0;
  const payloadJson = hasPayload
    ? JSON.stringify(payload, null, 0)
    : "";
  return hasPayload
    ? `[genui-action] 用户与 GenUI 组件交互：action=${action} payload=${payloadJson}`
    : `[genui-action] 用户与 GenUI 组件交互：action=${action}`;
}

export function GenuiProvider({ onAction, children }: GenuiProviderProps) {
  useEffect(() => {
    configureAssetBase();
  }, []);

  // Keep the latest sender in a ref so the context value stays stable and
  // memoized GenuiBlocks don't re-render on every parent state change.
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  const handler = useCallback<GenuiActionHandler>(
    (action: string, payload: Record<string, unknown>) => {
      const message = formatGenuiActionMessage(action, payload);
      const sender = onActionRef.current;
      if (sender) {
        sender(message, action, payload);
      } else {
        // No sender wired: surface it in the console for debugging.
        console.info("[genui] action without sender:", message);
      }
    },
    []
  );

  return (
    <GenuiActionContext.Provider value={handler}>
      {children}
    </GenuiActionContext.Provider>
  );
}
