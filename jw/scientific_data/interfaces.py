"""Resolve and persist scientific-data interface settings.

The WebUI configures the parsing interfaces from the machine that runs the
backend: PDF table OCR, chart reading and the data-discovery providers.  The
same rule as literature credentials applies -- environment variables win over
``~/.config/jw/config.yaml``, and the API only ever returns a mask for a secret.

Values are stored on the machine (not in browser storage) because the tools
that call Baidu/HTTP table APIs and the archive providers run inside the
backend process.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from jw.config.settings import JWConfig, load_config, save_config

__all__ = [
    "GROUPS",
    "SETTING_SPECS",
    "interface_status",
    "setting_value",
    "update_interfaces",
]

_MAX_VALUE_LENGTH = 2048
_URL_RE = re.compile(r"^https?://[^\s]+$")

GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "pdf",
        "label": "PDF 表格识别",
        "description": "文本层解析失败或页面只有图片时，把页面渲染成图片交给云端表格识别。",
    },
    {
        "id": "figure",
        "label": "图表识别",
        "description": "图表统一交给云端读图模型，返回 series / x / y 形式的候选数据点。",
    },
    {
        "id": "discovery",
        "label": "数据源发现",
        "description": "允许联网时自动检索 Zenodo / Dataverse / OpenAlex，把候选文件登记为数据源。",
    },
)


@dataclass(frozen=True, slots=True)
class SettingSpec:
    group: str
    field: str
    env: str
    label: str
    kind: str  # key | url | text | provider | list
    hint: str
    options: tuple[str, ...] = ()


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "pdf",
        "pdf_cloud_provider",
        "JW_PDF_CLOUD_PROVIDER",
        "表格识别服务",
        "provider",
        "留空 = 只用本地 PyMuPDF 文本层；baidu = 百度表格文字识别 V2；generic = 自建 HTTP 接口。",
        ("", "baidu", "generic"),
    ),
    SettingSpec(
        "pdf",
        "baidu_ocr_api_key",
        "BAIDU_OCR_API_KEY",
        "百度 OCR API Key",
        "key",
        "百度智能云控制台的 API Key；provider=baidu 时必填。",
    ),
    SettingSpec(
        "pdf",
        "baidu_ocr_secret_key",
        "BAIDU_OCR_SECRET_KEY",
        "百度 OCR Secret Key",
        "key",
        "与 API Key 配对，用于换取 access_token。",
    ),
    SettingSpec(
        "pdf",
        "pdf_cloud_endpoint",
        "JW_PDF_CLOUD_ENDPOINT",
        "通用表格接口地址",
        "url",
        "provider=generic 时必填；POST JSON {image_base64, mime, page, dpi}。",
    ),
    SettingSpec(
        "pdf",
        "pdf_cloud_api_key",
        "JW_PDF_CLOUD_API_KEY",
        "通用表格接口密钥",
        "key",
        "可选，作为 Bearer token 发送。",
    ),
    SettingSpec(
        "figure",
        "figure_cloud_provider",
        "JW_FIGURE_CLOUD_PROVIDER",
        "图表识别服务",
        "provider",
        "留空 = 关闭云端读图；generic = 自建 VLM/图表接口。",
        ("", "generic"),
    ),
    SettingSpec(
        "figure",
        "figure_cloud_endpoint",
        "JW_FIGURE_CLOUD_ENDPOINT",
        "图表接口地址",
        "url",
        "provider=generic 时必填；POST JSON {image_base64, mime, page, dpi, prompt}。",
    ),
    SettingSpec(
        "figure",
        "figure_cloud_api_key",
        "JW_FIGURE_CLOUD_API_KEY",
        "图表接口密钥",
        "key",
        "可选，作为 Bearer token 发送。",
    ),
    SettingSpec(
        "figure",
        "figure_cloud_model",
        "JW_FIGURE_CLOUD_MODEL",
        "图表模型名",
        "text",
        "可选；会写进缓存指纹，换模型后重新读图。",
    ),
    SettingSpec(
        "discovery",
        "data_providers",
        "JW_DATA_PROVIDERS",
        "启用的数据仓库",
        "list",
        "逗号分隔，可选 zenodo、dataverse、openalex；留空使用默认三源。",
    ),
    SettingSpec(
        "discovery",
        "zenodo_endpoint",
        "JW_ZENODO_ENDPOINT",
        "Zenodo 接口",
        "url",
        "默认 https://zenodo.org/api/records。",
    ),
    SettingSpec(
        "discovery",
        "dataverse_endpoint",
        "JW_DATAVERSE_ENDPOINT",
        "Dataverse 站点",
        "url",
        "默认 https://dataverse.harvard.edu。",
    ),
    SettingSpec(
        "discovery",
        "openalex_endpoint",
        "JW_OPENALEX_ENDPOINT",
        "OpenAlex 接口",
        "url",
        "默认 https://api.openalex.org/works。",
    ),
)

_BY_FIELD = {spec.field: spec for spec in SETTING_SPECS}


def _known_providers() -> tuple[str, ...]:
    from jw.scientific_data.data_providers import list_providers

    return list_providers()


def _mask(value: str, kind: str) -> str:
    if not value:
        return ""
    if kind != "key":
        return value
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


def _effective(spec: SettingSpec, config: JWConfig) -> tuple[str, str]:
    env_value = os.environ.get(spec.env, "").strip()
    if env_value:
        return env_value, "environment"
    config_value = str(getattr(config, spec.field, "") or "").strip()
    if config_value:
        return config_value, "config"
    return "", ""


def setting_value(field: str) -> str:
    """Return the effective value of one interface setting."""

    spec = _BY_FIELD.get(field)
    if spec is None:
        return ""
    return _effective(spec, load_config())[0]


def interface_status() -> dict[str, Any]:
    """Masked status for every interface setting the UI can manage."""

    config = load_config()
    items: list[dict[str, Any]] = []
    for spec in SETTING_SPECS:
        value, source = _effective(spec, config)
        items.append(
            {
                "group": spec.group,
                "field": spec.field,
                "label": spec.label,
                "env": spec.env,
                "kind": spec.kind,
                "hint": spec.hint,
                "options": list(spec.options),
                "configured": bool(value),
                "source": source,
                "masked": _mask(value, spec.kind),
                # Values that are not secrets stay visible so the operator can
                # edit an endpoint or provider in place.
                "value": "" if spec.kind == "key" else value,
                "env_override": source == "environment",
            }
        )
    return {"status": "ok", "groups": list(GROUPS), "items": items}


def _validate(spec: SettingSpec, value: str) -> str | None:
    if len(value) > _MAX_VALUE_LENGTH:
        return f"{spec.label}过长（上限 {_MAX_VALUE_LENGTH} 字符）"
    if spec.kind == "url" and value and not _URL_RE.match(value):
        return f"{spec.label}必须是 http(s) 地址"
    if spec.kind == "provider" and value and value not in spec.options:
        allowed = "、".join(option or "（留空）" for option in spec.options)
        return f"{spec.label}只能是 {allowed}"
    if spec.kind == "list" and value:
        known = set(_known_providers())
        items = [item.strip() for item in value.split(",") if item.strip()]
        unknown = [item for item in items if item not in known]
        if unknown:
            return f"未知数据仓库：{', '.join(unknown)}"
        if not items:
            return f"{spec.label}不能为空"
    return None


def update_interfaces(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a batch of interface settings.

    Keys are config field names (``pdf_cloud_endpoint``).  An empty string
    clears the stored value.  The whole batch is validated first, so one typo
    never leaves the config half-written.
    """

    if not isinstance(updates, dict) or not updates:
        return {"status": "error", "error": "没有需要保存的设置"}

    unknown = [key for key in updates if key not in _BY_FIELD]
    if unknown:
        return {"status": "error", "error": f"未知设置项：{', '.join(unknown)}"}

    config = load_config()
    pending: list[tuple[SettingSpec, str]] = []
    for field, raw in updates.items():
        spec = _BY_FIELD[field]
        if raw is None:
            continue
        if not isinstance(raw, str):
            return {"status": "error", "error": f"{spec.label}必须是字符串"}
        value = raw.strip()
        error = _validate(spec, value)
        if error:
            return {"status": "error", "error": error}
        pending.append((spec, value))

    if not pending:
        return {"status": "error", "error": "没有需要保存的设置"}

    for spec, value in pending:
        setattr(config, spec.field, value)
    save_config(config)

    return {
        "status": "ok",
        **interface_status(),
        "saved": [spec.field for spec, _ in pending],
        "note": "已写入本机配置（0600）。环境变量优先于配置文件。",
    }
