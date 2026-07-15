"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type LucideIcon,
  Compass,
  FolderOpen,
  Loader2,
  Pencil,
  Sparkles,
  User,
} from "lucide-react";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

interface IdentityTabProps {
  listing: { entries: Array<{ path: string }> } | null;
  listingLoading: boolean;
}

interface ProfileEntry {
  path: string;
  label: string;
  description: string;
  Icon: LucideIcon;
}

const CORE_ENTRIES: ProfileEntry[] = [
  {
    path: "profile/USER_PROFILE.md",
    label: "\u7528\u6237",
    description: "\u5173\u4e8e\u7814\u7a76\u8005\u7684\u6301\u4e45\u4e0a\u4e0b\u6587",
    Icon: User,
  },
  {
    path: "profile/RESEARCH_TASTE.md",
    label: "\u504f\u597d",
    description: "\u5ba1\u7f8e\u4e0e\u65b9\u6cd5\u504f\u597d",
    Icon: Compass,
  },
  {
    path: "profile/SOUL.md",
    label: "\u6838\u5fc3",
    description: "\u91d1\u4e4c\u7684\u6838\u5fc3\u4ef7\u503c\u4e0e\u6027\u683c",
    Icon: Sparkles,
  },
];

function ProfileRow({
  entry,
  selected,
  onSelect,
}: {
  entry: ProfileEntry;
  selected: boolean;
  onSelect: (e: ProfileEntry) => void;
}) {
  const { Icon } = entry;

  return (
    <button
      type="button"
      onClick={() => onSelect(entry)}
      className={cn(
        "group flex w-full min-w-0 items-center gap-2 rounded-md border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-[var(--brand)]/40 bg-accent"
          : "hover:border-[var(--brand)]/40 border-border bg-[var(--color-surface)] hover:bg-accent/60"
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-md transition-colors",
          selected
            ? "bg-[var(--brand)] text-white"
            : "bg-muted text-[var(--brand)] group-hover:bg-background"
        )}
      >
        <Icon
          className="size-4"
          aria-hidden="true"
        />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">
          {entry.label}
        </span>
        <span className="block truncate text-[11px] text-muted-foreground">
          {entry.description}
        </span>
      </span>
    </button>
  );
}

