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
  Upload,
  Users,
  CheckCircle2,
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
  source: "installed" | "builtin";
  bundle?: string;
  assignment?: { shared: boolean; agents: string[] };
  adaptation?: { name?: string; reason?: string } | null;
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
  assignment?: { shared: boolean; agents: string[] };
  bundle?: string;
  adaptation?: { name?: string; reason?: string } | null;
}

interface AgentGroup {
  name: string;
  title: string;
  description: string;
  skills: SkillCard[];
}

interface AgentProfile {
  name: string;
  title: string;
  description: string;
}

interface LocalCandidate {
  name: string;
  title: string;
  description: string;
  adaptedName: string;
  targetAgents: string[];
  source: "local";
  imported?: boolean;
}

export function SkillsMarketplace() {
  const [other, setOther] = useState<SkillCard[]>([]);
  const [agentGroups, setAgentGroups] = useState<AgentGroup[]>([]);
  const [supportAgents, setSupportAgents] = useState<AgentProfile[]>([]);
  const [sharedSkills, setSharedSkills] = useState<SkillCard[]>([]);
  const [localCandidates, setLocalCandidates] = useState<LocalCandidate[]>([]);
  const [importing, setImporting] = useState(false);
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
      if (!res.ok) throw new Error(d.error || "加载 Skills 失败");
      setOther((d.skills ?? []) as SkillCard[]);
      setAgentGroups((d.agents ?? []) as AgentGroup[]);
      setSupportAgents((d.supportAgentProfiles ?? []) as AgentProfile[]);
      setSharedSkills((d.sharedSkills ?? []) as SkillCard[]);
      setLocalCandidates((d.localCandidates ?? []) as LocalCandidate[]);
    } catch (e) {
      setOther([]);
      setError(e instanceof Error ? e.message : "加载已安装 Skills 失败。");
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
      if (!res.ok) throw new Error(d.error || "加载目录失败");
      setCatalog((d.skills ?? []) as CatalogSkill[]);
    } catch (e) {
      setCatalog(null);
      setCatalogError(e instanceof Error ? e.message : "加载官方目录失败。");
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
      if (!res.ok) throw new Error(d.error || "安装失败");
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
      setCatalogError(e instanceof Error ? e.message : "安装 Skill 失败。");
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
        throw new Error(d.error || "卸载失败");
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
      setError(e instanceof Error ? e.message : "卸载失败");
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

  const importImproved = async () => {
    if (localCandidates.length === 0) return;
    setImporting(true);
    setError(null);
    try {
      const res = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          names: localCandidates.map((candidate) => candidate.name),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "导入技能失败");
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入技能失败");
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[960px] px-4 py-5 sm:px-5 sm:py-6">
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold sm:text-2xl">
              JW 子 Agent 技能编排
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              查看每个子 Agent 的实际技能边界，并将本地技能适配后导入项目。
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={importImproved}
              disabled={importing || localCandidates.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--brand-solid)] px-2.5 py-1.5 text-xs font-medium text-[var(--brand-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
              title="导入并适配本地技能"
            >
              {importing ? (
                <Loader2
                  className="size-4 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <Upload
                  className="size-4"
                  aria-hidden="true"
                />
              )}
              一键导入改进技能
              {localCandidates.length ? `（${localCandidates.length}）` : ""}
            </button>
            <button
              type="button"
              onClick={toggleCatalog}
              aria-pressed={catalogOpen}
              aria-label="JW 官方内置目录"
              title="JW 官方内置目录"
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
              JW 官方内置目录
            </button>
            <button
              type="button"
              onClick={() => load(true)}
              disabled={loading}
              aria-label="刷新"
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
              JW 官方内置目录
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
                正在加载目录…
              </div>
            ) : catalog && catalog.length > 0 ? (
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {catalog.map((s) => (
                  <SkillTile
                    key={s.name}
                    title={s.title}
                    description={s.description}
                    meta={`${s.fileCount} 个文件`}
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
              <p className="text-sm text-muted-foreground">官方目录为空。</p>
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
            正在加载 Skills…
          </div>
        ) : (
          <div className="space-y-6">
            {(sharedSkills.length > 0 || agentGroups.length > 0) && (
              <section aria-label="子 Agent 技能分配">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">JW 主 Agent</h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      主 Agent
                      负责接收研究问题、选择路径并协调下面六个太阳科研子
                      Agent；共享基础技能单独列出，并对所有角色生效。
                    </p>
                  </div>
                  <Users
                    className="size-5 text-[var(--brand)]"
                    aria-hidden="true"
                  />
                </div>
                {sharedSkills.length > 0 && (
                  <div className="mb-3 rounded-lg border border-border/70 bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">
                      所有 Agent 共享
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {sharedSkills.map((skill) => (
                        <SkillBadge
                          key={skill.name}
                          skill={skill}
                          shared
                        />
                      ))}
                    </div>
                  </div>
                )}
                <div className="mb-2 text-xs font-medium text-muted-foreground">
                  六个太阳科研子 Agent
                </div>
                <div className="border-[var(--brand)]/20 grid grid-cols-1 gap-3 border-l-2 pl-3 md:grid-cols-2">
                  {agentGroups.map((group) => (
                    <div
                      key={group.name}
                      className="rounded-lg border border-border bg-card p-3"
                    >
                      <div className="mb-1.5 flex items-center gap-2">
                        <Users
                          className="size-4 text-[var(--brand)]"
                          aria-hidden="true"
                        />
                        <h4 className="font-medium">{group.title}</h4>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {group.name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {group.skills.length} 项专属技能
                        </span>
                      </div>
                      <p className="mb-2 text-xs leading-5 text-muted-foreground">
                        {group.description}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {group.skills.map((skill) => (
                          <SkillBadge
                            key={`${group.name}-${skill.name}`}
                            skill={skill}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {localCandidates.length > 0 && (
              <section
                className="border-[var(--brand)]/40 bg-[var(--brand)]/5 rounded-lg border border-dashed p-3"
                aria-label="待导入本地技能"
              >
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">待导入的本地技能</h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      导入会改写名称、补充 JW
                      科研边界，并按推荐角色分配；不会原样覆盖项目技能。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={importImproved}
                    disabled={importing}
                    className="text-xs font-medium text-[var(--brand)] hover:underline"
                  >
                    立即导入
                  </button>
                </div>
                <div className="space-y-2">
                  {localCandidates.map((candidate) => (
                    <div
                      key={candidate.name}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-background/70 px-2.5 py-2 text-sm"
                    >
                      <span className="min-w-0">
                        <span className="font-medium">{candidate.title}</span>
                        <span className="ml-2 text-xs text-muted-foreground">
                          → {candidate.adaptedName}
                        </span>
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {candidate.targetAgents.length
                          ? candidate.targetAgents.join("、")
                          : "项目基础层"}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {other.some((s) => s.source === "installed") && (
              <section>
                <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-tertiary">
                  已安装的 Skills
                </h3>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {other
                    .filter((s) => s.source === "installed")
                    .map((s) => (
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
            {supportAgents.length > 0 && (
              <div className="rounded-lg border border-border/70 bg-muted/20 p-3 text-xs text-muted-foreground">
                <div className="mb-1 font-medium text-foreground">
                  JW 主 Agent 的底层支持能力
                </div>
                <p className="leading-5">
                  {supportAgents.map((agent) => agent.title).join("、")}
                  。这些能力由 JW 主 Agent
                  按需调用，不作为独立太阳科研角色展示。
                </p>
              </div>
            )}
            {other.length === 0 &&
              agentGroups.length === 0 &&
              localCandidates.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  尚未发现可用的 JW Skills。
                </p>
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
            <DialogTitle>卸载 Skill？</DialogTitle>
            <DialogDescription>
              将从 WebUI 中移除“
              {uninstallTarget?.title ?? uninstallTarget?.name}
              ”。之后仍可重新安装。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setUninstallTarget(null)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={confirmUninstall}
            >
              卸载
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SkillBadge({
  skill,
  shared = false,
}: {
  skill: SkillCard;
  shared?: boolean;
}) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
      title={skill.description}
    >
      <CheckCircle2
        className="size-3 text-emerald-600"
        aria-hidden="true"
      />
      {skill.title || skill.name}
      {shared && <span className="text-muted-foreground">·共享</span>}
    </span>
  );
}

function SkillTile({
  title,
  description,
  meta,
  installed,
  readOnly = false,
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
  readOnly?: boolean;
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
        title="查看详情"
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
            {description || "暂无说明。"}
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
            title={latestVersion ? `更新到 v${latestVersion}` : "更新"}
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
              ? "正在更新…"
              : latestVersion
              ? `更新 → v${latestVersion}`
              : "更新"}
          </button>
        )}
        {installed && !readOnly ? (
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
            {busy === "uninstall" ? "正在移除…" : "卸载"}
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
            {busy === "install" ? "正在安装…" : "安装"}
          </button>
        )}
      </div>
    </div>
  );
}
