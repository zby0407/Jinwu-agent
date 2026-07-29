"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Loader2,
  Puzzle,
  RotateCw,
  Store,
  Trash2,
  Download,
  ArrowUpCircle,
} from "lucide-react";
import {
  SkillDetailDialog,
  type SkillDetailTarget,
} from "@/app/components/SkillDetailDialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface SkillCard {
  name: string;
  title: string;
  description: string;
  dir: string;
}

interface CatalogSkill {
  name: string;
  title: string;
  description: string;
  fileCount: number;
  installed: boolean;
  latestVersion?: string;
  installedVersion?: string;
  updateAvailable: boolean;
}

export function SkillsMarketplace() {
  const [other, setOther] = useState<SkillCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The official JWSkills catalog stays hidden behind the header button and
  // is fetched lazily the first time the user opens it.
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalog, setCatalog] = useState<CatalogSkill[] | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  // Per-skill in-flight action, keyed by skill name.
  const [busy, setBusy] = useState<
    Record<string, "install" | "uninstall" | "update">
  >({});
  // Skill whose detail dialog is open (null = closed).
  const [detail, setDetail] = useState<SkillDetailTarget | null>(null);
  const [uninstallTarget, setUninstallTarget] = useState<{
    name: string;
    title: string;
  } | null>(null);

  const load = useCallback(async (_refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/skills");
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Failed to load skills");
      setOther((d.skills ?? []) as SkillCard[]);
    } catch (e) {
      setOther([]);
      setError(
        e instanceof Error ? e.message : "Failed to load installed skills."
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const loadCatalog = useCallback(async (refresh = false) => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const res = await fetch(
        `/api/skills/catalog${refresh ? "?refresh=1" : ""}`
      );
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Failed to load catalog");
      setCatalog((d.skills ?? []) as CatalogSkill[]);
    } catch (e) {
      setCatalog(null);
      setCatalogError(
        e instanceof Error ? e.message : "Failed to load the official catalog."
      );
    }
    setCatalogLoading(false);
  }, []);

  const toggleCatalog = () => {
    const opening = !catalogOpen;
    setCatalogOpen(opening);
    if (opening && catalog === null && !catalogLoading) {
      void loadCatalog();
    }
  };

  // Install and update hit the same endpoint (it overwrites + re-records the
  // manifest commit); the mode only changes the busy label and success state.
  const install = async (
    name: string,
    mode: "install" | "update" = "install"
  ) => {
    setBusy((b) => ({ ...b, [name]: mode }));
    setError(null);
    try {
      const res = await fetch("/api/skills/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Failed to install");
      setCatalog((prev) =>
        prev
          ? prev.map((s) =>
              s.name === name
                ? {
                    ...s,
                    installed: true,
                    installedVersion: d.version ?? s.latestVersion,
                    updateAvailable: false,
                  }
                : s
            )
          : prev
      );
      await load();
    } catch (e) {
      setCatalogError(
        e instanceof Error ? e.message : "Failed to install the skill."
      );
    } finally {
      setBusy((b) => {
        const next = { ...b };
        delete next[name];
        return next;
      });
    }
  };

  const uninstall = async (name: string) => {
    setBusy((b) => ({ ...b, [name]: "uninstall" }));
    setError(null);
    try {
      const res = await fetch(`/api/skills?name=${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to uninstall");
      }
      setOther((prev) => prev.filter((s) => s.name !== name));
      setCatalog((prev) =>
        prev
          ? prev.map((s) =>
              s.name === name
                ? { ...s, installed: false, installedVersion: undefined }
                : s
            )
          : prev
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to uninstall");
    } finally {
      setBusy((b) => {
        const next = { ...b };
        delete next[name];
        return next;
      });
    }
  };

  const confirmUninstall = async () => {
    if (!uninstallTarget) return;
    const target = uninstallTarget;
    setUninstallTarget(null);
    await uninstall(target.name);
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[960px] px-4 py-5 sm:px-5 sm:py-6">
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold sm:text-2xl">
              Research Skills
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Manage locally installed skills.
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={toggleCatalog}
              aria-pressed={catalogOpen}
              aria-label="Official catalog"
              title="Official catalog"
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring ${
                catalogOpen
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              <Store
                className="size-4"
                aria-hidden="true"
              />
              Official catalog
            </button>
            <button
              type="button"
              onClick={() => load(true)}
              disabled={loading}
              aria-label="Refresh"
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RotateCw
                className={loading ? "size-4 animate-spin" : "size-4"}
                aria-hidden="true"
              />
            </button>
          </div>
        </header>

        {error && (
          <p
            role="alert"
            className="mb-4 text-sm text-destructive"
          >
            {error}
          </p>
        )}

        {catalogOpen && (
          <section className="mb-6">
            <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-tertiary">
              Official catalog
            </h3>
            {catalogError && (
              <p
                role="alert"
                className="mb-3 text-sm text-destructive"
              >
                {catalogError}
              </p>
            )}
            {catalogLoading ? (
              <div
                className="flex items-center gap-2 text-sm text-muted-foreground"
                aria-live="polite"
              >
                <Loader2
                  className="size-4 animate-spin"
                  aria-hidden="true"
                />
                Loading catalog…
              </div>
            ) : catalog && catalog.length > 0 ? (
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {catalog.map((s) => (
                  <SkillTile
                    key={s.name}
                    title={s.title}
                    description={s.description}
                    meta={`${s.fileCount} files`}
                    installed={s.installed}
                    installedVersion={s.installedVersion}
                    latestVersion={s.latestVersion}
                    updateAvailable={s.updateAvailable}
                    busy={busy[s.name]}
                    onOpen={() =>
                      setDetail({
                        name: s.name,
                        title: s.title,
                        description: s.description,
                        installed: s.installed,
                      })
                    }
                    onInstall={() => install(s.name)}
                    onUpdate={() => install(s.name, "update")}
                    onUninstall={() =>
                      setUninstallTarget({ name: s.name, title: s.title })
                    }
                  />
                ))}
              </div>
            ) : catalog ? (
              <p className="text-sm text-muted-foreground">
                The official catalog is empty.
              </p>
            ) : null}
          </section>
        )}

        {loading ? (
          <div
            className="flex items-center gap-2 text-sm text-muted-foreground"
            aria-live="polite"
          >
            <Loader2
              className="size-4 animate-spin"
              aria-hidden="true"
            />
            Loading skills…
          </div>
        ) : (
          <div className="space-y-6">
            {other.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No skills installed.
              </p>
            ) : (
              <section>
                <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-tertiary">
                  Installed skills
                </h3>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {other.map((s) => (
                    <SkillTile
                      key={s.name}
                      title={s.title}
                      description={s.description}
                      installed
                      busy={busy[s.name]}
                      onOpen={() =>
                        setDetail({
                          name: s.name,
                          title: s.title,
                          description: s.description,
                          installed: true,
                        })
                      }
                      onUninstall={() =>
                        setUninstallTarget({
                          name: s.name,
                          title: s.title,
                        })
                      }
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>

      <SkillDetailDialog
        skill={detail}
        onClose={() => setDetail(null)}
      />
      <Dialog
        open={uninstallTarget !== null}
        onOpenChange={(open) => {
          if (!open) setUninstallTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Uninstall skill?</DialogTitle>
            <DialogDescription>
              “{uninstallTarget?.title ?? uninstallTarget?.name}” will be
              removed from this Web UI. You can install it again later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setUninstallTarget(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmUninstall}
            >
              Uninstall
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SkillTile({
  title,
  description,
  meta,
  installed,
  installedVersion,
  latestVersion,
  updateAvailable,
  busy,
  onOpen,
  onInstall,
  onUpdate,
  onUninstall,
}: {
  title: string;
  description: string;
  meta?: string;
  installed: boolean;
  installedVersion?: string;
  latestVersion?: string;
  updateAvailable?: boolean;
  busy?: "install" | "uninstall" | "update";
  onOpen?: () => void;
  onInstall?: () => void;
  onUpdate?: () => void;
  onUninstall?: () => void;
}) {
  const versionLabel = installed
    ? installedVersion && `v${installedVersion}`
    : latestVersion && `v${latestVersion}`;
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-3">
      <button
        type="button"
        onClick={onOpen}
        className="-m-1 flex items-start gap-2.5 rounded-md p-1 text-left transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
        title="View details"
      >
        <Puzzle
          className="mt-0.5 size-5 shrink-0 text-[var(--brand)]"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <h3 className="break-words text-lg font-medium leading-tight">
              {title}
            </h3>
            {versionLabel && (
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {versionLabel}
              </span>
            )}
            {meta && (
              <span className="shrink-0 text-xs text-muted-foreground">
                · {meta}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground">
            {description || "No description."}
          </p>
        </div>
      </button>
      <div className="mt-2.5 flex items-center justify-end gap-2">
        {installed && updateAvailable && (
          <button
            type="button"
            onClick={onUpdate}
            disabled={!!busy}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--brand-solid)] px-2.5 py-1 text-xs font-medium text-[var(--brand-foreground)] transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            title={latestVersion ? `Update to v${latestVersion}` : "Update"}
          >
            {busy === "update" ? (
              <Loader2
                className="size-3.5 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <ArrowUpCircle
                className="size-3.5"
                aria-hidden="true"
              />
            )}
            {busy === "update"
              ? "Updating…"
              : latestVersion
              ? `Update → v${latestVersion}`
              : "Update"}
          </button>
        )}
        {installed ? (
          <button
            type="button"
            onClick={onUninstall}
            disabled={!!busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {busy === "uninstall" ? (
              <Loader2
                className="size-3.5 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <Trash2
                className="size-3.5"
                aria-hidden="true"
              />
            )}
            {busy === "uninstall" ? "Removing…" : "Uninstall"}
          </button>
        ) : (
          <button
            type="button"
            onClick={onInstall}
            disabled={!!busy}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--brand-solid)] px-2.5 py-1 text-xs font-medium text-[var(--brand-foreground)] transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {busy === "install" ? (
              <Loader2
                className="size-3.5 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <Download
                className="size-3.5"
                aria-hidden="true"
              />
            )}
            {busy === "install" ? "Installing…" : "Install"}
          </button>
        )}
      </div>
    </div>
  );
}
