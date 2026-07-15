import { type NextRequest, NextResponse } from "next/server";

import { isCrossOrigin, listObservations } from "@/lib/server/memory";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "no-store",
};

export async function GET(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin access not allowed." },
        { status: 403, headers: NO_STORE_HEADERS }
      );
    }
    return NextResponse.json(await listObservations(), {
      headers: NO_STORE_HEADERS,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to load observations.",
      },
      { status: 400, headers: NO_STORE_HEADERS }
    );
  }
}
