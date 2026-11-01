"""Resolve and persist literature-source credentials.

The competition demo is often run by an operator on a laptop, while the tools
that call OpenAlex/Crossref run inside the langgraph backend process. Keeping
the key in browser localStorage would therefore not reach the tools and would
expose it to page scripts. This module keeps the key on the machine instead:
``~/.config/jw/config.yaml`` (0600) or the process environment.

Resolution order is environment first, then the config file. The environment
is never overwritten from the file, so a key injected by a deployment keeps
working even if the file is stale. The API never returns a stored value; it
returns a mask plus the effective source.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from jw.config.settings import JWConfig, load_config, save_config

__all__ = [
    "CREDENTIAL_SPECS",
    "credential_status",
    "credential_value",
    "contact_email",
    "update_credentials",
]

_MAX_VALUE_LENGTH = 512
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    provider: str
    field: str
    env: str
    label: str
    kind: str
    hint: str


CREDENTIAL_SPECS: tuple[CredentialSpec, ...] = (
    CredentialSpec(
        "openalex",
        "openalex_api_key",
        "OPENALEX_API_KEY",
        "OpenAlex API key",
        "key",
        "可选。填写后启用礼貌池和 cursor 深分页，历史队列构建更稳定。",
    ),
    CredentialSpec(
        "openalex_mailto",
        "openalex_mailto",
        "OPENALEX_MAILTO",
        "OpenAlex 联系邮箱",
        "email",
        "可选。OpenAlex 礼貌池联系方式；不填时回退到 Crossref 邮箱。",
    ),
    CredentialSpec(
        "crossref",
        "crossref_mailto",
        "CROSSREF_MAILTO",
        "Crossref 联系邮箱",
        "email",
        "可选。Crossref 礼貌池联系方式，也作为 OpenAlex 的回退邮箱。",
    ),
    CredentialSpec(
        "unpaywall",
        "unpaywall_email",
        "UNPAYWALL_EMAIL",
        "Unpaywall 邮箱",
        "email",
        "可选。Unpaywall 要求真实邮箱；未配置时不会发起请求。",
    ),
    CredentialSpec(
        "ads",
        "ads_api_token",
        "ADS_API_TOKEN",
        "NASA ADS token",
        "key",
        "可选。未配置时 ADS 会返回 typed not_configured，不影响其他来源。",
    ),
    CredentialSpec(
        "semantic_scholar",
        "semantic_scholar_api_key",
        "SEMANTIC_SCHOLAR_API_KEY",
        "Semantic Scholar key",
        "key",
        "可选。提高 Semantic Scholar 限流额度。",
    ),
    CredentialSpec(
        "core",
        "core_api_key",
        "CORE_API_KEY",
        "CORE API key",
        "key",
        "可选。未配置时 CORE 不参与检索。",
    ),
)

_BY_PROVIDER = {spec.provider: spec for spec in CREDENTIAL_SPECS}
_BY_FIELD = {spec.field: spec for spec in CREDENTIAL_SPECS}


def _spec(provider: str) -> CredentialSpec | None:
    return _BY_PROVIDER.get(provider)


def _mask(value: str, kind: str) -> str:
    if not value:
        return ""
    if kind == "email" and "@" in value:
        local, domain = value.split("@", 1)
        head = local[:1] if local else ""
        return f"{head}***@{domain}"
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


def _source(value: str) -> str:
    return "environment" if value else ""


def _resolve(spec: CredentialSpec, config: JWConfig) -> tuple[str, str]:
    env_value = os.environ.get(spec.env, "").strip()
    if env_value:
        return env_value, "environment"
    config_value = str(getattr(config, spec.field, "") or "").strip()
    if config_value:
        return config_value, "config"
    return "", ""


def credential_value(provider: str) -> str:
    """Return the effective credential for ``provider``, or an empty string."""

    spec = _spec(provider)
    if spec is None:
        # Publisher TDM providers are still environment-only; keep the old
        # lookup so their adapters do not silently lose support.
        from jw.research_influence.providers.base import CREDENTIAL_ENV

        env = CREDENTIAL_ENV.get(provider, "")
        return os.environ.get(env, "").strip() if env else ""
    return _resolve(spec, load_config())[0]


def contact_email() -> str:
    """Polite-pool email shared by OpenAlex/Crossref, or empty."""

    for provider in ("crossref", "unpaywall", "openalex_mailto"):
        value = credential_value(provider)
        if value:
            return value
    return ""


def credential_status() -> list[dict[str, Any]]:
    """Masked status for every credential the UI can manage."""

    config = load_config()
    items: list[dict[str, Any]] = []
    for spec in CREDENTIAL_SPECS:
        value, source = _resolve(spec, config)
        items.append(
            {
                "provider": spec.provider,
                "label": spec.label,
                "env": spec.env,
                "kind": spec.kind,
                "hint": spec.hint,
                "configured": bool(value),
                "source": source,
                "masked": _mask(value, spec.kind),
                # The environment always wins; tell the UI that a saved value
                # would be shadowed rather than pretending the save worked.
                "env_override": source == "environment",
            }
        )
    return items


def _validate_value(spec: CredentialSpec, value: str) -> str | None:
    if len(value) > _MAX_VALUE_LENGTH:
        return f"{spec.label}过长（上限 {_MAX_VALUE_LENGTH} 字符）"
    if value and spec.kind == "email" and not _EMAIL_RE.match(value):
        return f"{spec.label}不是有效邮箱地址"
    return None


def update_credentials(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a batch of credential updates.

    ``updates`` is keyed by provider. An empty string clears the stored value.
    The whole batch is validated before anything is written, so a typo in one
    field does not leave the config half-updated.
    """

    if not isinstance(updates, dict) or not updates:
        return {"status": "error", "error": "没有需要保存的凭据"}

    unknown = [key for key in updates if key not in _BY_PROVIDER]
    if unknown:
        return {"status": "error", "error": f"未知来源：{', '.join(unknown)}"}

    config = load_config()
    pending: list[tuple[CredentialSpec, str]] = []
    for provider, raw in updates.items():
        spec = _BY_PROVIDER[provider]
        if raw is None:
            continue
        if not isinstance(raw, str):
            return {"status": "error", "error": f"{spec.label}必须是字符串"}
        value = raw.strip()
        error = _validate_value(spec, value)
        if error:
            return {"status": "error", "error": error}
        pending.append((spec, value))

    if not pending:
        return {"status": "error", "error": "没有需要保存的凭据"}

    for spec, value in pending:
        setattr(config, spec.field, value)
    save_config(config)

    return {
        "status": "ok",
        "items": credential_status(),
        "saved": [spec.provider for spec, _ in pending],
        "note": "已写入本机配置（0600）。环境变量优先于配置文件。",
    }


def credential_field(provider: str) -> str:
    """Config field name for a provider (for tests and diagnostics)."""

    spec = _spec(provider)
    return spec.field if spec else ""


def credential_specs() -> tuple[CredentialSpec, ...]:
    return CREDENTIAL_SPECS
