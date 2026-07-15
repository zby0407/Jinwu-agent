// Assemble a self-contained server into dist/ from the Next standalone output.
// `next build` with output:"standalone" produces:
//   .next/standalone/  (server.js + traced node_modules + .next/server)
//   .next/static/      (NOT inside standalone — must be copied)
// We copy everything into dist/ so the published package ships one folder that
// `node dist/server.js` can run.
import { cp, rm, readdir } from "fs/promises";
import { existsSync } from "fs";

const STANDALONE = ".next/standalone";
const STATIC = ".next/static";
const PUBLIC = "public";
const OUT = "dist";

if (!existsSync(STANDALONE)) {
  console.error(
    `✗ ${STANDALONE} not found. Did "next build" run with output:"standalone"?`
  );
  process.exit(1);
}

await rm(OUT, { recursive: true, force: true });
await cp(STANDALONE, OUT, { recursive: true });
await cp(STATIC, `${OUT}/.next/static`, { recursive: true });
if (existsSync(PUBLIC)) {
  await cp(PUBLIC, `${OUT}/public`, { recursive: true });
}

// Next copies the whole project root into the standalone bundle. Prune it down
// to just the runtime essentials (drops src/, configs, and local notes so
// they never get published).
const KEEP = new Set([
  "server.js",
  ".next",
  "node_modules",
  "package.json",
  "public",
]);
for (const entry of await readdir(OUT)) {
  if (!KEEP.has(entry)) {
    await rm(`${OUT}/${entry}`, { recursive: true, force: true });
  }
}

// Strip source maps (*.map): debug-only artifacts the published runtime never
// needs. Removing them keeps the package lean and avoids shipping a stack-trace
// → source mapping. Safe to delete — source maps are never required at runtime.
let strippedMaps = 0;
for (const entry of await readdir(OUT, { recursive: true })) {
  if (entry.endsWith(".map")) {
    await rm(`${OUT}/${entry}`, { force: true });
    strippedMaps++;
  }
}

console.log(
  `✓ Assembled standalone server into ${OUT}/ ` +
    `(${strippedMaps} source map(s) stripped; run: node ${OUT}/server.js)`
);
