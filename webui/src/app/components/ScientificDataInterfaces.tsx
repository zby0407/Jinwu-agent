"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
    <div className="research-config-field">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={`sdi-${item.field}`} className="text-sm font-medium">
          {item.label}
        </Label>
        <SourceBadge item={item} />
        {item.env_override && (
          <span className="text-xs text-muted-foreground">
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
      <p className="text-xs leading-5 text-muted-foreground">{item.hint}</p>
      <p className="font-mono text-[11px] text-muted-foreground">
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
  const [activeGroup, setActiveGroup] = useState<string | null>(null);

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
  const groups = catalog.groups.map(group => ({ ...group, items: catalog.items.filter(item => item.group === group.id) })).filter(group => group.items.length > 0);
  const selected = groups.find(group => group.id === activeGroup);

  return <div className="research-data-catalog">
    {selected ? <>
      <button type="button" className="research-text-action mb-4" onClick={() => setActiveGroup(null)}><ArrowLeft className="size-4" />返回接口目录</button>
      <div className="research-config-panel"><div className="research-config-heading"><div><h4>{selected.label}配置</h4><p>{selected.items.filter(item => item.configured).length} / {selected.items.length} 项已配置 · 配置状态不代表连接状态</p></div><button type="button" className="research-text-action" disabled={saving || loading} onClick={() => void load()}><RefreshCw className="size-4" />重新加载</button></div>
      <div className="research-config-fields">{selected.items.map(item => <Field key={item.field} item={item} value={drafts[item.field] ?? item.value} onChange={next => setDrafts(current => ({ ...current, [item.field]: next }))} onClear={() => setDrafts(current => ({ ...current, [item.field]: "" }))} />)}</div>
      {dirtyCount > 0 && <div className="research-config-save"><span>{dirtyCount} 项修改未保存</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={saving} onClick={() => { setDrafts({}); setError(null); setNote(null); }}>放弃</Button><Button size="sm" disabled={saving} onClick={() => void save()}>{saving && <Loader2 className="mr-1 size-3.5 animate-spin" />}保存修改</Button></div></div>}</div>
    </> : <div className="research-catalog-list">{groups.map(group => <button key={group.id} type="button" className="research-catalog-row" onClick={() => setActiveGroup(group.id)}><span className="research-catalog-marker" aria-hidden="true" /><span className="min-w-0 flex-1"><strong>{group.label}</strong><span className="research-catalog-desc">{group.description}</span><span className="research-catalog-meta">{group.items.length} 个配置项 · {group.items.filter(item => item.configured).length} 项已配置</span></span><ArrowRight className="size-4 shrink-0 text-slate-400" /></button>)}</div>}
    {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
    {note && <p role="status" className="mt-4 text-sm text-[var(--color-success)]">{note}</p>}
  </div>;
}
