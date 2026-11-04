"""Non-interactive desktop setup helpers.

The Electron shell cannot run the interactive TUI wizard on a fresh install, so
it drives this small JSON interface instead:

``python -m jw.desktop_setup status``
    Report whether the effective configuration has a usable provider credential
    and return the provider schema the setup page renders.

``python -m jw.desktop_setup configure``
    Read a JSON object from stdin (provider, model, api_key, optional base_url),
    validate it, persist ``config.yaml`` with user-only permissions, and return a
    secret-free summary.

API keys are accepted on stdin rather than argv so they do not appear in the
process list or desktop logs.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .config import get_config_path, get_effective_config, load_config, save_config
from .config.onboard.constants import VALID_PROVIDERS

MAX_PAYLOAD_CHARS = 64_000
MAX_API_KEY_CHARS = 8_192
MAX_MODEL_CHARS = 200
MAX_BASE_URL_CHARS = 2_048


class SetupError(ValueError):
    """Invalid setup request from the desktop shell."""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    default_model: str
    api_key_field: str
    requires_api_key: bool = True
    requires_base_url: bool = False
    base_url_field: str = ""
    placeholder: str = ""


# Only the providers most likely to be used by a desktop customer are shown in
# the first-run wizard. The CLI onboarding wizard still supports every entry in
# VALID_PROVIDERS, and ``configure`` accepts those too as long as the required
# credential/base URL is supplied.
_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "dashscope",
        "阿里云百炼 / DashScope（Qwen，默认）",
        "qwen3.7-plus",
        "dashscope_api_key",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "openai",
        "OpenAI",
        "gpt-5.6-terra",
        "openai_api_key",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "anthropic",
        "Anthropic",
        "claude-sonnet-5",
        "anthropic_api_key",
        placeholder="sk-ant-...",
    ),
    ProviderSpec(
        "google-genai",
        "Google Gemini",
        "gemini-3.5-flash",
        "google_api_key",
        placeholder="AIza...",
    ),
    ProviderSpec(
        "deepseek",
        "DeepSeek",
        "deepseek-v4-pro",
        "deepseek_api_key",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "siliconflow",
        "SiliconFlow",
        "qwen3.7-plus",
        "siliconflow_api_key",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "openrouter",
        "OpenRouter",
        "qwen3.7-plus",
        "openrouter_api_key",
        placeholder="sk-or-...",
    ),
    ProviderSpec(
        "minimax",
        "MiniMax",
        "minimax-m3",
        "minimax_api_key",
        placeholder="...",
    ),
    ProviderSpec(
        "moonshot",
        "Moonshot / Kimi",
        "kimi-k2.6",
        "moonshot_api_key",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "nvidia",
        "NVIDIA NIM",
        "nemotron-super",
        "nvidia_api_key",
        placeholder="nvapi-...",
    ),
    ProviderSpec(
        "custom-openai",
        "自定义 OpenAI 兼容接口",
        "qwen3.7-plus",
        "custom_openai_api_key",
        requires_base_url=True,
        base_url_field="custom_openai_base_url",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "custom-anthropic",
        "自定义 Anthropic 兼容接口",
        "claude-sonnet-5",
        "custom_anthropic_api_key",
        requires_base_url=True,
        base_url_field="custom_anthropic_base_url",
        placeholder="sk-...",
    ),
    ProviderSpec(
        "ollama",
        "Ollama（本机）",
        "qwen3:8b",
        "ollama_base_url",
        requires_api_key=False,
        requires_base_url=False,
    ),
)

_SPEC_BY_ID = {spec.id: spec for spec in _PROVIDER_SPECS}
# Credential/base URL fields are written into the disposable test subprocess
# environment so provider SDKs can read them without touching config.yaml.
_TEST_ENV_BY_FIELD = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
    "openai_api_key": "OPENAI_API_KEY",
    "nvidia_api_key": "NVIDIA_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "minimax_api_key": "MINIMAX_API_KEY",
    "minimax_base_url": "MINIMAX_BASE_URL",
    "siliconflow_api_key": "SILICONFLOW_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "zhipu_api_key": "ZHIPU_API_KEY",
    "volcengine_api_key": "VOLCENGINE_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "dashscope_base_url": "DASHSCOPE_BASE_URL",
    "moonshot_api_key": "MOONSHOT_API_KEY",
    "kimi_api_key": "KIMI_API_KEY",
    "custom_openai_api_key": "CUSTOM_OPENAI_API_KEY",
    "custom_openai_base_url": "CUSTOM_OPENAI_BASE_URL",
    "custom_anthropic_api_key": "CUSTOM_ANTHROPIC_API_KEY",
    "custom_anthropic_base_url": "CUSTOM_ANTHROPIC_BASE_URL",
    "ollama_base_url": "OLLAMA_BASE_URL",
}
# Full credential lookup for status checks. The setup wizard only offers the
# common desktop providers, but a configuration created by `jw onboard` for any
# supported provider must still count as configured.
_KEY_FIELD_BY_PROVIDER = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google-genai": "google_api_key",
    "minimax": "minimax_api_key",
    "nvidia": "nvidia_api_key",
    "siliconflow": "siliconflow_api_key",
    "openrouter": "openrouter_api_key",
    "deepseek": "deepseek_api_key",
    "zhipu": "zhipu_api_key",
    "zhipu-code": "zhipu_api_key",
    "volcengine": "volcengine_api_key",
    "dashscope": "dashscope_api_key",
    "dashscope-code": "dashscope_api_key",
    "moonshot": "moonshot_api_key",
    "kimi-coding": "kimi_api_key",
    "custom-openai": "custom_openai_api_key",
    "custom-anthropic": "custom_anthropic_api_key",
}


def provider_schema() -> list[dict[str, Any]]:
    """Return the display/validation schema consumed by the setup page."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "defaultModel": spec.default_model,
            "requiresApiKey": spec.requires_api_key,
            "requiresBaseUrl": spec.requires_base_url,
            "placeholder": spec.placeholder,
        }
        for spec in _PROVIDER_SPECS
    ]


