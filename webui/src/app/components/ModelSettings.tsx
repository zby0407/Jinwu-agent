"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  RefreshCw,
  Search,
  Server,
} from "lucide-react";
import {
  siAnthropic,
  siDeepseek,
  siGooglegemini,
  siMinimax,
  siMoonshotai,
  siNvidia,
  siOllama,
  siOpenrouter,
  siQwen,
} from "simple-icons";
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

function providerKind(spec: ProviderSpec): "local" | "custom" | "cloud" {
  if (spec.id === "ollama") return "local";
  if (spec.id.startsWith("custom-")) return "custom";
  return "cloud";
}

function providerDescription(spec: ProviderSpec): string {
  if (spec.id === "ollama") {
    return "连接本机或局域网内的 Ollama 服务，无需云端 API Key；保留本地模型接口与 Base URL 配置。";
  }
  if (spec.id === "custom-openai") {
    return "接入任意 OpenAI 兼容服务，适合自建网关、私有部署或第三方聚合接口。";
  }
  if (spec.id === "custom-anthropic") {
    return "接入 Anthropic 兼容服务，适合企业网关或私有代理。";
  }
  const descriptions: Record<string, string> = {
    dashscope: "阿里云百炼提供的 Qwen 系列模型，也是当前桌面部件的默认推荐。",
    openai: "OpenAI 的 GPT 系列模型。",
    anthropic: "Anthropic 的 Claude 系列模型。",
    "google-genai": "Google Gemini 系列模型。",
    deepseek: "DeepSeek 官方推理与对话模型。",
    siliconflow: "SiliconFlow 聚合的多品牌开源与商业模型。",
    openrouter: "OpenRouter 聚合的多提供方模型接口。",
    minimax: "MiniMax 系列模型。",
    moonshot: "Moonshot Kimi 系列模型。",
    nvidia: "NVIDIA NIM 托管的企业推理模型。",
  };
  return descriptions[spec.id] ?? "配置该提供商的模型、凭证与连接地址。";
}

type BrandIcon = { title: string; hex: string; path: string };

const OPENAI_ICON: BrandIcon = {
  title: "OpenAI",
  hex: "000000",
  path: "M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z",
};

const SILICONFLOW_ICON: BrandIcon = {
  title: "SiliconFlow",
  hex: "6E29F5",
  path: "M22.959 6.262H12.523c-.577 0-1.043.467-1.043 1.042v3.13c0 .578-.467 1.044-1.043 1.044H1.043C.466 11.478 0 11.945 0 12.52v4.175c0 .577.467 1.042 1.043 1.042h10.436c.577 0 1.042-.467 1.042-1.042v-3.13c0-.578.468-1.044 1.043-1.044h9.393c.577 0 1.043-.467 1.043-1.043V7.304c0-.577-.467-1.042-1.043-1.042z",
};

const PROVIDER_ICONS: Record<string, BrandIcon> = {
  dashscope: siQwen,
  openai: OPENAI_ICON,
  anthropic: siAnthropic,
  "google-genai": siGooglegemini,
  deepseek: siDeepseek,
  siliconflow: SILICONFLOW_ICON,
  openrouter: siOpenrouter,
  minimax: siMinimax,
  moonshot: siMoonshotai,
  nvidia: siNvidia,
  "custom-openai": OPENAI_ICON,
  "custom-anthropic": siAnthropic,
  ollama: siOllama,
};

function providerInitials(spec: ProviderSpec): string {
  return spec.label.slice(0, 2);
}

function ProviderAvatar({ spec, large = false }: { spec: ProviderSpec; large?: boolean }) {
  const icon = PROVIDER_ICONS[spec.id];
  if (!icon) {
    const kind = providerKind(spec);
    return (
      <span
        aria-hidden="true"
        className={cn(
          "flex shrink-0 items-center justify-center rounded-xl border font-semibold",
          large ? "size-11 text-sm" : "size-8 text-[11px]",
          kind === "local" && "border-emerald-200 bg-emerald-50 text-emerald-700",
          kind === "custom" && "border-violet-200 bg-violet-50 text-violet-700",
          kind === "cloud" && "border-sky-200 bg-sky-50 text-sky-700"
        )}
      >
        {providerInitials(spec)}
      </span>
    );
  }
  const color = `#${icon.hex}`;
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex shrink-0 items-center justify-center rounded-xl border",
        large ? "size-11" : "size-8"
      )}
      style={{
        color,
        backgroundColor: `${color}10`,
        borderColor: `${color}30`,
      }}
    >
      <svg
        viewBox="0 0 24 24"
        className={large ? "size-5" : "size-4"}
        focusable="false"
        aria-hidden="true"
      >
        <path fill="currentColor" d={icon.path} />
      </svg>
    </span>
  );
}

