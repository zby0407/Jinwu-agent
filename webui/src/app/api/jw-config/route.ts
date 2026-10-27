import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import { jwConfigPath } from "@/lib/server/jwPaths";

const DEFAULT_PORT = 6174;
const CONFIG_PATH = jwConfigPath();

// Resolve the JW langgraph dev port the same way the backend does:
// env override > config.yaml > default. SECURITY: only the single
// `langgraph_dev_port` value is extracted — API keys and every other field in
// config.yaml are never read or returned.
async function resolvePort(): Promise<number> {
  const envPort =
    process.env.JW_LANGGRAPH_DEV_PORT || process.env.agent_LANGGRAPH_DEV_PORT;
  if (envPort && /^\d+$/.test(envPort.trim())) {
    return parseInt(envPort.trim(), 10);
  }
  try {
    const yaml = await fs.readFile(CONFIG_PATH, "utf-8");
    const m = yaml.match(/^\s*langgraph_dev_port:\s*(\d+)\s*$/m);
    if (m) return parseInt(m[1], 10);
  } catch {
    // No config file — fall through to the default.
  }
  return DEFAULT_PORT;
}

export async function GET() {
  const port = await resolvePort();
  return NextResponse.json({
    port,
    deploymentUrl: `http://127.0.0.1:${port}`,
  });
}
