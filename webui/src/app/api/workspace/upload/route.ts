import { promises as fs } from "fs";
import { basename, dirname, resolve } from "path";
import { NextRequest, NextResponse } from "next/server";
import { getWorkspaceDir, hasControlChar } from "@/lib/server/workspace";

export const runtime = "nodejs";

const MAX_FILE_BYTES = 50 * 1024 * 1024;
const MAX_TOTAL_BYTES = 100 * 1024 * 1024;
const MAX_FILES = 20;

function sanitizeFileName(name: string) {
  const fileName = basename(name.replaceAll("\\", "/")).trim();
  if (
    !fileName ||
    fileName === "." ||
    fileName === ".." ||
    hasControlChar(fileName)
  ) {
    throw new Error("Invalid file name.");
  }
  return fileName;
}

function addSuffix(fileName: string, index: number) {
  const dotIndex = fileName.lastIndexOf(".");
  const stem = dotIndex > 0 ? fileName.slice(0, dotIndex) : fileName;
  const extension = dotIndex > 0 ? fileName.slice(dotIndex) : "";
  return `${stem} (${index})${extension}`;
}

async function writeUniqueFile(
  workspaceDir: string,
  fileName: string,
  content: Uint8Array
) {
  for (let index = 1; ; index += 1) {
    const candidate = index === 1 ? fileName : addSuffix(fileName, index);
    const target = resolve(workspaceDir, candidate);
    if (dirname(target) !== workspaceDir) {
      throw new Error("Invalid file path.");
    }
    try {
      // `wx` never overwrites an existing file (so an upload can't clobber the
      // deployment's own files); collisions get a numeric suffix instead.
      await fs.writeFile(target, content, { flag: "wx" });
      return candidate;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }
}

export async function POST(request: NextRequest) {
  try {
    const origin = request.headers.get("origin");
    if (origin && origin !== request.nextUrl.origin) {
      return NextResponse.json(
        { error: "Cross-origin workspace uploads are not allowed." },
        { status: 403 }
      );
    }

    // Reject oversized bodies before buffering the whole multipart payload.
    // (Best-effort — Content-Length is advisory; the per-file checks below are
    // the real enforcement.)
    const declaredLength = Number(request.headers.get("content-length") ?? "");
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > MAX_TOTAL_BYTES + 1024 * 1024
    ) {
      return NextResponse.json(
        { error: "The selected files exceed the 100 MB upload limit." },
        { status: 413 }
      );
    }

    const formData = await request.formData();
    const files = formData
      .getAll("files")
      .filter((value): value is File => typeof value !== "string");
    if (files.length === 0) {
      return NextResponse.json(
        { error: "Choose at least one file to upload." },
        { status: 400 }
      );
    }
    if (files.length > MAX_FILES) {
      return NextResponse.json(
        { error: `Upload at most ${MAX_FILES} files at a time.` },
        { status: 400 }
      );
    }

    let totalBytes = 0;
    const validatedFiles = files.map((file) => {
      if (file.size > MAX_FILE_BYTES) {
        throw new Error(`${file.name} is larger than the 50 MB upload limit.`);
      }
      totalBytes += file.size;
      if (totalBytes > MAX_TOTAL_BYTES) {
        throw new Error("The selected files exceed the 100 MB upload limit.");
      }
      return { file, fileName: sanitizeFileName(file.name) };
    });

    // Lands in the working directory of the currently running deployment, so the
    // agent can read the files via its workspace file tools.
    const workspaceDir = await getWorkspaceDir();
    const uploadedFiles: { name: string; path: string; size: number }[] = [];
    const writtenPaths: string[] = [];
    try {
      for (const { file, fileName } of validatedFiles) {
        const savedName = await writeUniqueFile(
          workspaceDir,
          fileName,
          new Uint8Array(await file.arrayBuffer())
        );
        writtenPaths.push(resolve(workspaceDir, savedName));
        uploadedFiles.push({
          name: savedName,
          path: `/${savedName}`,
          size: file.size,
        });
      }
    } catch (error) {
      // Roll back files already written so a partial upload doesn't linger.
      // allSettled so a cleanup failure can't mask the original error.
      await Promise.allSettled(
        writtenPaths.map((p) => fs.rm(p, { force: true }))
      );
      throw error;
    }

    return NextResponse.json({ files: uploadedFiles });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to upload files.",
      },
      { status: 400 }
    );
  }
}
