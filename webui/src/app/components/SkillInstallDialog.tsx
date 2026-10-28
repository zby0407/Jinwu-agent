"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileArchive, FolderSearch, FolderUp, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** What `/api/skills/import` returns on success. */
export interface InstalledSkillResult {
  name: string;
  files: number;
  bytes: number;
  overwritten: boolean;
}

type SourceMode = "folder" | "zip" | "path";

const MODES: Array<{
  id: SourceMode;
  label: string;
  Icon: typeof FolderUp;
}> = [
  { id: "folder", label: "选择文件夹", Icon: FolderUp },
  { id: "zip", label: "选择 ZIP", Icon: FileArchive },
  { id: "path", label: "本机路径", Icon: FolderSearch },
];

function describeMode(mode: SourceMode): string {
  switch (mode) {
    case "folder":
      return "选中一个包含 SKILL.md 的文件夹，里面的脚本与资源会一起导入。";
    case "zip":
      return "选中一个 .zip 压缩包，包内必须包含 SKILL.md（可在包根目录或唯一一层子目录里）。";
    default:
      return "直接填服务器本机上的绝对路径，例如 /Users/you/Jinwu/skills/my-skill 或 ~/skills/my-skill。";
  }
}

/**
 * 安装/导入用户自己的 Skill：文件夹、zip 包或本机路径三选一，导入进
 * `<DATA_DIR>/skills/<name>/` 后立刻出现在技能列表里并可开关。
 */
export function SkillInstallDialog({
  open,
  onOpenChange,
  onInstalled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstalled: (result: InstalledSkillResult) => void;
}) {
  const [mode, setMode] = useState<SourceMode>("folder");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [folderFiles, setFolderFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Name the server rejected because it already exists; re-submit overwrites.
  const [conflict, setConflict] = useState<string | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const zipInput = useRef<HTMLInputElement | null>(null);

  // `webkitdirectory` is not part of React's typed input props.
  useEffect(() => {
    const el = folderInput.current;
    if (!el) return;
    el.setAttribute("webkitdirectory", "");
    el.setAttribute("directory", "");
  }, [open]);

  const reset = useCallback(() => {
    setMode("folder");
    setName("");
    setPath("");
    setFolderFiles([]);
    setZipFile(null);
    setOverwrite(false);
    setError(null);
    setConflict(null);
    if (folderInput.current) folderInput.current.value = "";
    if (zipInput.current) zipInput.current.value = "";
  }, []);

  const submit = async (force = false) => {
    setError(null);
    setConflict(null);
    const overrideName = name.trim();
    const overwriteFlag = overwrite || force;
    let request: Promise<Response>;
    if (mode === "path") {
      if (!path.trim()) {
        setError("请填写本机路径。");
        return;
      }
      request = fetch("/api/skills/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: path.trim(),
          name: overrideName || undefined,
          overwrite: overwriteFlag,
        }),
      });
    } else {
      const form = new FormData();
      if (mode === "folder") {
        if (folderFiles.length === 0) {
          setError("请选择一个包含 SKILL.md 的文件夹。");
          return;
        }
        for (const file of folderFiles) {
          const relative =
            (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
            file.name;
          form.append("files", file, relative);
        }
      } else {
        if (!zipFile) {
          setError("请选择一个 .zip 压缩包。");
          return;
        }
        form.append("archive", zipFile, zipFile.name);
      }
      if (overrideName) form.append("name", overrideName);
      if (overwriteFlag) form.append("overwrite", "1");
      request = fetch("/api/skills/import", {
        method: "POST",
        body: form,
      });
    }

    setBusy(true);
    try {
      const res = await request;
      const data = (await res.json().catch(() => ({}))) as
        | (InstalledSkillResult & { ok?: boolean })
        | { error?: string; name?: string };
      if (res.status === 409) {
        setConflict(
          (data as { error?: string }).error || "同名技能已存在，覆盖会替换它的全部文件。"
        );
        return;
      }
      if (!res.ok) {
        throw new Error((data as { error?: string }).error || "导入失败");
      }
      const result = data as InstalledSkillResult & { ok?: boolean };
      onInstalled({
        name: result.name,
        files: result.files ?? 0,
        bytes: result.bytes ?? 0,
        overwritten: Boolean(result.overwritten),
      });
      onOpenChange(false);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset();
        }
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>安装 Skill</DialogTitle>
          <DialogDescription>
            把你的 Skill 导入本机技能目录。导入后它会出现在上方分组里，可以随时开关或卸载。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex gap-1 rounded-lg bg-muted p-1">
            {MODES.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => {
                  setMode(id);
                  setError(null);
                  setConflict(null);
                }}
                aria-pressed={mode === id}
                className={cn(
                  "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
                  mode === id
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon
                  className="size-3.5"
                  aria-hidden="true"
                />
                {label}
              </button>
            ))}
          </div>

          <p className="text-xs leading-5 text-muted-foreground">
            {describeMode(mode)}
          </p>

          {mode === "folder" && (
            <div className="space-y-1.5">
              <input
                ref={folderInput}
                type="file"
                multiple
                className="hidden"
                data-testid="skill-install-folder"
                onChange={(e) => {
                  setFolderFiles(Array.from(e.target.files ?? []));
                  setError(null);
                }}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => folderInput.current?.click()}
              >
                <FolderUp
                  className="mr-1.5 size-4"
                  aria-hidden="true"
                />
                选择文件夹
              </Button>
              {folderFiles.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  已选择 {folderFiles.length} 个文件
                </p>
              )}
            </div>
          )}

          {mode === "zip" && (
            <div className="space-y-1.5">
              <input
                ref={zipInput}
                type="file"
                accept=".zip,application/zip"
                className="hidden"
                data-testid="skill-install-zip"
                onChange={(e) => {
                  setZipFile(e.target.files?.[0] ?? null);
                  setError(null);
                }}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => zipInput.current?.click()}
              >
                <FileArchive
                  className="mr-1.5 size-4"
                  aria-hidden="true"
                />
                选择 ZIP
              </Button>
              {zipFile && (
                <p className="text-xs text-muted-foreground">{zipFile.name}</p>
              )}
            </div>
          )}

          {mode === "path" && (
            <input
              type="text"
              value={path}
              onChange={(e) => {
                setPath(e.target.value);
                setError(null);
              }}
              placeholder="~/skills/my-skill"
              data-testid="skill-install-path"
              className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          )}

          <div className="space-y-1.5">
            <label
              htmlFor="skill-install-name"
              className="text-xs font-medium text-muted-foreground"
            >
              技能名称（可选）
            </label>
            <input
              id="skill-install-name"
              type="text"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              placeholder="留空则使用文件夹名"
              className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
              className="size-3.5 accent-[var(--brand)]"
            />
            覆盖同名技能
          </label>

          {conflict && (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-700">
              {conflict}
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="text-xs text-destructive"
            >
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              reset();
              onOpenChange(false);
            }}
            disabled={busy}
          >
            取消
          </Button>
          {conflict ? (
            <Button
              onClick={() => submit(true)}
              disabled={busy}
            >
              {busy && (
                <Loader2
                  className="mr-1.5 size-4 animate-spin"
                  aria-hidden="true"
                />
              )}
              覆盖安装
            </Button>
          ) : (
            <Button
              onClick={() => submit(false)}
              disabled={busy}
            >
              {busy && (
                <Loader2
                  className="mr-1.5 size-4 animate-spin"
                  aria-hidden="true"
                />
              )}
              导入
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
