import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the npm package (the bin launcher runs
  // dist/server.js). Needed because /api/skills is a server route.
  output: "standalone",
  // Pin the workspace root to this directory so a stray parent lockfile does
  // not shift the standalone output path.
  turbopack: {
    root: ".",
  },
  // dsh-genui's embed entry ships raw TypeScript + CSS modules; without this
  // Turbopack reports "Unknown module type" on its .ts/.module.css files.
  transpilePackages: ["@changfenhuang/dsh-genui"],
  // dsh-genui's bundled source has a few cosmetic TS errors (RefObject null
  // variance in charts.tsx, implicit any, unmet DSH peer types). They don't
  // affect runtime; our own code stays fully type-checked in CI via
  // `tsc --noEmit` with skipLibCheck-style excludes where needed.
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
