"use client";

import { cn } from "@/lib/utils";

/**
 * Minimal accessible switch. Shared so every "on/off" surface in the app
 * (Skills, Observational data sources, knowledge-base feeds) looks and
 * sounds the same: it is a real `role="switch"` button with `aria-checked`,
 * not a styled checkbox.
 */
export function ToggleSwitch({
  checked,
  disabled,
  onChange,
  label,
  className,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  /** Accessible name, e.g. "启用 solar-cycle 技能". */
  label: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        checked ? "bg-[var(--brand)]" : "bg-muted-foreground/30",
        disabled && "cursor-not-allowed opacity-50",
        className
      )}
    >
      <span
        className={cn(
          "inline-block size-4 transform rounded-full bg-background shadow transition-transform",
          checked ? "translate-x-[1.125rem]" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
