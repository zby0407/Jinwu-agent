"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  fetchLiteratureCredentials,
  saveLiteratureCredentials,
  type LiteratureCredential,
} from "@/lib/literatureCredentials";

function SourceBadge({ item }: { item: LiteratureCredential }) {
  if (item.source === "environment") {
    return (
      <span className="bg-[var(--color-success)]/10 inline-flex rounded-full px-2 py-0.5 text-[11px] text-[var(--color-success)]">
        环境变量
      </span>
    );
  }
  if (item.configured) {
    return (
      <span className="bg-[var(--color-success)]/10 inline-flex rounded-full px-2 py-0.5 text-[11px] text-[var(--color-success)]">
        已保存
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
      未配置
    </span>
  );
}

export function LiteratureCredentials() {
  const [items, setItems] = useState<LiteratureCredential[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const catalog = await fetchLiteratureCredentials();
      setItems(catalog.items);
    } catch (err) {
      setItems(null);
      setError(err instanceof Error ? err.message : "无法读取文献源凭据。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = Object.fromEntries(
    Object.entries(drafts).filter(([, value]) => value.trim().length > 0)
  );

  async function save() {
    if (Object.keys(pending).length === 0) {
      setError("没有新的凭据需要保存。");
      return;
    }
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const result = await saveLiteratureCredentials(pending);
      setItems(result.items);
      setDrafts({});
      setNote(result.note || "已保存。环境变量优先于本机配置文件。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  async function clear(item: LiteratureCredential) {
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const result = await saveLiteratureCredentials({ [item.provider]: "" });
      setItems(result.items);
      setDrafts((current) => {
        const next = { ...current };
        delete next[item.provider];
        return next;
      });
      setNote(
        item.env_override
          ? `已清除本机保存的 ${item.label}；当前仍由环境变量 ${item.env} 提供。`
          : `已清除 ${item.label}。`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除失败。");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
        <Loader2
          className="size-4 animate-spin"
          aria-hidden="true"
        />
        正在读取文献源凭据…
      </div>
    );
  }

  if (!items) {
    return (
      <div className="space-y-3">
        <p
          role="alert"
          className="bg-[var(--color-error)]/10 rounded-md px-3 py-2 text-xs text-[var(--color-error)]"
        >
          {error || "无法读取文献源凭据。"}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load()}
        >
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <KeyRound
            className="size-4 text-[var(--brand)]"
            aria-hidden="true"
          />
          文献源 API 凭据
        </h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          保存在运行后端的这台机器上（<code>~/.config/jw/config.yaml</code>，
          权限 0600），不会写入浏览器。环境变量优先；这里保存的值不会回显。
        </p>
      </div>

      {error && (
        <p
          role="alert"
          className="bg-[var(--color-error)]/10 rounded-md px-3 py-2 text-xs text-[var(--color-error)]"
        >
          {error}
        </p>
      )}
      {note && (
        <p className="bg-[var(--color-success)]/10 flex items-start gap-2 rounded-md px-3 py-2 text-xs text-[var(--color-success)]">
          <ShieldCheck
            className="mt-0.5 size-3.5 shrink-0"
            aria-hidden="true"
          />
          {note}
        </p>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <div
            key={item.provider}
            className={cn(
              "rounded-lg border px-3 py-3",
              item.configured
                ? "border-border/70"
                : "border-dashed border-border"
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <p className="truncate text-xs font-medium">{item.label}</p>
                <SourceBadge item={item} />
                {item.env_override && (
                  <span className="text-[10px] text-[var(--color-warning)]">
                    环境变量覆盖中
                  </span>
                )}
              </div>
              <code className="text-[10px] text-muted-foreground">
                {item.env}
              </code>
            </div>
            <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
              {item.hint}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <Input
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={drafts[item.provider] ?? ""}
                onChange={(event) => {
                  setDrafts((current) => ({
                    ...current,
                    [item.provider]: event.target.value,
                  }));
                  if (error) setError(null);
                }}
                placeholder={
                  item.configured
                    ? `已保存 ${item.masked}，留空保持不变`
                    : item.kind === "email"
                    ? "name@example.org"
                    : "粘贴凭据"
                }
                aria-label={item.label}
              />
              {item.configured && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={saving}
                  onClick={() => void clear(item)}
                  title={`清除 ${item.label}`}
                >
                  <Trash2
                    className="size-3.5"
                    aria-hidden="true"
                  />
                  清除
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
        <p className="text-[11px] text-muted-foreground">
          保存后下一次检索立即生效，无需重启。
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={saving}
            onClick={() => {
              setDrafts({});
              setError(null);
            }}
          >
            清空输入
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={saving || Object.keys(pending).length === 0}
            onClick={() => void save()}
          >
            {saving && (
              <Loader2
                className="size-4 animate-spin"
                aria-hidden="true"
              />
            )}
            保存凭据
          </Button>
        </div>
      </div>
    </div>
  );
}
