"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface PanelEdgeToggleProps {
  side: "left" | "right";
  open: boolean;
  onClick: () => void;
  label: string;
  controls?: string;
  badge?: number;
  className?: string;
}

export function PanelEdgeToggle({
  side,
  open,
  onClick,
  label,
  controls,
  badge = 0,
  className,
}: PanelEdgeToggleProps) {
  const pointsLeft = side === "left" ? open : !open;
  const Icon = pointsLeft ? ChevronLeft : ChevronRight;

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-controls={controls}
      aria-expanded={open}
      title={label}
      className={cn(
        "relative jw-edge-toggle platform-blue-hover",
        side === "left" ? "jw-edge-toggle-left" : "jw-edge-toggle-right",
        className
      )}
    >
      <Icon className="size-4" aria-hidden="true" />
      {badge > 0 && (
        <span className="absolute -right-1.5 -top-1.5 z-20 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground shadow-md">
          {badge}
        </span>
      )}
    </button>
  );
}