function statusDot(spec: ProviderSpec, active: boolean, credential: boolean): string {
  if (active) return "bg-sky-500";
  if (spec.id === "ollama") return "bg-emerald-500";
  if (credential) return "bg-emerald-500";
  return "bg-slate-300";
}

function providerMeta(spec: ProviderSpec, active: boolean, credential: boolean): string {
  if (active) return "当前默认";
  if (spec.id === "ollama") return "本地接口";
  if (credential) return "密钥已配置";
  return "未配置";
}

/**
 * 模型与 API 设置（桌面版）。
 *
 * 通过 `/api/model-config` 走主进程的设置桥接：密钥只提交给本机 Electron，
 * 不写入浏览器存储，也不会回显到前端。保存后桌面壳会重启后端并保留当前会话。
 */
export function ModelSettings({ embedded = false }: { embedded?: boolean }) {
  const [status, setStatus] = useState<ModelConfigStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [onlineModels, setOnlineModels] = useState<string[]>([]);
  const [modelsProvider, setModelsProvider] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [providerSearch, setProviderSearch] = useState("");
  const [modelOptionsOpen, setModelOptionsOpen] = useState(false);
  const [activeModelIndex, setActiveModelIndex] = useState(0);
  const modelPickerRef = useRef<HTMLDivElement>(null);
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
      const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (!response.ok) {
        throw new Error(typeof data.error === "string" ? data.error : `HTTP ${response.status}`);
      }
      const next: ModelConfigStatus = {
        available: Boolean(data.available),
        configured: Boolean(data.configured),
        provider: typeof data.provider === "string" ? data.provider : null,
        model: typeof data.model === "string" ? data.model : null,
        schema: normalizeSpecs(data.schema),
        hasCredential: Boolean(data.hasCredential),
        credentialProviders: Array.isArray(data.credentialProviders)
          ? data.credentialProviders.filter((item): item is string => typeof item === "string")
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
      setLoadError(error instanceof Error ? error.message : "读取模型配置失败。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const specs = useMemo(() => status?.schema ?? [], [status]);
  const currentSpec = specs.find((spec) => spec.id === provider) ?? null;
  const isLocalProvider = currentSpec?.id === "ollama";
  const showBaseUrl = Boolean(currentSpec?.requiresBaseUrl || isLocalProvider);
  const knownCredential = Boolean(
    currentSpec && (status?.credentialProviders ?? []).includes(currentSpec.id)
  );

  const filteredSpecs = useMemo(() => {
    const query = providerSearch.trim().toLowerCase();
    if (!query) return specs;
    return specs.filter((spec) =>
      `${spec.label} ${spec.id}`.toLowerCase().includes(query)
    );
  }, [providerSearch, specs]);

  const providerGroups = useMemo(() => {
    const groups = [
      { id: "cloud", title: "云端模型服务", specs: [] as ProviderSpec[] },
      { id: "custom", title: "自定义接口", specs: [] as ProviderSpec[] },
      { id: "local", title: "本地模型", specs: [] as ProviderSpec[] },
    ];
    for (const spec of filteredSpecs) {
      groups.find((group) => group.id === providerKind(spec))?.specs.push(spec);
    }
    return groups.filter((group) => group.specs.length > 0);
  }, [filteredSpecs]);

  const suggestions = useMemo(() => {
    const names = new Set<string>();
    for (const entry of registry.entries) {
      if (provider && entry.provider !== provider) continue;
      if (entry.name) names.add(entry.name);
    }
    if (currentSpec?.defaultModel) names.add(currentSpec.defaultModel);
    if (modelsProvider === provider) {
      for (const name of onlineModels) names.add(name);
    }
    return [...names].slice(0, 200);
  }, [registry.entries, provider, currentSpec?.defaultModel, modelsProvider, onlineModels]);

  const visibleModelSuggestions = useMemo(() => {
    const query = model.trim().toLowerCase();
    const exactSelection = query.length > 0 && suggestions.some((name) => name.toLowerCase() === query);
    const filtered = !query || exactSelection
      ? suggestions
      : suggestions.filter((name) => name.toLowerCase().includes(query));
    return filtered.slice(0, 100);
  }, [model, suggestions]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelOptionsOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, []);

  const chooseModel = useCallback((name: string) => {
    setModel(name);
    setModelOptionsOpen(false);
    setActiveModelIndex(0);
    setFeedback(null);
  }, []);

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
      const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
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
      setModelsError(error instanceof Error ? error.message : "获取模型列表失败。");
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
    setOnlineModels([]);
    setModelsProvider(null);
    setModelsError(null);
    setModelsLoading(false);
    const spec = specs.find((item) => item.id === nextProvider);
    setModel(spec?.defaultModel ?? "");
    setModelOptionsOpen(false);
    setActiveModelIndex(0);
    setApiKey("");
    setBaseUrl("");
    setShowApiKey(false);
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
    if (currentSpec?.requiresBaseUrl && !baseUrl.trim()) {
      setFeedback({ tone: "error", text: "请填写 Base URL。" });
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
      const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
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
      <div className="flex min-h-full items-center justify-center bg-slate-50 text-sm text-slate-500">
        <Loader2 className="mr-2 size-4 animate-spin" />
        正在读取模型配置…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-3 bg-slate-50 text-center">
        <AlertCircle className="size-5 text-red-600" />
        <p className="text-sm text-red-600">{loadError}</p>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw className="mr-2 size-3.5" />
          重试
        </Button>
      </div>
    );
  }

  if (!status?.available) {
    const notice = (
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
        <AlertCircle className="mt-0.5 size-4 shrink-0" />
        <span>
          当前 WebUI 未连接桌面设置服务，因此不能修改模型与 API。
          在桌面版中打开本页后，可配置云端提供商、自定义接口和本地 Ollama 模型。
        </span>
      </div>
    );
    if (embedded) return notice;
    return (
      <div className="min-h-full bg-slate-50 px-6 py-8 text-slate-900">
        <div className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
          <p className="text-xs font-semibold tracking-[0.16em] text-sky-700">MODEL PROVIDERS</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">模型管理</h2>
          <div className="mt-6">{notice}</div>
        </div>
      </div>
    );
  }

  return (
    <div className={embedded ? "text-slate-900" : "min-h-full bg-slate-50 text-slate-900"}>
      <div className={embedded ? "" : "mx-auto max-w-7xl px-4 py-5 sm:px-7 sm:py-7"}>
        {!embedded && <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold tracking-[0.16em] text-sky-700">MODEL PROVIDERS</p>
            <h2 className="mt-1.5 text-2xl font-semibold tracking-tight">模型管理</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              管理云端提供商、自定义兼容接口和本地模型。密钥只写入本机受保护配置，不会保存在浏览器中。
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={saving}>
            <RefreshCw className="mr-2 size-3.5" />
            重新读取
          </Button>
        </header>}

        <div className="grid overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm lg:grid-cols-[290px_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-slate-50/80 lg:border-b-0 lg:border-r">
            <div className="border-b border-slate-200 p-3">
              <label className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <input
                  value={providerSearch}
                  onChange={(event) => setProviderSearch(event.target.value)}
                  placeholder="搜索提供商…"
                  className="h-10 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100"
                />
              </label>
            </div>
            <div className="max-h-[680px] overflow-y-auto p-2.5">
              {providerGroups.length === 0 ? (
                <p className="px-2 py-8 text-center text-sm text-slate-500">没有匹配的提供商</p>
              ) : (
                providerGroups.map((group) => (
                  <section key={group.id} className="mb-4 last:mb-0">
                    <h3 className="px-2 pb-1.5 text-[11px] font-semibold tracking-wide text-slate-500">{group.title}</h3>
                    <div className="grid gap-1">
                      {group.specs.map((spec) => {
                        const active = status.provider === spec.id;
                        const credential = status.credentialProviders.includes(spec.id);
                        const selected = provider === spec.id;
                        return (
                          <button
                            key={spec.id}
                            type="button"
                            onClick={() => onProviderChange(spec.id)}
                            aria-current={selected ? "true" : undefined}
                            className={cn(
                              "flex w-full items-center gap-2.5 rounded-xl border px-2.5 py-2.5 text-left transition",
                              selected
                                ? "border-sky-300 bg-sky-50 shadow-sm"
                                : "border-transparent hover:border-slate-200 hover:bg-white"
                            )}
                          >
                            <ProviderAvatar spec={spec} />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-slate-800">{spec.label}</span>
                              <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                                <span className={cn("size-1.5 rounded-full", statusDot(spec, active, credential))} />
                                {providerMeta(spec, active, credential)}
                              </span>
                            </span>
                            <ChevronRight className={cn("size-4", selected ? "text-sky-600" : "text-slate-300")} />
                          </button>
                        );
                      })}
                    </div>
                  </section>
                ))
              )}
            </div>
            <p className="border-t border-slate-200 px-4 py-3 text-xs leading-5 text-slate-500">
              自定义 OpenAI / Anthropic 兼容接口和本地 Ollama 已保留在列表中。
            </p>
          </aside>

          <section className="min-w-0 bg-white">
            {currentSpec ? (
              <>
                <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 px-5 py-5 sm:px-7">
                  <div className="flex min-w-0 items-start gap-3">
                    <ProviderAvatar spec={currentSpec} large />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-xl font-semibold tracking-tight">{currentSpec.label}</h3>
                        {status.provider === currentSpec.id && (
                          <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-semibold text-sky-800">当前默认</span>
                        )}
                        {isLocalProvider && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">本地模型</span>
                        )}
                        {!isLocalProvider && (
                          <span className={cn(
                            "rounded-full px-2 py-0.5 text-xs font-semibold",
                            knownCredential ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"
                          )}>
                            {knownCredential ? "密钥已配置" : "待配置"}
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-600">{providerDescription(currentSpec)}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-600">
                    {isLocalProvider ? <Server className="size-3.5" /> : <Cloud className="size-3.5" />}
                    {isLocalProvider ? "本地接口" : currentSpec.requiresApiKey ? "需要 API Key" : "无需 API Key"}
                  </div>
                </div>

                <div className="grid max-w-3xl gap-5 px-5 py-6 sm:px-7">
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
                        <RefreshCw className={cn("mr-1.5 size-3", modelsLoading && "animate-spin")} />
                        {modelsLoading ? "获取中…" : isLocalProvider ? "检测本地模型" : "获取在线模型"}
                      </Button>
                    </div>
                    <div ref={modelPickerRef} className="relative">
                      <Input
                        id="model-name"
                        name="model"
                        role="combobox"
                        aria-autocomplete="list"
                        aria-expanded={modelOptionsOpen}
                        aria-controls="model-name-options"
                        autoComplete="off"
                        spellCheck={false}
                        placeholder={currentSpec.defaultModel || "例如 qwen3:8b"}
                        value={model}
                        className="pr-9"
                        onFocus={() => setModelOptionsOpen(true)}
                        onChange={(event) => {
                          setModel(event.target.value);
                          setModelOptionsOpen(true);
                          setActiveModelIndex(0);
                          if (feedback) setFeedback(null);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "ArrowDown") {
                            event.preventDefault();
                            setModelOptionsOpen(true);
                            setActiveModelIndex((index) =>
                              Math.min(index + 1, Math.max(visibleModelSuggestions.length - 1, 0))
                            );
                          } else if (event.key === "ArrowUp") {
                            event.preventDefault();
                            setModelOptionsOpen(true);
                            setActiveModelIndex((index) => Math.max(index - 1, 0));
                          } else if (event.key === "Enter" && modelOptionsOpen && visibleModelSuggestions[activeModelIndex]) {
                            event.preventDefault();
                            chooseModel(visibleModelSuggestions[activeModelIndex]);
                          } else if (event.key === "Escape") {
                            setModelOptionsOpen(false);
                          }
                        }}
                      />
                      <button
                        type="button"
                        aria-label="展开模型建议"
                        aria-expanded={modelOptionsOpen}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => setModelOptionsOpen((open) => !open)}
                        className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                      >
                        <ChevronDown className={cn("size-4 transition-transform", modelOptionsOpen && "rotate-180")} />
                      </button>
                      {modelOptionsOpen && (
                        <div
                          id="model-name-options"
                          role="listbox"
                          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-white p-1 shadow-xl"
                        >
                          {visibleModelSuggestions.length > 0 ? (
                            visibleModelSuggestions.map((name, index) => {
                              const selected = name === model.trim();
                              return (
                                <button
                                  key={name}
                                  type="button"
                                  role="option"
                                  aria-selected={selected}
                                  onMouseDown={(event) => event.preventDefault()}
                                  onMouseEnter={() => setActiveModelIndex(index)}
                                  onClick={() => chooseModel(name)}
                                  className={cn(
                                    "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition",
                                    index === activeModelIndex && "bg-slate-100",
                                    selected ? "font-medium text-sky-700" : "text-slate-700"
                                  )}
                                >
                                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{name}</span>
                                  {selected && <Check className="size-4 shrink-0" />}
                                </button>
                              );
                            })
                          ) : (
                            <div className="px-3 py-6 text-center text-sm text-slate-500">
                              没有匹配的模型建议；仍可手动输入模型 ID。
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <p className="text-xs leading-5 text-slate-500">
                      {modelsLoading
                        ? `正在从 ${currentSpec.label} 获取模型列表…`
                        : modelsProvider === provider && onlineModels.length > 0
                          ? `已获取 ${onlineModels.length} 个可用模型，也可直接输入任意受支持的模型 ID。`
                          : modelsError
                            ? modelsError
                            : "可填写提供商支持的任意模型 ID。"}
                    </p>
                  </div>

                  {currentSpec.requiresApiKey && (
                    <div className="grid gap-2">
                      <Label htmlFor="model-api-key">
                        API Key
                        {knownCredential && !apiKey ? (
                          <span className="ml-2 text-xs font-normal text-slate-500">已保存</span>
                        ) : null}
                      </Label>
                      <div className="relative">
                        <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                        <Input
                          id="model-api-key"
                          name="api-key"
                          type={showApiKey ? "text" : "password"}
                          autoComplete="new-password"
                          spellCheck={false}
                          className="pl-9 pr-10"
                          placeholder={
                            knownCredential
                              ? "留空则沿用已保存的密钥"
                              : currentSpec.placeholder ?? "sk-..."
                          }
                          value={apiKey}
                          onChange={(event) => {
                            setApiKey(event.target.value);
                            if (feedback) setFeedback(null);
                          }}
                        />
                        <button
                          type="button"
                          aria-label={showApiKey ? "隐藏 API Key" : "显示 API Key"}
                          onClick={() => setShowApiKey((value) => !value)}
                          className="absolute right-2 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-slate-500 hover:bg-slate-100"
                        >
                          {showApiKey ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                        </button>
                      </div>
                      <p className="text-xs leading-5 text-slate-500">
                        密钥只提交给本机桌面进程并写入受保护的配置文件，不会保存在浏览器中。
                      </p>
                    </div>
                  )}

                  {showBaseUrl && (
                    <div className="grid gap-2">
                      <Label htmlFor="model-base-url">Base URL{isLocalProvider ? "（本地接口）" : ""}</Label>
                      <Input
                        id="model-base-url"
                        name="base-url"
                        type="url"
                        autoComplete="off"
                        spellCheck={false}
                        placeholder={isLocalProvider ? "http://127.0.0.1:11434" : "https://api.example.com/v1"}
                        value={baseUrl}
                        onChange={(event) => {
                          setBaseUrl(event.target.value);
                          if (feedback) setFeedback(null);
                        }}
                      />
                      <p className="text-xs leading-5 text-slate-500">
                        {isLocalProvider
                          ? "留空则沿用已保存地址；未保存时使用默认本机 Ollama 地址 http://127.0.0.1:11434，也可填写局域网或自定义本地推理服务地址。"
                          : "自定义兼容接口必须填写 Base URL。"}
                      </p>
                    </div>
                  )}

                  <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-600">
                    <Cpu className="mt-0.5 size-4 shrink-0 text-sky-700" />
                    <div>
                      保存后会把该提供商设为本机金乌的默认模型，并重启后端应用配置。
                      {status.configPath ? <span className="mt-1 block break-all">配置文件：{status.configPath}</span> : null}
                    </div>
                  </div>

                  {feedback && (
                    <div
                      role="status"
                      aria-live="polite"
                      className={cn(
                        "flex items-start gap-2 rounded-xl border px-4 py-3 text-sm leading-6",
                        feedback.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-700",
                        feedback.tone === "error" && "border-red-200 bg-red-50 text-red-700",
                        feedback.tone === "info" && "border-slate-200 bg-slate-50 text-slate-600"
                      )}
                    >
                      {feedback.tone === "success" ? (
                        <CheckCircle2 className="mt-1 size-4 shrink-0" />
                      ) : (
                        <AlertCircle className="mt-1 size-4 shrink-0" />
                      )}
                      <span>{feedback.text}</span>
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-slate-50/70 px-5 py-4 sm:px-7">
                  <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
                    {saving ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : null}
                    保存并应用
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void load()} disabled={saving}>
                    <RefreshCw className="mr-2 size-3.5" />
                    重新读取
                  </Button>
                </div>
              </>
            ) : (
              <div className="flex min-h-[420px] items-center justify-center text-sm text-slate-500">
                请选择左侧的模型提供商。
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
