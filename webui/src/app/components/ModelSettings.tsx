"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Cpu,
  KeyRound,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAvailableModels } from "@/app/hooks/useAvailableModels";
import { cn } from "@/lib/utils";

interface ProviderSpec {
  id: string;
  label: string;
  defaultModel: string;
  requiresApiKey: boolean;
  requiresBaseUrl: boolean;
  placeholder?: string;
}

interface ModelConfigStatus {
  available: boolean;
  configured: boolean;
  provider: string | null;
  model: string | null;
  schema: ProviderSpec[];
  hasCredential: boolean;
  credentialProviders: string[];
  configPath: string | null;
}

type Feedback = { tone: "success" | "error" | "info"; text: string };

function normalizeSpecs(raw: unknown): ProviderSpec[] {
  if (!Array.isArray(raw)) return [];
  const specs: ProviderSpec[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const spec = item as Record<string, unknown>;
    if (typeof spec.id !== "string" || !spec.id) continue;
    specs.push({
      id: spec.id,
      label: typeof spec.label === "string" ? spec.label : spec.id,
      defaultModel:
        typeof spec.defaultModel === "string" ? spec.defaultModel : "",
      requiresApiKey: Boolean(spec.requiresApiKey),
      requiresBaseUrl: Boolean(spec.requiresBaseUrl),
      placeholder:
        typeof spec.placeholder === "string" ? spec.placeholder : undefined,
    });
  }
  return specs;
}

/**
 * 模型与 API 设置（桌面版）。
 *
 * 通过 `/api/model-config` 走主进程的设置桥接：密钥只提交给本机 Electron，
 * 不写入浏览器存储，也不会回显到前端。保存后桌面壳会重启后端并保留当前会话。
 */
