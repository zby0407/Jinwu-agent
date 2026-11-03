#!/usr/bin/env node
/**
 * Sync dsh-genui lazy engine bundles (mermaid/three/echarts/icons/maplibre/
 * standalone-runtime) from node_modules into public/genui-assets so the
 * embedded GenUI renderer can serve them same-origin via
 * setGenuiAssetBase("/genui-assets/").
 *
 * Run manually after `npm install`:
 *   node scripts/sync-genui-assets.mjs
 * The directory is gitignored; a fresh checkout needs this once.
 */
import { cpSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webuiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const srcDir = join(
  webuiRoot,
  "node_modules",
  "@changfenhuang",
  "dsh-genui",
  "lib",
  "assets"
);
const destDir = join(webuiRoot, "public", "genui-assets");

if (!existsSync(srcDir)) {
  console.error(
    `[sync-genui-assets] source missing: ${srcDir}\n` +
      "  Install dependencies first: npm install @changfenhuang/dsh-genui"
  );
  process.exit(1);
}

mkdirSync(destDir, { recursive: true });
let copied = 0;
for (const file of readdirSync(srcDir)) {
  if (!file.endsWith(".js")) continue;
  const src = join(srcDir, file);
  const dest = join(destDir, file);
  const srcMtime = statSync(src).mtimeMs;
  if (existsSync(dest) && statSync(dest).mtimeMs === srcMtime) continue;
  cpSync(src, dest);
  copied += 1;
  console.log(`[sync-genui-assets] ${file}`);
}
if (copied === 0) {
  console.log("[sync-genui-assets] already up to date");
} else {
  console.log(`[sync-genui-assets] copied ${copied} file(s) → public/genui-assets`);
}