def _effective_key(config: Any, provider: str) -> str:
    field = _KEY_FIELD_BY_PROVIDER.get(provider)
    if field is None:
        return ""
    return str(getattr(config, field, "") or "")


def is_configured(config: Any | None = None) -> bool:
    """Return True when the active provider has a credential or needs none."""
    cfg = config or get_effective_config()
    spec = _SPEC_BY_ID.get(cfg.provider)
    if spec is not None and not spec.requires_api_key:
        return True
    return bool(_effective_key(cfg, cfg.provider))


def configuration_status() -> dict[str, Any]:
    """Secret-free status plus the setup schema."""
    config = get_effective_config()
    configured = is_configured(config)
    return {
        "configured": configured,
        "provider": config.provider,
        "model": config.model,
        "configPath": str(get_config_path()),
        "configExists": get_config_path().is_file(),
        "schema": provider_schema(),
        "hasCredential": bool(_effective_key(config, config.provider)),
        "credentialProviders": [
            p for p in _KEY_FIELD_BY_PROVIDER if _effective_key(config, p)
        ],
        **proxy_status(),
    }


def proxy_status() -> dict[str, Any]:
    from dotenv import dotenv_values, find_dotenv

    env_path = find_dotenv(usecwd=True)
    dot_value = (
        dotenv_values(env_path).get("JW_DASHSCOPE_PROXY_MODE") if env_path else None
    )
    env_value = os.environ.get("JW_DASHSCOPE_PROXY_MODE")
    source = "workspace_env" if dot_value else "environment" if env_value else "config"
    value = dot_value or env_value or load_config().dashscope_proxy_mode
    return {
        "proxyMode": value,
        "proxyModeSource": source,
        "proxyModeOverridden": source != "config",
        "proxyModeSourcePath": env_path if dot_value else None,
    }


def _clean_text(payload: dict[str, Any], name: str, limit: int) -> str:
    value = payload.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SetupError(f"{name} 必须是字符串")
    value = value.strip()
    if len(value) > limit:
        raise SetupError(f"{name} 过长")
    return value


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SetupError("base_url 必须是有效的 http(s) URL")
    return value.rstrip("/")


def config_from_payload(payload: Any):
    """Validate a draft without persisting it or exposing its credentials."""
    if not isinstance(payload, dict):
        raise SetupError("配置必须是 JSON 对象")

    provider = _clean_text(payload, "provider", 80)
    if provider not in VALID_PROVIDERS:
        raise SetupError(f"不支持的 provider: {provider!r}")
    spec = _SPEC_BY_ID.get(provider)
    if spec is None:
        raise SetupError(
            f"{provider!r} 暂不支持桌面配置向导，请使用 `jw onboard` 完成配置"
        )

    model = _clean_text(payload, "model", MAX_MODEL_CHARS) or spec.default_model
    api_key = _clean_text(payload, "api_key", MAX_API_KEY_CHARS)
    base_url = _clean_text(payload, "base_url", MAX_BASE_URL_CHARS)

    config = load_config()
    effective = get_effective_config()
    if not api_key:
        api_key = _effective_key(effective, provider)
    if not base_url and spec.base_url_field:
        base_url = str(getattr(effective, spec.base_url_field, "") or "")
    if spec.requires_api_key and not api_key:
        raise SetupError(f"{spec.label} 需要 API Key")
    if spec.requires_base_url and not base_url:
        raise SetupError(f"{spec.label} 需要 Base URL")
    if base_url:
        base_url = _validate_base_url(base_url)

    config.provider = provider
    config.model = model
    config.ui_backend = "webui"
    if spec.requires_api_key:
        setattr(config, spec.api_key_field, api_key)
    if base_url and spec.base_url_field:
        setattr(config, spec.base_url_field, base_url)
    route = _clean_text(payload, "proxy_mode", 20) or config.dashscope_proxy_mode
    if route not in {"env", "direct"}:
        raise SetupError("连接方式必须为 env 或 direct")
    if provider in {"dashscope", "dashscope-code"}:
        config.dashscope_proxy_mode = route
    return config


