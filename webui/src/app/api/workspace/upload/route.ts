import { promises as fs } from "fs";
import { basename, dirname, join, resolve } from "path";
import { createHash, randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceDir,
  hasControlChar,
  isCrossOrigin,
  safeResolve,
} from "@/lib/server/workspace";

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
    throw new Error("文件名无效。" );
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
      throw new Error("文件路径无效。" );
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
    if (isCrossOrigin(request)) {
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
      { error: "所选文件超过 100 MB 上传上限。" },
        { status: 413 }
      );
    }

    const formData = await request.formData();
    const files = formData
      .getAll("files")
      .filter((value): value is File => typeof value !== "string");
    if (files.length === 0) {
      return NextResponse.json(
      { error: "请至少选择一个要上传的文件。" },
        { status: 400 }
      );
    }
    if (files.length > MAX_FILES) {
      return NextResponse.json(
      { error: `每次最多上传 ${MAX_FILES} 个文件。` },
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
      throw new Error("所选文件超过 100 MB 上传上限。" );
      }
      return { file, fileName: sanitizeFileName(file.name) };
    });

    // Lands in the working directory of the currently running deployment, so the
    // agent can read the files via its workspace file tools.
    const threadId = request.nextUrl.searchParams.get("threadId");
    const workspaceDir = await getWorkspaceDir(threadId);
    const inputDir = await safeResolve(workspaceDir, "inputs");
    const uploadedFiles: { name: string; path: string; size: number }[] = [];
    const manifestRows: Array<{
      name: string;
      path: string;
      size: number;
      sha256: string;
      uploaded_at: string;
    }> = [];
    const writtenPaths: string[] = [];
    try {
      for (const { file, fileName } of validatedFiles) {
        const content = new Uint8Array(await file.arrayBuffer());
        const savedName = await writeUniqueFile(inputDir, fileName, content);
        writtenPaths.push(resolve(inputDir, savedName));
        uploadedFiles.push({
          name: savedName,
          path: `/inputs/${savedName}`,
          size: file.size,
        });
        manifestRows.push({
          name: savedName,
          path: `inputs/${savedName}`,
          size: file.size,
          sha256: createHash("sha256").update(content).digest("hex"),
          uploaded_at: new Date().toISOString(),
        });
      }
      const manifestPath = join(workspaceDir, "input_manifest.json");
      const currentManifest = JSON.parse(
        await fs.readFile(manifestPath, "utf-8")
      ) as { schema_version?: number; thread_id?: string; inputs?: unknown[] };
      const nextManifest = {
        ...currentManifest,
        inputs: [
          ...(Array.isArray(currentManifest.inputs)
            ? currentManifest.inputs
            : []),
          ...manifestRows,
        ],
      };
      const tempManifest = `${manifestPath}.${process.pid}.${randomUUID()}.tmp`;
      await fs.writeFile(
        tempManifest,
        JSON.stringify(nextManifest, null, 2) + "\n",
        "utf-8"
      );
      await fs.rename(tempManifest, manifestPath);
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
        error instanceof Error ? error.message : "上传文件失败。",
      },
      { status: 400 }
    );
  }
}
