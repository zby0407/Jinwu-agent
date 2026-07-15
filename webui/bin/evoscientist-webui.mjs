#!/usr/bin/env node
import { spawn, execSync } from "child_process";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { readFileSync } from "fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgDir = resolve(__dirname, "..");
const pkg = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf-8"));
const serverEntry = join(pkgDir, "dist", "server.js");

const args = process.argv.slice(2);

if (["-v", "--version", "version"].includes(args[0])) {
  console.log(pkg.version);
  process.exit(0);
}

if (["-h", "--help", "help"].includes(args[0])) {
  console.log(`
${pkg.name} v${pkg.version}

Usage: jinwu-webui [--port <port>]

Starts the 金乌 Web UI and opens it in your browser.
Then enter your 金乌 deployment URL (default http://127.0.0.1:6174)
from \`EvoSci deploy\` to connect.

Options:
  --port <port>   Port to run on (default 4716)
  -v, --version   Show version
  -h, --help      Show this help
`);
  process.exit(0);
}

// Resolve port: --port flag > PORT env > default.
let port = 4716;
const portIdx = args.indexOf("--port");
if (portIdx !== -1 && args[portIdx + 1]) {
  port = parseInt(args[portIdx + 1], 10);
} else if (process.env.PORT && !Number.isNaN(parseInt(process.env.PORT, 10))) {
  port = parseInt(process.env.PORT, 10);
}

const url = `http://localhost:${port}`;
console.log(`  ⏳ Starting 金乌 Web UI on ${url} …`);

const child = spawn(process.execPath, [serverEntry], {
  stdio: "inherit",
  env: {
    ...process.env,
    PORT: String(port),
    HOSTNAME: process.env.HOSTNAME || "127.0.0.1",
    NODE_ENV: "production",
  },
});

child.on("error", (err) => {
  console.error(`  ✗ Failed to start: ${err.message}`);
  process.exit(1);
});

// Poll until the server responds, then open the browser once.
let opened = false;
let waited = 0;
function openBrowser() {
  if (opened) return;
  opened = true;
  const cmd =
    process.platform === "darwin"
      ? `open "${url}"`
      : process.platform === "win32"
      ? `start "" "${url}"`
      : `xdg-open "${url}"`;
  try {
    execSync(cmd, { stdio: "ignore" });
  } catch {
    // Browser couldn't be opened automatically — the URL is printed above.
  }
}
function poll() {
  fetch(url)
    .then(() => {
      console.log(`  ✓ 金乌 Web UI is running → ${url}`);
      openBrowser();
    })
    .catch(() => {
      waited += 500;
      if (waited < 30000) setTimeout(poll, 500);
    });
}
setTimeout(poll, 800);

child.on("exit", (code) => process.exit(code ?? 0));
process.on("SIGINT", () => child.kill("SIGINT"));
process.on("SIGTERM", () => child.kill("SIGTERM"));
