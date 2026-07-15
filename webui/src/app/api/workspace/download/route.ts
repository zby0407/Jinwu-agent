import { createReadStream, promises as fs } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { randomUUID } from "crypto";
import { spawn } from "child_process";
import { Readable } from "stream";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  zipExcludeArgs,
  isCrossOrigin,
} from "@/lib/server/workspace";

export const runtime = "nodejs";

/** Zip `workspaceDir` (minus the ignore list) into `outFile` using the OS `zip`.
 *  Aborts (and kills the child) if `signal` fires — e.g. the client disconnects
 *  mid-archive. */
function zipWorkspace(
  workspaceDir: string,
  outFile: string,
  signal?: AbortSignal
): Promise<void> {
  return new Promise((resolve, reject) => {
    // -r recurse, -q quiet, -X drop extra file attributes, -y store symlinks AS
    // symlinks instead of dereferencing them (so a symlink pointing outside the
    // workspace can't pull external file *contents* into the archive).
    // Exclusions come from the shared ignore lists so the archive matches the
    // tree exactly (dotfiles, large_tool_results/conversation_history, build noise).
    const child = spawn(
      "zip",
      ["-r", "-q", "-X", "-y", outFile, ".", ...zipExcludeArgs()],
      { cwd: workspaceDir }
    );

    const onAbort = () => child.kill("SIGKILL");
    if (signal) {
      if (signal.aborted) {
        child.kill("SIGKILL");
        reject(new Error("Request aborted."));
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }

    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", (err) => {
      signal?.removeEventListener("abort", onAbort);
      reject(
        (err as NodeJS.ErrnoException).code === "ENOENT"
          ? new Error(
              "The `zip` command is not available on this system, so the workspace can't be downloaded as an archive."
            )
          : err
      );
    });
    child.on("close", (code) => {
      signal?.removeEventListener("abort", onAbort);
      if (signal?.aborted) reject(new Error("Request aborted."));
      // 12 = "nothing to do" (empty workspace) — treat as a friendly error.
      else if (code === 12) reject(new Error("The workspace is empty."));
      else if (code !== 0)
        reject(new Error(stderr.trim() || `zip exited with code ${code}`));
      else resolve();
    });
  });
}

export async function GET(request: NextRequest) {
  let tmpFile: string | null = null;
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "Cross-origin workspace access is not allowed." },
        { status: 403 }
      );
    }

    const workspaceDir = await getWorkspaceDir();
    tmpFile = join(tmpdir(), `jinwu-workspace-${randomUUID()}.zip`);
    await zipWorkspace(workspaceDir, tmpFile, request.signal);

    const stat = await fs.stat(tmpFile);
    const nodeStream = createReadStream(tmpFile);
    // Delete the temp archive once the response has been fully read (or the
    // client disconnects) — `close` fires in both cases.
    const cleanup = tmpFile;
    nodeStream.on("close", () => void fs.rm(cleanup, { force: true }));
    const webStream = Readable.toWeb(nodeStream) as ReadableStream<Uint8Array>;

    return new NextResponse(webStream, {
      headers: {
        "Content-Type": "application/zip",
        "Content-Length": String(stat.size),
        "Content-Disposition": 'attachment; filename="workspace.zip"',
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    if (tmpFile) await fs.rm(tmpFile, { force: true }).catch(() => {});
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to package the workspace.",
      },
      { status: 400 }
    );
  }
}
