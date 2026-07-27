"use client";

import { Toaster } from "sonner";

/** Sonner toaster uses the application's only supported color scheme. */
export function ThemedToaster() {
  return <Toaster theme="dark" />;
}