def configure_from_payload(payload: Any) -> dict[str, Any]:
    config = config_from_payload(payload)
    save_config(config)

    # The caller only gets non-secret fields; API keys never cross stdout.
    return {
        "configured": is_configured(get_effective_config()),
        "provider": config.provider,
        "model": config.model,
        "configPath": str(get_config_path()),
        **proxy_status(),
    }


async def test_connection(payload: Any) -> dict[str, Any]:
    """One explicit minimal model request; no graph, tools, persistence or retries."""
    import asyncio
    import time

    from .provider_diagnostics import diagnose_provider_error

    config = config_from_payload(payload)
    started = time.monotonic()
    route_info = proxy_status()
    route = (
        route_info["proxyMode"]
        if route_info["proxyModeOverridden"]
        else config.dashscope_proxy_mode
    )
    os.environ["JW_DASHSCOPE_PROXY_MODE"] = route
    # This command runs in a disposable subprocess. Explicit draft credentials
    # override only this test's environment, never the application or a file.
    for field, env_name in _TEST_ENV_BY_FIELD.items():
        value = str(getattr(config, field, "") or "")
        if value:
            os.environ[env_name] = value
    from .llm import get_chat_model

    model = None
    try:
        model = get_chat_model(
            model=config.model, provider=config.provider, max_retries=0
        )
        request_kwargs: dict[str, Any] = {"max_tokens": 16}
        if config.provider in {"dashscope", "dashscope-code"}:
            request_kwargs["extra_body"] = {"enable_thinking": False}
        result = await asyncio.wait_for(
            model.ainvoke("Reply with OK only.", **request_kwargs),
            timeout=max(0.1, 28 - (time.monotonic() - started)),
        )
        return {
            "success": True,
            "route": route,
            "code": "OK",
            "message": "模型请求成功。",
            "elapsedMs": round((time.monotonic() - started) * 1000),
            "receivedResponse": bool(result.content),
        }
    except Exception as exc:
        return {
            "success": False,
            **diagnose_provider_error(exc, route),
            "elapsedMs": round((time.monotonic() - started) * 1000),
        }
    finally:
        if model is not None:
            if getattr(model, "http_async_client", None) is not None:
                await model.http_async_client.aclose()
            if getattr(model, "http_client", None) is not None:
                model.http_client.close()


_MODEL_LIST_TIMEOUT_S = 15.0
_MODEL_LIST_LIMIT = 1_000

# The official APIs advertise every non-chat artifact they host; the settings
# picker only wants models a chat request can target.
_NON_CHAT_MODEL_MARKERS = (
    "embed",
    "whisper",
    "tts",
    "transcribe",
    "speech",
    "realtime",
    "moderation",
    "rerank",
    "dall-e",
    "sora",
    "imagen",
    "image",
    "audio",
)

_OPENAI_COMPAT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "zhipu-code": "https://open.bigmodel.cn/api/coding/paas/v4",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "dashscope-code": "https://coding.dashscope.aliyuncs.com/v1",
}
_ANTHROPIC_COMPAT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "minimax": "https://api.minimaxi.com/anthropic",
    "kimi-coding": "https://api.kimi.com/coding/",
}
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def _redact_error(message: Any, secret: str) -> str:
    text = str(message or "未知错误").strip()
    if secret:
        text = text.replace(secret, "***")
    return text[:400]