function ContentPanel({ entry }: { entry: ProfileEntry | null }) {
  const [content, setContent] = useState("");
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqRef = useRef(0);

  useEffect(() => {
    if (!entry) {
      setContent("");
      setDraft("");
      return;
    }
    const reqId = ++reqRef.current;
    setLoading(true);
    setEditing(false);
    setError(null);
    fetch(`/api/memory?path=${encodeURIComponent(entry.path)}`)
      .then(async (r) => {
        const d = (await r.json().catch(() => ({}))) as {
          content?: string;
          error?: string;
        };
        if (!r.ok) {
          throw new Error(d.error || "Failed to load.");
        }
        return d;
      })
      .then((d) => {
        if (reqRef.current !== reqId) return;
        setContent(d.content ?? "");
        setDraft(d.content ?? "");
      })
      .catch((e: unknown) => {
        if (reqRef.current !== reqId) return;
        setContent("");
        setDraft("");
        setError(e instanceof Error ? e.message : "Failed to load.");
      })
      .finally(() => {
        if (reqRef.current === reqId) setLoading(false);
      });
  }, [entry]);

  const save = async () => {
    if (!entry) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/memory", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: entry.path, content: draft }),
      });
      const d = (await res.json()) as { error?: string };
      if (!res.ok) throw new Error(d.error ?? "Failed to save.");
      setContent(draft);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  };

  if (!entry) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Select a profile to view
      </div>
    );
  }

  const body = content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-sm font-medium">{entry.label}</span>
        <div className="flex items-center gap-1.5">
          {editing ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setEditing(false);
                  setDraft(content);
                }}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={save}
                disabled={saving || draft === content}
              >
                {saving ? (
                  <>
                    <Loader2
                      className="size-3.5 animate-spin"
                      aria-hidden="true"
                    />
                    Saving
                  </>
                ) : (
                  "Save"
                )}
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditing(true)}
              disabled={loading}
            >
              <Pencil
                className="size-3.5"
                aria-hidden="true"
              />
              Edit raw
            </Button>
          )}
        </div>
      </div>

      {error && (
        <p className="flex-shrink-0 px-4 py-2 text-xs text-destructive">
          {error}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        {loading ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2
              className="size-4 animate-spin"
              aria-hidden="true"
            />
            {"\u52a0\u8f7d\u4e2d..."}…
          </div>
        ) : editing ? (
          <textarea
            aria-label={`\u7f16\u8f91${entry.label}\u8bb0\u5fc6`}
            className="size-full resize-none bg-background p-4 font-mono text-sm text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
          />
        ) : (
          <ScrollArea className="h-full">
            <div className="px-4 py-4">
              <MarkdownContent content={body} />
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}

export function IdentityTab({ listing, listingLoading }: IdentityTabProps) {
  const [selected, setSelected] = useState<ProfileEntry | null>(null);
  const [isDesktop, setIsDesktop] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const update = () => setIsDesktop(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  const projectEntries: ProfileEntry[] = useMemo(
    () =>
      (listing?.entries ?? [])
        .filter(
          (e) =>
            e.path.startsWith("profile/projects/") && e.path.endsWith(".md")
        )
        .map((e) => {
          const segments = e.path.split("/");
          const id =
            segments.length >= 4
              ? `${segments[2]}/${segments.slice(3).join("/")}`
              : e.path;
          return {
            path: e.path,
            label: id.replace(/\/PROJECT_PROFILE\.md$/, ""),
            description: "\u9879\u76ee\u6863\u6848",
            Icon: FolderOpen,
          };
        }),
    [listing?.entries]
  );

  const entries = useMemo(
    () => [...CORE_ENTRIES, ...projectEntries],
    [projectEntries]
  );

  useEffect(() => {
    if (listingLoading || entries.length === 0) return;
    if (!selected || !entries.some((entry) => entry.path === selected.path)) {
      setSelected(entries[0]);
    }
  }, [entries, listingLoading, selected]);

  const sidebar = (
    <ScrollArea className="h-full">
      <div className="p-3">
        <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
          {"\u4e2a\u4eba\u6863\u6848"}
        </p>
        <div className="space-y-1.5">
          {CORE_ENTRIES.map((entry) => (
            <ProfileRow
              key={entry.path}
              entry={entry}
              selected={selected?.path === entry.path}
              onSelect={setSelected}
            />
          ))}
        </div>

        <p className="mb-2 mt-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
          {"\u9879\u76ee\u6863\u6848"}
        </p>
        {listingLoading ? (
          <div className="flex h-10 items-center gap-2 text-xs text-muted-foreground">
            <Loader2
              className="size-3.5 animate-spin"
              aria-hidden="true"
            />
            {"\u52a0\u8f7d\u4e2d..."} projects…
          </div>
        ) : projectEntries.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {"\u6682\u65e0\u9879\u76ee\u6863\u6848\u3002"}
          </p>
        ) : (
          <div className="space-y-1.5">
            {projectEntries.map((entry) => (
              <ProfileRow
                key={entry.path}
                entry={entry}
                selected={selected?.path === entry.path}
                onSelect={setSelected}
              />
            ))}
          </div>
        )}
      </div>
    </ScrollArea>
  );

  if (!isDesktop) {
    return (
      <div className="flex min-h-0 w-full flex-1 flex-col">
        <div className="max-h-[42svh] flex-shrink-0 overflow-hidden border-b border-border">
          {sidebar}
        </div>
        <div className="min-h-0 flex-1">
          <ContentPanel entry={selected} />
        </div>
      </div>
    );
  }

  return (
    <ResizablePanelGroup
      direction="horizontal"
      autoSaveId="evomemory-identity"
      className="h-full"
    >
      <ResizablePanel
        defaultSize={35}
        minSize={22}
        maxSize={55}
      >
        {sidebar}
      </ResizablePanel>

      <ResizableHandle />

      <ResizablePanel
        defaultSize={65}
        minSize={45}
      >
        <ContentPanel entry={selected} />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