export function ModelSettings() {
  const [status, setStatus] = useState<ModelConfigStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [onlineModels, setOnlineModels] = useState<string[]>([]);
  const [modelsProvider, setModelsProvider] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelSearch, setModelSearch] = useState("");
  const credentialsRef = useRef({ apiKey: "", baseUrl: "" });
  credentialsRef.current = { apiKey, baseUrl };
  const providerRef = useRef(provider);
  providerRef.current = provider;
  const { registry } = useAvailableModels();

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await fetch("/api/model-config", { cache: "no-store" });
      const data = (await response.json().catch(() => ({}))) as Record<
        string,
        unknown
      >;
      if (!response.ok) {
        throw new Error(
          typeof data.error === "string" ? data.error : `HTTP ${response.status}`
        );
      }
      const next: ModelConfigStatus = {
        available: Boolean(data.available),
        configured: Boolean(data.configured),
        provider: typeof data.provider === "string" ? data.provider : null,
        model: typeof data.model === "string" ? data.model : null,
        schema: normalizeSpecs(data.schema),
        hasCredential: Boolean(data.hasCredential),
        credentialProviders: Array.isArray(data.credentialProviders)
          ? data.credentialProviders.filter(
              (item): item is string => typeof item === "string"
            )
          : [],
        configPath: typeof data.configPath === "string" ? data.configPath : null,
      };
      setStatus(next);
      setProvider(next.provider ?? next.schema[0]?.id ?? "");
      setModel(next.model ?? "");
      setApiKey("");
      setBaseUrl("");
    } catch (error) {
      setStatus(null);
      setLoadError(
        error instanceof Error ? error.message : "读取模型配置失败。"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const specs = status?.schema ?? [];
  const currentSpec = specs.find((spec) => spec.id === provider) ?? null;
  const showBaseUrl = Boolean(currentSpec?.requiresBaseUrl);
  const knownCredential = Boolean(
    currentSpec && (status?.credentialProviders ?? []).includes(currentSpec.id)
  );

  const suggestions = useMemo(() => {
    const names = new Set<string>();
    for (const entry of registry.entries) {
      if (provider && entry.provider !== provider) continue;
      if (entry.name) names.add(entry.name);
    }
    if (currentSpec?.defaultModel) names.add(currentSpec.defaultModel);
    return [...names];
  }, [registry.entries, provider, currentSpec?.defaultModel]);

  const onlineReady = modelsProvider === provider && onlineModels.length > 0;
  const visibleModels = useMemo(() => {
    const source = onlineReady ? onlineModels : suggestions;
    const query = modelSearch.trim().toLowerCase();
    const names = query
      ? source.filter((name) => name.toLowerCase().includes(query))
      : source;
    return names.slice(0, 100);
  }, [onlineReady, onlineModels, suggestions, modelSearch]);

  const loadOnlineModels = useCallback(async (targetProvider: string) => {
    if (!targetProvider) return;
    const { apiKey: typedKey, baseUrl: typedBase } = credentialsRef.current;
    setModelsLoading(true);
    setModelsError(null);
    try {
      const payload: Record<string, string> = { provider: targetProvider };
      if (typedKey.trim()) payload.api_key = typedKey.trim();
      if (typedBase.trim()) payload.base_url = typedBase.trim();
      const response = await fetch("/api/model-config/models", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json().catch(() => ({}))) as Record<
        string,
        unknown
      >;
      if (!response.ok || data.ok === false) {
        throw new Error(
          typeof data.error === "string"
            ? data.error
            : `获取模型列表失败（HTTP ${response.status}）`
        );
      }
      const list = Array.isArray(data.models)
        ? data.models.filter((item): item is string => typeof item === "string")
        : [];
      if (!list.length) throw new Error("接口没有返回可用的对话模型。");
      if (providerRef.current !== targetProvider) return;
      setOnlineModels(list);
      setModelsProvider(targetProvider);
    } catch (error) {
      if (providerRef.current !== targetProvider) return;
      setOnlineModels([]);
      setModelsProvider(null);
      setModelsError(
        error instanceof Error ? error.message : "获取模型列表失败。"
      );
    } finally {
      if (providerRef.current === targetProvider) setModelsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!status || !provider) return;
    const spec = status.schema.find((item) => item.id === provider);
    if (!spec) return;
    const known = status.credentialProviders.includes(provider);
    if (spec.requiresApiKey && !known) return;
    void loadOnlineModels(provider);
  }, [status, provider, loadOnlineModels]);

  const onProviderChange = (nextProvider: string) => {
    setProvider(nextProvider);
    setFeedback(null);
    setModelOpen(false);
    setModelSearch("");
    setOnlineModels([]);
    setModelsProvider(null);
    setModelsError(null);
    setModelsLoading(false);
    const spec = specs.find((item) => item.id === nextProvider);
    setModel(spec?.defaultModel ?? "");
    setApiKey("");
    setBaseUrl("");
  };

  const handleSave = async () => {
    if (!provider) {
      setFeedback({ tone: "error", text: "请选择模型提供商。" });
      return;
    }
    if (!model.trim()) {
      setFeedback({ tone: "error", text: "请填写模型名称。" });
      return;
    }
    if (currentSpec?.requiresApiKey && !apiKey.trim() && !knownCredential) {
      setFeedback({ tone: "error", text: "请填写 API 密钥。" });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const payload: Record<string, string> = {
        provider,
        model: model.trim(),
      };
      if (apiKey.trim()) payload.api_key = apiKey.trim();
      if (showBaseUrl && baseUrl.trim()) payload.base_url = baseUrl.trim();
      const response = await fetch("/api/model-config", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json().catch(() => ({}))) as Record<
        string,
        unknown
      >;
      if (!response.ok || data.ok === false) {
        throw new Error(
          typeof data.error === "string" ? data.error : `保存失败（HTTP ${response.status}）`
        );
      }
      setApiKey("");
      setFeedback({
        tone: "success",
        text:
          data.pending === true
            ? "已保存。服务空闲时会自动应用新配置。"
            : "已保存，正在重启后端以应用新配置；当前界面与草稿会保留。",
      });
      setStatus((prev) =>
        prev
          ? {
              ...prev,
              configured: true,
              provider,
              model: model.trim(),
              hasCredential:
                prev.hasCredential || Boolean(apiKey.trim()) || !currentSpec?.requiresApiKey,
              credentialProviders: apiKey.trim()
                ? [...new Set([...prev.credentialProviders, provider])]
                : prev.credentialProviders,
            }
          : prev
      );
    } catch (error) {
      setFeedback({
        tone: "error",
        text: error instanceof Error ? error.message : "保存失败。",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center py-10 text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        正在读取模型配置…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-10 text-center">
        <AlertCircle className="size-5 text-destructive" />
        <p className="text-sm text-destructive">{loadError}</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load()}
        >
          <RefreshCw className="mr-2 size-3.5" />
          重试
        </Button>
      </div>
    );
  }

  const credentialReady = knownCredential;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto py-1">
      <div className="grid gap-5">
        <div className="flex items-start gap-3 rounded-md border border-border/70 bg-muted/30 px-3 py-2.5">
          <Cpu className="mt-0.5 size-4 shrink-0 text-[var(--brand)]" />
          <div className="min-w-0 text-xs leading-relaxed text-muted-foreground">
            这里修改的是本机金乌的默认模型与 API 凭证，保存后会自动重启后端。
            {status?.configPath ? (
              <span className="mt-1 block break-all">
                配置文件：{status.configPath}
              </span>
            ) : null}
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="model-provider">模型提供商</Label>
          <select
            id="model-provider"
            value={provider}
            onChange={(event) => onProviderChange(event.target.value)}
            className={cn(
              "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none",
              "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            )}
          >
            {specs.length === 0 && <option value="">（无可用提供商）</option>}
            {specs.map((spec) => (
              <option
                key={spec.id}
                value={spec.id}
              >
                {spec.label}
                {spec.requiresApiKey && !(status?.credentialProviders ?? []).includes(spec.id)
                  ? " · 未配置密钥"
                  : ""}
              </option>
            ))}
          </select>
        </div>

        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="model-name">模型</Label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => void loadOnlineModels(provider)}
              disabled={!provider || modelsLoading || saving}
            >
              <RefreshCw
                className={cn("mr-1.5 size-3", modelsLoading && "animate-spin")}
              />
              {modelsLoading ? "获取中…" : "获取在线模型"}
            </Button>
          </div>
          <div className="relative">
            <Input
              id="model-name"
              name="model"
              autoComplete="off"
              spellCheck={false}
              role="combobox"
              aria-expanded={modelOpen}
              aria-controls="model-options"
              aria-autocomplete="list"
              className="pr-8"
              placeholder={currentSpec?.defaultModel || "例如 deepseek-v4-pro"}
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
                setModelSearch(event.target.value);
                setModelOpen(true);
                if (feedback) setFeedback(null);
              }}
              onFocus={() => {
                setModelOpen(true);
                setModelSearch("");
              }}
              onBlur={() => setModelOpen(false)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setModelOpen(false);
              }}
            />
            <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
            {modelOpen && (
              <div
                id="model-options"
                role="listbox"
                className="absolute top-full z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-border bg-popover p-1 text-sm shadow-md"
              >
                {visibleModels.length === 0 ? (
                  <p className="px-2 py-1.5 text-xs text-muted-foreground">
                    {modelsLoading ? "正在获取在线模型…" : "没有匹配的模型"}
                  </p>
                ) : (
                  visibleModels.map((name) => (
                    <button
                      key={name}
                      type="button"
                      role="option"
                      aria-selected={name === model}
                      className={cn(
                        "block w-full truncate rounded-sm px-2 py-1.5 text-left hover:bg-accent hover:text-accent-foreground",
                        name === model && "bg-accent/60"
                      )}
                      onMouseDown={(event) => {
                        event.preventDefault();
                        setModel(name);
                        setModelSearch("");
                        setModelOpen(false);
                      }}
                    >
                      {name}
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {modelsLoading
              ? `正在从 ${currentSpec?.label ?? provider} 获取官方模型列表…`
              : modelsProvider === provider && onlineModels.length > 0
                ? `已从 ${currentSpec?.label ?? provider} 在线获取 ${onlineModels.length} 个模型，也可直接输入模型 ID。`
                : modelsError
                  ? modelsError
                  : "可填写提供商支持的任意模型 ID，或点击“获取在线模型”拉取官方列表。"}
          </p>
        </div>

        {currentSpec?.requiresApiKey !== false && (
          <div className="grid gap-2">
            <Label htmlFor="model-api-key">
              API 密钥
              {credentialReady && !apiKey ? (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  已保存
                </span>
              ) : null}
            </Label>
            <div className="relative">
              <KeyRound className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="model-api-key"
                name="api-key"
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                className="pl-9"
                placeholder={
                  credentialReady
                    ? "留空则沿用已保存的密钥"
                    : currentSpec?.placeholder ?? "sk-..."
                }
                value={apiKey}
                onChange={(event) => {
                  setApiKey(event.target.value);
                  if (feedback) setFeedback(null);
                }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              密钥只提交给本机桌面进程并写入受保护的配置文件，不会保存在浏览器中。
            </p>
          </div>
        )}

        {showBaseUrl && (
          <div className="grid gap-2">
            <Label htmlFor="model-base-url">API Base URL</Label>
            <Input
              id="model-base-url"
              name="base-url"
              type="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://api.example.com/v1"
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
                if (feedback) setFeedback(null);
              }}
            />
            <p className="text-xs text-muted-foreground">
              留空则沿用已保存的地址。
            </p>
          </div>
        )}

        {feedback && (
          <div
            role="status"
            aria-live="polite"
            className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 text-xs leading-relaxed",
              feedback.tone === "success" &&
                "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
              feedback.tone === "error" &&
                "border-destructive/30 bg-destructive/10 text-destructive",
              feedback.tone === "info" &&
                "border-border/70 bg-muted/40 text-muted-foreground"
            )}
          >
            {feedback.tone === "success" ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
            )}
            <span>{feedback.text}</span>
          </div>
        )}

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : null}
            保存并应用
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void load()}
            disabled={saving}
          >
            <RefreshCw className="mr-2 size-3.5" />
            重新读取
          </Button>
        </div>
      </div>
    </div>
  );
}
