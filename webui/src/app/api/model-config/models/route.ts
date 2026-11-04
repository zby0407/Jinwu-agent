import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Ask the provider's live model API through the desktop bridge. The credential
// stays inside the Electron/Python side: the browser only sends the provider
// and, optionally, an unsaved key typed into the form.
const BRIDGE_URL = process.env.JW_DESKTOP_BRIDGE_URL;
const BRIDGE_TICKET = process.env.JW_DESKTOP_BRIDGE_TICKET;
const TIMEOUT_MS = 45_000;

export async function POST(request: Request) {
  if (!BRIDGE_URL || !BRIDGE_TICKET) {
    return NextResponse.json(
      {
        ok: false,
        available: false,
        error: "仅桌面版支持自动获取模型列表。",
      },
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

  let modelsUrl: URL;
  try {
    modelsUrl = new URL(BRIDGE_URL);
    modelsUrl.pathname = "/provider-models";
  } catch {
    return NextResponse.json(
      { ok: false, error: "桌面设置服务地址无效。" },
      { status: 502 }
    );
  }

  try {
    const response = await fetch(modelsUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-jw-bridge-ticket": BRIDGE_TICKET,
      },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const payload = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : "无法连接桌面设置服务。",
      },
      { status: 502 }
    );
  }
}
