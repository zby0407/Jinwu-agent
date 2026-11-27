"use client";

import { useRef } from "react";

export interface SidebarSize {
  width: number;
  minWidth: number;
  maxWidth: number;
  onResize: (width: number) => void;
}

interface SidebarResizeHandleProps {
  side: "left" | "right";
  label: string;
  controls: string;
  size: SidebarSize;
  onDraggingChange?: (dragging: boolean) => void;
}

export function SidebarResizeHandle({ side, label, controls, size, onDraggingChange }: SidebarResizeHandleProps) {
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  const { width, minWidth, maxWidth, onResize } = size;
  const direction = side === "left" ? 1 : -1;
  const disabled = maxWidth <= minWidth;

  const resizeTo = (nextWidth: number) => {
    onResize(Math.max(minWidth, Math.min(maxWidth, Math.round(nextWidth))));
  };

  const finishDrag = (pointerId: number) => {
    if (drag.current?.pointerId !== pointerId) return;
    drag.current = null;
    onDraggingChange?.(false);
  };

  return (
    <div
      role="separator"
      aria-label={label}
      aria-controls={controls}
      aria-orientation="vertical"
      aria-valuemin={minWidth}
      aria-valuemax={maxWidth}
      aria-valuenow={Math.round(width)}
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      title={label}
      className="chat-sidebar-resize-handle"
      data-side={side}
      onPointerDown={event => {
        if (disabled || event.button !== 0 || drag.current) return;
        event.preventDefault();
        const { pointerId, clientX, currentTarget } = event;
        currentTarget.focus({ preventScroll: true });
        currentTarget.setPointerCapture(pointerId);
        drag.current = { pointerId, startX: clientX, startWidth: width };
        onDraggingChange?.(true);
      }}
      onPointerMove={event => {
        const currentDrag = drag.current;
        if (!currentDrag || currentDrag.pointerId !== event.pointerId) return;
        resizeTo(currentDrag.startWidth + direction * (event.clientX - currentDrag.startX));
      }}
      onPointerUp={event => finishDrag(event.pointerId)}
      onPointerCancel={event => finishDrag(event.pointerId)}
      onLostPointerCapture={event => finishDrag(event.pointerId)}
      onKeyDown={event => {
        if (disabled) return;
        const nextWidth = ({
          ArrowLeft: width - 16 * direction,
          ArrowRight: width + 16 * direction,
          Home: minWidth,
          End: maxWidth,
        } as Partial<Record<string, number>>)[event.key];
        if (typeof nextWidth !== "number") return;
        event.preventDefault();
        resizeTo(nextWidth);
      }}
    />
  );
}
