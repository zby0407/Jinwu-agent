// Install a skill the user brings themselves: a folder from the browser, a .zip
// archive, or a directory that already exists on the server.
//
// Body shapes:
//   application/json       -> { path, name?, overwrite? }  (server directory)
//   multipart/form-data    -> archive (one .zip)  OR  files (many files)
//                             + name? + overwrite?
//
// File parts should carry their relative path as the multipart filename
// (`formData.append("files", file, file.webkitRelativePath)`), because the
// browser never transmits `webkitRelativePath` on its own.

import { NextRequest, NextResponse } from "next/server";
import {
  importSkillFromDirectory,
  importSkillFromUploads,
  importSkillFromZip,
  SkillImportError,
} from "@/lib/server/skillImport";
import { MAX_SKILL_BYTES } from "@/lib/server/skills";
import { isCrossOrigin } from "@/lib/server/workspace";

export const runtime = "nodejs";

/** Multipart framing adds a little overhead per part — allow for it. */
const MULTIPART_OVERHEAD_BYTES = 8 * 1024 * 1024;

function isTruthy(value: unknown): boolean {
  return value === true || value === "1" || value === "true";
}

function asName(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

/** `my-skill.zip` -> `my-skill` (only when it would pass name validation). */
function nameFromArchive(fileName: string): string | undefined {
  const stem = fileName.replace(/\.zip$/i, "").trim();
  return stem || undefined;
}

/** The relative path a File part was stored under (`/`-separated). */
function relativePathOf(file: File): string {
  const withRelative = file as File & { webkitRelativePath?: string };
  const relative = withRelative.webkitRelativePath;
  return relative && relative.trim() ? relative : file.name;
}

function errorResponse(error: unknown): NextResponse {
  if (error instanceof SkillImportError) {
    const status =
      error.code === "exists" ? 409 : error.code === "too-large" ? 413 : 400;
    return NextResponse.json({ error: error.message }, { status });
  }
  return NextResponse.json(
    {
      error: `导入技能失败: ${
        error instanceof Error ? error.message : "未知错误"
      }`,
    },
    { status: 500 }
  );
}

export async function POST(request: NextRequest) {
  try {
    if (isCrossOrigin(request)) {
      return NextResponse.json(
        { error: "不允许跨域导入技能。" },
        { status: 403 }
      );
    }

    const contentType = request.headers.get("content-type") ?? "";

    if (contentType.includes("application/json")) {
      const body = (await request.json().catch(() => null)) as {
        path?: unknown;
        name?: unknown;
        overwrite?: unknown;
      } | null;
      const path = asName(body?.path);
      if (!path) {
        return NextResponse.json(
          { error: "缺少技能目录路径。" },
          { status: 400 }
        );
      }
      const result = await importSkillFromDirectory(path, {
        name: asName(body?.name),
        overwrite: isTruthy(body?.overwrite),
      });
      return NextResponse.json({ ok: true, ...result });
    }

    if (!contentType.includes("multipart/form-data")) {
      return NextResponse.json(
        { error: "请以 multipart/form-data 或 application/json 提交。" },
        { status: 400 }
      );
    }

    // Reject clearly-oversized bodies before buffering the whole upload. The
    // per-file checks in skillImport.ts are the real enforcement.
    const declaredLength = Number(request.headers.get("content-length") ?? "");
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > MAX_SKILL_BYTES + MULTIPART_OVERHEAD_BYTES
    ) {
      return NextResponse.json(
        {
          error: `技能体积过大（最多 ${Math.round(
            MAX_SKILL_BYTES / 1024 / 1024
          )} MB）。`,
        },
        { status: 413 }
      );
    }

    const form = await request.formData();
    const name = asName(form.get("name"));
    const overwrite = isTruthy(form.get("overwrite"));

    const archive = form.get("archive");
    if (archive instanceof File && archive.size > 0) {
      const bytes = new Uint8Array(await archive.arrayBuffer());
      try {
        const result = await importSkillFromZip(bytes, { name, overwrite });
        return NextResponse.json({ ok: true, ...result });
      } catch (error) {
        // A flat archive (SKILL.md at its root) carries no folder name; retry
        // with the archive's own name so `my-skill.zip` still installs.
        const fallback = nameFromArchive(archive.name);
        if (
          !name &&
          fallback &&
          error instanceof SkillImportError &&
          error.code === "invalid"
        ) {
          const result = await importSkillFromZip(bytes, {
            name: fallback,
            overwrite,
          });
          return NextResponse.json({ ok: true, ...result });
        }
        throw error;
      }
    }

    const uploads = form
      .getAll("files")
      .filter((value): value is File => value instanceof File && !!value.name);
    if (uploads.length === 0) {
      return NextResponse.json(
        { error: "请选择要导入的技能文件夹或 .zip 压缩包。" },
        { status: 400 }
      );
    }
    const files = await Promise.all(
      uploads.map(async (file) => ({
        path: relativePathOf(file),
        data: new Uint8Array(await file.arrayBuffer()),
      }))
    );
    const result = await importSkillFromUploads(files, { name, overwrite });
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    return errorResponse(error);
  }
}
