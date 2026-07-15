"use client";

import React, { useMemo, useState, useEffect, useRef } from "react";
import {
  Download,
  Loader2,
  FileText,
  Pencil,
  Save,
  Trash2,
  Eye,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { MarkdownContent } from "@/app/components/MarkdownContent";

const LANGUAGE_MAP: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  cpp: "cpp",
  c: "c",
  cs: "csharp",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  json: "json",
  jsonl: "json",
  xml: "xml",
  html: "html",
  css: "css",
  scss: "scss",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  tex: "latex",
  bib: "latex",
  r: "r",
};

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);
// Extensions we render as text. Anything not here and not an image/pdf is
// treated as a binary download.
const TEXT_EXTS = new Set([
  ...Object.keys(LANGUAGE_MAP),
  "txt",
  "md",
  "markdown",
  "log",
  "csv",
  "tsv",
  "cfg",
  "conf",
  "env",
  "gitignore",
]);
// Inline text preview is capped — bigger files are offered as a download so we
// never pull tens of MB into the browser just to render it.
const MAX_INLINE_TEXT_BYTES = 2 * 1024 * 1024;

export function workspaceFileUrl(path: string, download = false): string {
  const qs = new URLSearchParams({ path });
  if (download) qs.set("download", "1");
  return `/api/workspace/file?${qs.toString()}`;
}

type Kind = "text" | "image" | "pdf" | "binary";

