"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/providers/ThemeProvider";

/**
 * Top-bar light/dark toggle. The theme defaults to "system"; clicking flips
 * between explicit light and dark based on what is currently showing.
 *
 * Until mounted, both server and first client render show the Sun icon so they
 * match (no hydration warning); the resolved icon appears right after mount.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = mounted && resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-8 sm:size-9"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? (
        <Moon
          className="size-5"
          aria-hidden="true"
        />
      ) : (
        <Sun
          className="size-5"
          aria-hidden="true"
        />
      )}
    </Button>
  );
}
