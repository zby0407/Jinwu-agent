"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  fetchScientificDataInterfaces,
  saveScientificDataInterfaces,
  type ScientificDataInterfaceCatalog,
  type ScientificDataInterfaceItem,
} from "@/lib/scientificDataInterfaces";

function SourceBadge({ item }: { item: ScientificDataInterfaceItem }) {
  if (item.source === "environment") {
    return (
      <span className="inline-flex rounded-full bg-[var(--color-success)]/10 px-2 py-0.5 text-[11px] text-[var(--color-success)]">
        环境变量
      </span>
    );
  }
  if (item.configured) {
    return (
      <span className="inline-flex rounded-full bg-[var(--color-success)]/10 px-2 py-0.5 text-[11px] text-[var(--color-success)]">
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

function Field({
  item,
  value,
  onChange,
  onClear,
}: {
  item: ScientificDataInterfaceItem;
  value: string;
  onChange: (next: string) => void;
  onClear: () => void;
}) {
  const options = item.kind === "provider" ? item.options : [];
  return (
    <div className="grid gap-1.5 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={`sdi-${item.field}`} className="text-xs font-medium">
          {item.label}
        </Label>
        <SourceBadge item={item} />
        {item.env_override && (
          <span className="text-[10px] text-muted-foreground">
            环境变量 {item.env} 优先
          </span>
        )}
      </div>
      {options.length > 0 ? (
        <select
          id={`sdi-${item.field}`}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-base outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30 md:text-sm"
        >
          {options.map((option) => (
            <option key={option || "empty"} value={option}>
              {option || "（留空，关闭）"}
            </option>
          ))}
        </select>
      ) : (
        <div className="flex items-center gap-2">
          <Input
            id={`sdi-${item.field}`}
            type={item.kind === "key" ? "password" : "text"}
            autoComplete="off"
            spellCheck={false}
            value={value}
            placeholder={
              item.kind === "key" && item.masked ? item.masked : "未配置"
            }
            onChange={(event) => onChange(event.target.value)}
          />
          {item.kind === "key" && item.configured && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onClear}
              title="清除已保存的值"
            >
              清除
            </Button>
          )}
        </div>
      )}
      <p className="text-[11px] leading-4 text-muted-foreground">{item.hint}</p>
      <p className="font-mono text-[10px] text-muted-foreground/70">
        {item.env}
      </p>
    </div>
  );
}

export function ScientificDataInterfaces() {
  const [catalog, setCatalog] = useState<ScientificDataInterfaceCatalog | null>(
    null
  );
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchScientificDataInterfaces();
      setCatalog(next);
      setDrafts({});
    } catch (err) {
      setCatalog(null);
      setError(err instanceof Error ? err.message : "无法读取接口设置。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (Object.keys(drafts).length === 0) {
      setError("没有需要保存的修改。");
      return;
    }
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const result = await saveScientificDataInterfaces(drafts);
      setCatalog({ status: result.status, groups: result.groups, items: result.items });
      setDrafts({});
      setNote(result.note || "已保存。环境变量优先于本机配置文件。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        正在读取接口设置…
      </div>
    );
  }

  if (!catalog) {
    return (
      <div className="py-4">
        <p role="alert" className="text-sm text-destructive">
          {error || "无法读取接口设置。"}
        </p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void load()}>
          重试
        </Button>
      </div>
    );
  }

  const dirtyCount = Object.keys(drafts).length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="flex items-start gap-2 pb-2">
          <KeyRound className="mt-0.5 size-4 text-[var(--brand)]" />
          <p className="text-xs leading-5 text-muted-foreground">
            这些接口由后端进程调用。密钥只保存在本机配置文件（0600），
            前端只能看到掩码；环境变量始终优先。
          </p>
        </div>
        {catalog.groups.map((group) => {
          const items = catalog.items.filter((item) => item.group === group.id);
          if (items.length === 0) return null;
          return (
            <section key={group.id} className="border-t border-border/60 py-3">
              <h3 className="text-sm font-semibold">{group.label}</h3>
              <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                {group.description}
              </p>
              <div className="mt-1 divide-y divide-border/40">
                {items.map((item) => (
                  <Field
                    key={item.field}
                    item={item}
                    value={drafts[item.field] ?? item.value}
                    onChange={(next) =>
                      setDrafts((current) => ({ ...current, [item.field]: next }))
                    }
                    onClear={() =>
                      setDrafts((current) => ({ ...current, [item.field]: "" }))
                    }
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>
      <div className="shrink-0 border-t border-border/60 pt-3">
        {error && (
          <p role="alert" className="mb-2 text-xs text-destructive">
            {error}
          </p>
        )}
        {note && (
          <p className="mb-2 text-xs text-[var(--color-success)]">{note}</p>
        )}
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={saving || dirtyCount === 0} onClick={() => void save()}>
            {saving && <Loader2 className="mr-1 size-3.5 animate-spin" />}
            保存修改
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={saving || dirtyCount === 0}
            onClick={() => {
              setDrafts({});
              setError(null);
              setNote(null);
            }}
          >
            放弃
          </Button>
          <span
            className={cn(
              "text-[11px] text-muted-foreground",
              dirtyCount === 0 && "opacity-60"
            )}
          >
            {dirtyCount === 0 ? "暂无改动" : `${dirtyCount} 项待保存`}
          </span>
        </div>
      </div>
    </div>
  );
}