function kindOf(ext: string): Kind {
  if (IMAGE_EXTS.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (TEXT_EXTS.has(ext)) return "text";
  return "binary";
}

export const WorkspaceFileDialog = React.memo<{
  /** Path relative to the workspace root, or null to close. */
  path: string | null;
  /** Byte size from the listing — used to gate inline text preview. */
  size?: number;
  onClose: () => void;
  /** Called after a successful save or delete so the listing can refresh. */
  onChanged?: () => void;
}>(({ path, size, onClose, onChanged }) => {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Edit / save / delete state.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [discardAction, setDiscardAction] = useState<
    "close" | "cancel-edit" | null
  >(null);

  // Suppress state updates after unmount — closing the inspector panel mid
  // save/delete unmounts this dialog while a request is still in flight.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const name = path ? path.split("/").pop() || path : "";
  const ext = useMemo(
    () => (name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""),
    [name]
  );
  const kind = kindOf(ext);
  const tooBigForText =
    kind === "text" && size != null && size > MAX_INLINE_TEXT_BYTES;
  const editable = kind === "text" && !tooBigForText;

  useEffect(() => {
    if (!path || kind !== "text" || tooBigForText) {
      setContent(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEditing(false);
    setActionError(null);
    fetch(workspaceFileUrl(path))
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.error || `Failed to load file (${res.status})`);
        }
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setContent(text);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? "Failed to load file.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, kind, tooBigForText]);

  if (!path) return null;

  const isMarkdown = ext === "md" || ext === "markdown";
  const language = LANGUAGE_MAP[ext] || "text";

  const dirty = editing && content !== null && draft !== content;
  const runOrConfirmDiscard = (action: "close" | "cancel-edit") => {
    if (!dirty) {
      if (action === "close") onClose();
      else setEditing(false);
      return;
    }
    setDiscardAction(action);
  };

  const requestClose = () => {
    runOrConfirmDiscard("close");
  };

  const startEdit = () => {
    setDraft(content ?? "");
    setActionError(null);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setActionError(null);
    try {
      const res = await fetch(workspaceFileUrl(path), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: draft }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error || "Failed to save.");
      if (!mountedRef.current) return;
      setContent(draft);
      setEditing(false);
      onChanged?.();
    } catch (e) {
      if (mountedRef.current) {
        setActionError(e instanceof Error ? e.message : "Failed to save.");
      }
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  };

  const remove = async () => {
    setDeleting(true);
    setActionError(null);
    try {
      const res = await fetch(workspaceFileUrl(path), { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error || "Failed to delete.");
      }
      onChanged?.();
      onClose();
    } catch (e) {
      if (mountedRef.current) {
        // Close the confirm dialog so the error (rendered in the main dialog)
        // isn't hidden behind the confirm overlay.
        setDeleteOpen(false);
        setActionError(e instanceof Error ? e.message : "Failed to delete.");
      }
    } finally {
      if (mountedRef.current) setDeleting(false);
    }
  };

  const confirmDiscardChanges = () => {
    const action = discardAction;
    setDiscardAction(null);
    if (action === "close") onClose();
    if (action === "cancel-edit") setEditing(false);
  };

  return (
    <>
      <Dialog
        open={true}
        onOpenChange={(open) => {
          if (!open) requestClose();
        }}
      >
        <DialogContent
          aria-describedby={undefined}
          className="flex h-[80vh] max-h-[80vh] min-w-[60vw] flex-col p-6"
        >
          <DialogTitle className="sr-only">{path}</DialogTitle>
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-border pb-4">
            <div className="flex min-w-0 items-center gap-2">
              <FileText
                className="h-5 w-5 shrink-0 text-[var(--color-text-tertiary)]"
                aria-hidden="true"
              />
              <span className="overflow-hidden text-ellipsis whitespace-nowrap text-base font-medium text-primary">
                {path}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {editing ? (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2"
                    onClick={() => {
                      runOrConfirmDiscard("cancel-edit");
                    }}
                    disabled={saving}
                  >
                    <Eye
                      size={16}
                      className="mr-1"
                      aria-hidden="true"
                    />
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    className="h-8 bg-[var(--brand-solid)] px-3 text-[var(--brand-foreground)] hover:opacity-90"
                    onClick={save}
                    disabled={saving || !dirty}
                  >
                    {saving ? (
                      <Loader2
                        size={16}
                        className="mr-1 animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <Save
                        size={16}
                        className="mr-1"
                        aria-hidden="true"
                      />
                    )}
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </>
              ) : (
                <>
                  {editable && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 px-2"
                      onClick={startEdit}
                      disabled={loading || content === null}
                      aria-label="Edit file"
                    >
                      <Pencil
                        size={16}
                        className="mr-1"
                        aria-hidden="true"
                      />
                      Edit
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2"
                    asChild
                  >
                    <a
                      href={workspaceFileUrl(path, true)}
                      download={name}
                    >
                      <Download
                        size={16}
                        className="mr-1"
                        aria-hidden="true"
                      />
                      Download
                    </a>
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-muted-foreground hover:text-destructive"
                    onClick={() => setDeleteOpen(true)}
                    disabled={deleting}
                    aria-label="Delete file"
                    title="Delete file"
                  >
                    {deleting ? (
                      <Loader2
                        size={16}
                        className="animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <Trash2
                        size={16}
                        aria-hidden="true"
                      />
                    )}
                  </Button>
                </>
              )}
            </div>
          </div>

          {actionError && (
            <p
              role="alert"
              className="mb-2 text-sm text-destructive"
            >
              {actionError}
            </p>
          )}

          <div className="min-h-0 flex-1 overflow-hidden">
            {editing ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                aria-label="File content"
                className="h-full w-full resize-none rounded-md border border-border bg-background p-4 font-mono text-sm leading-relaxed text-foreground outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                placeholder="File is empty…"
              />
            ) : kind === "image" ? (
              <ScrollArea className="h-full rounded-md bg-[var(--color-surface)]">
                <div className="flex items-center justify-center p-4">
                  <img
                    src={workspaceFileUrl(path)}
                    alt={name}
                    className="max-h-full max-w-full object-contain"
                  />
                </div>
              </ScrollArea>
            ) : kind === "pdf" ? (
              <iframe
                src={workspaceFileUrl(path)}
                title={name}
                className="h-full w-full rounded-md border border-border"
              />
            ) : kind === "binary" || tooBigForText ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-12 text-center">
                <p className="text-sm text-muted-foreground">
                  {tooBigForText
                    ? "This file is too large to preview inline."
                    : "This file type can't be previewed."}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  asChild
                >
                  <a
                    href={workspaceFileUrl(path, true)}
                    download={name}
                  >
                    <Download
                      size={16}
                      className="mr-1"
                      aria-hidden="true"
                    />
                    Download file
                  </a>
                </Button>
              </div>
            ) : loading ? (
              <div className="flex h-full items-center justify-center">
                <Loader2
                  className="size-5 animate-spin text-muted-foreground"
                  aria-hidden="true"
                />
              </div>
            ) : error ? (
              <div className="flex h-full items-center justify-center p-12">
                <p className="text-sm text-destructive">{error}</p>
              </div>
            ) : (
              <ScrollArea className="h-full rounded-md bg-[var(--color-surface)]">
                <div className="p-4">
                  {content && content.length > 0 ? (
                    isMarkdown ? (
                      <div className="rounded-md p-6">
                        <MarkdownContent content={content} />
                      </div>
                    ) : (
                      <SyntaxHighlighter
                        language={language}
                        style={oneDark}
                        customStyle={{
                          margin: 0,
                          borderRadius: "0.5rem",
                          fontSize: "0.875rem",
                        }}
                        showLineNumbers
                        wrapLines={true}
                        lineProps={{ style: { whiteSpace: "pre-wrap" } }}
                      >
                        {content}
                      </SyntaxHighlighter>
                    )
                  ) : (
                    <div className="flex items-center justify-center p-12">
                      <p className="text-sm text-muted-foreground">
                        File is empty
                      </p>
                    </div>
                  )}
                </div>
              </ScrollArea>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={discardAction !== null}
        onOpenChange={(open) => {
          if (!open) setDiscardAction(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Discard unsaved changes?</DialogTitle>
            <DialogDescription>
              Your edits to “{name}” have not been saved.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDiscardAction(null)}
            >
              Keep Editing
            </Button>
            <Button
              onClick={confirmDiscardChanges}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteOpen}
        onOpenChange={(open) => {
          if (!deleting) setDeleteOpen(open);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete file?</DialogTitle>
            <DialogDescription>
              “{name}” will be permanently removed from the workspace. This
              can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              onClick={remove}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
});

WorkspaceFileDialog.displayName = "WorkspaceFileDialog";
