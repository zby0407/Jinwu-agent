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
};

export default nextConfig;
