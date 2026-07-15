// API for EvoScientist's global memory files (~/.evoscientist/memories).
//   GET                 → list every memory file
//   GET ?path=<rel>     → read one text file's content
//   PUT  {path, content}→ create or overwrite a text file
//   DELETE ?path=<rel>  → delete a file
//
// All mutations are same-origin only (loopback dev UI, no auth) and pass through
// the traversal/symlink guards in lib/server/memory.ts.

import { NextRequest, NextResponse } from "next/server";
import {
  listMemory,
  readMemory,
  writeMemory,
  deleteMemory,
  isCrossOrigin,
} from "@/lib/server/memory";

export const runtime = "nodejs";

// Reject oversized request bodies before buffering/parsing them. The precise
// per-content cap lives in writeMemory; this is a coarse upfront guard (a 1MB
// content string JSON-encodes larger, so allow generous headroom).
const MAX_BODY_BYTES = 4 * 1024 * 1024;

function fail(error: unknown, status = 400) {
  return NextResponse.json(
    {
      error: error instanceof Error ? error.message : "Memory request failed.",
    },
    { status }
  );
}

export async function GET(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return fail("Cross-origin memory access is not allowed.", 403);
    }
    const path = request.nextUrl.searchParams.get("path");
    if (path) {
      const file = await readMemory(path);
      return NextResponse.json(file);
    }
    return NextResponse.json(await listMemory());
  } catch (error) {
    return fail(error);
  }
}

export async function PUT(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return fail("Cross-origin memory access is not allowed.", 403);
    }
    const declaredLen = Number(request.headers.get("content-length") || 0);
    if (declaredLen > MAX_BODY_BYTES) {
      return fail("Request body is too large.", 413);
    }
    const body = (await request.json().catch(() => null)) as {
      path?: unknown;
      content?: unknown;
    } | null;
    if (!body || typeof body.path !== "string") {
      return fail("A file path is required.");
    }
    if (typeof body.content !== "string") {
      return fail("File content is required.");
    }
    const file = await writeMemory(body.path, body.content);
    return NextResponse.json(file);
  } catch (error) {
    return fail(error);
  }
}

export async function DELETE(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return fail("Cross-origin memory access is not allowed.", 403);
    }
    const path = request.nextUrl.searchParams.get("path");
    if (!path) return fail("A file path is required.");
    await deleteMemory(path);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return fail(error);
  }
}