def _resolve_model_endpoint(provider: str, config: Any) -> tuple[str, str]:
    """Return ``(kind, base_url)`` for providers whose models can be listed."""
    if provider == "ollama":
        base_url = (config.ollama_base_url or "").strip() or _DEFAULT_OLLAMA_BASE_URL
        return "ollama", base_url
    if provider == "google-genai":
        return "google", ""
    if provider == "custom-openai":
        return "openai", (config.custom_openai_base_url or "").strip()
    if provider == "custom-anthropic":
        return "anthropic", (config.custom_anthropic_base_url or "").strip()
    if provider == "anthropic":
        base_url = (
            (config.anthropic_base_url or "").strip()
            or os.environ.get("ANTHROPIC_BASE_URL", "").strip()
            or _ANTHROPIC_COMPAT_BASE_URLS["anthropic"]
        )
        return "anthropic", base_url
    if provider == "minimax":
        base_url = (
            config.minimax_base_url or ""
        ).strip() or _ANTHROPIC_COMPAT_BASE_URLS["minimax"]
        return "anthropic", base_url
    if provider in _OPENAI_COMPAT_BASE_URLS:
        base_url = _OPENAI_COMPAT_BASE_URLS[provider]
        if provider == "openai":
            base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or base_url
        elif provider == "dashscope":
            base_url = (
                (config.dashscope_base_url or "").strip()
                or os.environ.get("DASHSCOPE_BASE_URL", "").strip()
                or base_url
            )
        return "openai", base_url
    if provider in _ANTHROPIC_COMPAT_BASE_URLS:
        return "anthropic", _ANTHROPIC_COMPAT_BASE_URLS[provider]
    raise SetupError(f"{provider} 暂不支持自动获取模型列表，请手动填写模型名称。")


def _filter_chat_models(model_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in model_ids:
        model_id = str(raw or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id.removeprefix("models/")
        if not model_id:
            continue
        lowered = model_id.casefold()
        if any(marker in lowered for marker in _NON_CHAT_MODEL_MARKERS):
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        result.append(model_id)
    return result


def _list_openai_compatible_models(api_key: str, base_url: str) -> list[str]:
    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=_MODEL_LIST_TIMEOUT_S,
        max_retries=0,
    )
    return [str(item.id) for item in client.models.list().data]


def _list_anthropic_models(api_key: str, base_url: str) -> list[str]:
    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url or None,
        timeout=_MODEL_LIST_TIMEOUT_S,
        max_retries=0,
    )
    return [str(item.id) for item in client.models.list(limit=_MODEL_LIST_LIMIT).data]


def _list_google_models(api_key: str) -> list[str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    return [str(model.name) for model in client.models.list()]


def list_provider_models(payload: Any) -> dict[str, Any]:
    """List models the provider's live API currently advertises.

    The payload follows the same shape as ``configure`` (provider plus optional
    draft ``api_key`` / ``base_url``); a blank or omitted credential falls back
    to the saved one. Nothing is persisted.
    """
    config = config_from_payload(payload)
    provider = config.provider
    kind, base_url = _resolve_model_endpoint(provider, config)
    api_key = _effective_key(config, provider)

    try:
        if kind == "ollama":
            from .llm.ollama_discovery import validate_ollama_connection

            reachable, message, names = validate_ollama_connection(base_url)
            if not reachable:
                raise SetupError(f"无法连接 Ollama：{message}")
            model_ids = names
        elif kind == "google":
            model_ids = _list_google_models(api_key)
        elif kind == "anthropic":
            model_ids = _list_anthropic_models(api_key, base_url)
        else:
            model_ids = _list_openai_compatible_models(api_key, base_url)
    except SetupError:
        raise
    except Exception as exc:
        raise SetupError(f"获取模型列表失败：{_redact_error(exc, api_key)}") from None

    models = _filter_chat_models(model_ids)[:_MODEL_LIST_LIMIT]
    if not models:
        raise SetupError("接口没有返回可用的对话模型，请手动填写模型名称。")
    return {"provider": provider, "models": models}


def _read_payload() -> Any:
    raw = sys.stdin.read(MAX_PAYLOAD_CHARS + 1)
    if len(raw) > MAX_PAYLOAD_CHARS:
        raise SetupError("配置内容过长")
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise SetupError("配置不是有效 JSON") from exc


def main(argv: list[str] | None = None) -> int:
    """CLI entry used by the Electron setup window."""
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else ""
    try:
        if mode == "status":
            result = configuration_status()
        elif mode == "configure":
            result = configure_from_payload(_read_payload())
        elif mode == "validate":
            config_from_payload(_read_payload())
            result = {"valid": True}
        elif mode == "test":
            import asyncio

            result = asyncio.run(test_connection(_read_payload()))
        elif mode == "models":
            result = list_provider_models(_read_payload())
        else:
            raise SetupError(
                "用法: python -m jw.desktop_setup status|configure|validate|test|models"
            )
    except SetupError as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 2
    except Exception as exc:
        # The JSON protocol must never leak a traceback or secret to the shell.
        json.dump(
            {
                "ok": False,
                "error": f"配置失败（{type(exc).__name__}），请检查配置格式。",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 1
    json.dump({"ok": True, **result}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
