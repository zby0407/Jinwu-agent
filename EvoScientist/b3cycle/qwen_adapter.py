from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-max-2026-06-08"
REVIEWED_MODELS = frozenset(
    {
        DEFAULT_MODEL,
        "qwen3.7-plus-2026-05-26",
        "qwen3.6-flash-2026-04-16",
    }
)
_ALLOWED_DASHSCOPE_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
    }
)
_COMPATIBILITY_PATH = "/compatible-mode/v1"


class QwenAdapterConfigurationError(ValueError):
    """Raised when a live Qwen route is outside the reviewed trust boundary."""


def _validated_model(model: str) -> str:
    if model not in REVIEWED_MODELS:
        raise QwenAdapterConfigurationError(
            "B3_QWEN_MODEL must be a reviewed dated Qwen model: "
            + ", ".join(sorted(REVIEWED_MODELS))
        )
    return model


def _validated_base_url(base_url: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in base_url):
        raise QwenAdapterConfigurationError(
            "B3_QWEN_BASE_URL must not contain control characters"
        )
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise QwenAdapterConfigurationError(
            "B3_QWEN_BASE_URL is not a valid URL"
        ) from exc

    hostname = (parsed.hostname or "").lower()
    canonical_authority = hostname if port is None else f"{hostname}:{port}"
    official_host = hostname in _ALLOWED_DASHSCOPE_HOSTS or hostname.endswith(
        ".maas.aliyuncs.com"
    )
    if (
        parsed.scheme.lower() != "https"
        or not official_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.netloc.lower() != canonical_authority
        or parsed.path != _COMPATIBILITY_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise QwenAdapterConfigurationError(
            "B3_QWEN_BASE_URL must be an official Alibaba Cloud HTTPS "
            "compatibility endpoint with the exact /compatible-mode/v1 path"
        )
    return urllib.parse.urlunsplit(
        ("https", canonical_authority, _COMPATIBILITY_PATH, "", "")
    )


@dataclass(frozen=True)
class QwenAdapterConfig:
    enabled: bool
    api_key_present: bool
    base_url: str
    model: str
    timeout_seconds: float
    max_tokens: int
    temperature: float


class QwenAdapter:
    """Optional Alibaba Cloud Model Studio/Qwen adapter with deterministic fallback.

    The adapter follows the OpenAI-compatible DashScope route. It is deliberately
    opt-in: setting an API key is not enough; B3_QWEN_ENABLED must also be true.
    """

    def __init__(self, config: QwenAdapterConfig, api_key: str | None = None) -> None:
        self.config = config
        self._api_key = api_key or ""

    @classmethod
    def from_env(cls) -> "QwenAdapter":
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY") or ""
        enabled = (os.getenv("B3_QWEN_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
        config = QwenAdapterConfig(
            enabled=enabled,
            api_key_present=bool(api_key),
            base_url=os.getenv("B3_QWEN_BASE_URL") or DEFAULT_BASE_URL,
            model=os.getenv("B3_QWEN_MODEL") or DEFAULT_MODEL,
            timeout_seconds=float(os.getenv("B3_QWEN_TIMEOUT_SECONDS") or "20"),
            max_tokens=int(os.getenv("B3_QWEN_MAX_TOKENS") or "900"),
            temperature=float(os.getenv("B3_QWEN_TEMPERATURE") or "0.2"),
        )
        return cls(config, api_key=api_key)

    def status(self) -> dict[str, Any]:
        if not self.config.enabled:
            mode = "deterministic_fallback"
            reason = "B3_QWEN_ENABLED is not true"
        else:
            try:
                _validated_model(self.config.model)
                _validated_base_url(self.config.base_url)
            except QwenAdapterConfigurationError as exc:
                mode = "deterministic_fallback"
                reason = str(exc)
            else:
                if not self.config.api_key_present:
                    mode = "deterministic_fallback"
                    reason = "DASHSCOPE_API_KEY or QWEN_API_KEY is not set"
                else:
                    mode = "qwen_openai_compatible"
                    reason = None
        return {
            "provider": "Alibaba Cloud Model Studio / Qwen",
            "mode": mode,
            "enabled": self.config.enabled,
            "api_key_present": self.config.api_key_present,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "timeout_seconds": self.config.timeout_seconds,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "fallback_reason": reason,
            "credential_policy": "API keys are read from environment variables and never persisted in run artifacts.",
        }

    def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if self.status()["mode"] != "qwen_openai_compatible":
            return self._fallback_payload(fallback, "adapter_not_enabled")
        request_payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": "Return one valid JSON object only. Do not wrap it in Markdown.",
                            "input": user_payload,
                            "schema": schema,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        try:
            response = self._post_chat_completion(request_payload)
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_json_fence(content))
            if not isinstance(parsed, dict):
                return self._fallback_payload(fallback, "model_returned_non_object")
            parsed["_qwen_adapter"] = {
                "mode": "qwen_openai_compatible",
                "model": self.config.model,
                "finish_reason": response["choices"][0].get("finish_reason"),
            }
            return parsed
        except Exception as exc:
            payload = self._fallback_payload(fallback, "model_call_failed")
            payload["_qwen_adapter"]["error_type"] = type(exc).__name__
            payload["_qwen_adapter"]["error"] = str(exc)[:300]
            return payload

    def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Revalidate immediately before constructing the Authorization-bearing
        # request so a caller cannot bypass the environment/status checks.
        _validated_model(self.config.model)
        base_url = _validated_base_url(self.config.base_url)
        endpoint = f"{base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen HTTP {exc.code}: {body[:300]}") from exc
        parsed = json.loads(body)
        if not isinstance(parsed, dict) or "choices" not in parsed:
            raise RuntimeError("Qwen response missing choices")
        return parsed

    def _fallback_payload(self, fallback: dict[str, Any], reason: str) -> dict[str, Any]:
        payload = dict(fallback)
        payload["_qwen_adapter"] = {
            "mode": "deterministic_fallback",
            "model": self.config.model,
            "reason": reason,
        }
        return payload


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped
