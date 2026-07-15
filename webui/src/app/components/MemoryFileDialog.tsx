"use client";

import React, { useEffect, useRef, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownContent } from "@/app/components/MarkdownContent";

interface MemoryFile {
  path: string;
  content: string;
  size?: number;
  mtime?: number;
}

/**
 * Read-only modal for a single memory file, used by the chat's click-to-open
 * file-link flow. Mirrors the over-chat behaviour of `WorkspaceFileDialog`
 * so workspace and memory paths feel uniform to the user; we don't reuse
 * that component because the API surface and edit/save semantics differ.
 * v1 is read-only — if the user wants to edit, the Memory view still has the
 * full editor.
 */
export const MemoryFileDialog = React.memo<{
  /** Path under the memory root (e.g. `/memories/foo/bar.md`), or null to
   *  close the dialog. */
  path: string | null;
  onClose: () => void;
}>(({ path, onClose }) => {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Monotonic so a slow fetch can't overwrite a newer selection.
  const reqRef = useRef(0);

  useEffect(() => {
    if (!path) {
      setContent(null);
      setError(null);
      return;
    }
    const reqId = ++reqRef.current;
    setLoading(true);
    setError(null);
    setContent(null);
    void (async () => {
      try {
        const res = await fetch(`/api/memory?path=${encodeURIComponent(path)}`);
        if (!res.ok) {
          const body = (await res.json().catch(() => null)) as {
            error?: string;
          } | null;
          throw new Error(body?.error || `HTTP ${res.status}`);
        }
        const data = (await res.json()) as MemoryFile;
        if (reqRef.current !== reqId) return;
        setContent(typeof data.content === "string" ? data.content : "");
      } catch (e) {
        if (reqRef.current !== reqId) return;
        setError(e instanceof Error ? e.message : "Failed to read file.");
      } finally {
        if (reqRef.current === reqId) setLoading(false);
      }
    })();
  }, [path]);

  const open = path !== null;
  const filename = path ? path.split("/").filter(Boolean).pop() || path : "";
  const isMarkdown = filename.toLowerCase().endsWith(".md");

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="flex h-[80vh] max-h-[80vh] min-w-[60vw] flex-col gap-3 p-6">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <FileText
              className="size-4 shrink-0"
              aria-hidden="true"
            />
            <span className="truncate">{filename}</span>
          </DialogTitle>
          {path && (
            <DialogDescription className="break-all font-mono text-xs">
              {path}
            </DialogDescription>
          )}
        </DialogHeader>
        <ScrollArea className="-mx-2 max-h-[65vh] flex-1 px-2">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          )}
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
              {error}
            </div>
          )}
          {!loading &&
            !error &&
            content !== null &&
            (isMarkdown ? (
              <MarkdownContent content={content} />
            ) : (
              <pre className="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-6">
                {content}
              </pre>
            ))}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
});

MemoryFileDialog.displayName = "MemoryFileDialog";
