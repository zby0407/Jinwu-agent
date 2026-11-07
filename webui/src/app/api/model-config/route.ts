import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// The desktop shell owns credentials and persistence. It exposes a loopback
// bridge (random port + per-launch ticket, injected as env) so this route can
// read or update provider/model/API settings without the browser ever seeing a
// key or gaining Node privileges. Outside the desktop app the bridge env is
// absent and the UI falls back to read-only.
const BRIDGE_URL = process.env.JW_DESKTOP_BRIDGE_URL;
const BRIDGE_TICKET = process.env.JW_DESKTOP_BRIDGE_TICKET;
const GET_TIMEOUT_MS = 15_000;
const POST_TIMEOUT_MS = 90_000;

function bridgeAvailable(): boolean {
  return Boolean(BRIDGE_URL && BRIDGE_TICKET);
}

async function callBridge(
  method: "GET" | "POST",
  body?: unknown
): Promise<{ status: number; payload: Record<string, unknown> }> {
  const headers: Record<string, string> = {
    "x-jw-bridge-ticket": BRIDGE_TICKET as string,
  };
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(BRIDGE_URL as string, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal: AbortSignal.timeout(
      method === "GET" ? GET_TIMEOUT_MS : POST_TIMEOUT_MS
    ),
  });
  const payload = (await response.json().catch(() => ({}))) as Record<
    string,
    unknown
  >;
  return { status: response.status, payload };
}

export async function GET() {
  if (!bridgeAvailable()) {
    return NextResponse.json({ ok: false, available: false });
  }
  try {
    const { status, payload } = await callBridge("GET");
    return NextResponse.json({ ...payload, available: status < 500 }, {
      status,
    });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        available: false,
        error:
          error instanceof Error ? error.message : "无法连接桌面设置服务。",
      },
      { status: 502 }
    );
  }
}

export async function POST(request: Request) {
  if (!bridgeAvailable()) {
    return NextResponse.json(
      { ok: false, available: false, error: "仅桌面版支持在此修改模型与 API。" },
      { status: 409 }
    );
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "请求内容不是有效 JSON。" },
      { status: 400 }
    );
  }
  try {
    const { status, payload } = await callBridge("POST", body);
    return NextResponse.json(payload, { status });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error:
          error instanceof Error ? error.message : "无法连接桌面设置服务。",
      },
      { status: 502 }
    );
  }
}
