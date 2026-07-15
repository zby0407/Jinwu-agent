from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def resolve_roots() -> tuple[Path, Path]:
    env_root = os.getenv("B3_PROJECT_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        code_root = root / "code" if (root / "code" / "src").exists() else root
        return root, code_root
    candidate = Path(__file__).resolve().parents[1]
    if candidate.name == "code" and (candidate.parent / "release_manifest.json").exists():
        return candidate.parent, candidate
    return candidate, candidate


ROOT, CODE_ROOT = resolve_roots()
SRC = CODE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3cycle.qwen_adapter import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    REVIEWED_MODELS,
    QwenAdapter,
)


SHARED_ENDPOINT_SCOPES = {
    "https://dashscope.aliyuncs.com/compatible-mode/v1": "shared_cn_beijing",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1": "shared_us",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1": "shared_international",
}


def endpoint_proof_metadata(base_url: str) -> dict[str, str]:
    """Describe a route without persisting a dedicated workspace hostname."""

    normalized = base_url.rstrip("/")
    scope = SHARED_ENDPOINT_SCOPES.get(normalized)
    if scope is not None:
        display = normalized
    else:
        parsed = urllib.parse.urlsplit(normalized)
        hostname = (parsed.hostname or "").lower()
        if hostname.endswith(".maas.aliyuncs.com"):
            labels = hostname.split(".")
            masked_host = ".".join(["<workspace>", *labels[1:]])
            display = urllib.parse.urlunsplit(
                ("https", masked_host, parsed.path, "", "")
            )
            scope = "workspace_dedicated"
        else:
            display = "<official-endpoint>"
            scope = "official_other"
    return {
        "endpoint_scope": scope,
        "endpoint_display": display,
        "endpoint_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _sanitize_endpoint_strings(value: Any, base_url: str) -> Any:
    """Remove a dedicated workspace host or ID from nested diagnostic fields."""

    normalized = base_url.rstrip("/")
    if normalized in SHARED_ENDPOINT_SCOPES:
        return value
    parsed = urllib.parse.urlsplit(normalized)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".maas.aliyuncs.com"):
        return value
    workspace_id = hostname.split(".", 1)[0]
    replacements = (
        (normalized, "<workspace-endpoint>"),
        (hostname, "<workspace-host>"),
        (workspace_id, "<workspace>"),
    )

    if isinstance(value, str):
        sanitized = value
        for sensitive, replacement in replacements:
            if sensitive:
                sanitized = sanitized.replace(sensitive, replacement)
        return sanitized
    if isinstance(value, dict):
        return {
            key: _sanitize_endpoint_strings(item, normalized)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_endpoint_strings(item, normalized) for item in value]
    return value


def configure_cli_route(model: str, endpoint: str) -> tuple[str, str]:
    """Apply an explicit reviewed route to this process without persisting it."""

    if model not in REVIEWED_MODELS:
        raise ValueError(
            "model must be a reviewed dated Qwen model: "
            + ", ".join(sorted(REVIEWED_MODELS))
        )
    normalized = endpoint.rstrip("/")
    suffix = "/chat/completions"
    base_url = normalized[: -len(suffix)] if normalized.endswith(suffix) else normalized
    os.environ["B3_QWEN_MODEL"] = model
    os.environ["B3_QWEN_BASE_URL"] = base_url
    return model, base_url


def make_probe() -> dict[str, Any]:
    adapter = QwenAdapter.from_env()
    status = adapter.status()
    schema = {
        "type": "object",
        "required": ["verdict", "qwen_role", "safety_boundary"],
        "properties": {
            "verdict": {"type": "string"},
            "qwen_role": {"type": "string"},
            "safety_boundary": {"type": "string"},
        },
    }
    fallback = {
        "verdict": "fallback_not_live",
        "qwen_role": "language-only critique layer",
        "safety_boundary": "numeric solar-cycle results stay controlled by deterministic code",
    }
    response = adapter.complete_json(
        system_prompt=(
            "You are a connection probe for Solar-Cycle Co-Scientist. "
            "Return valid JSON only. Do not include secrets or credentials."
        ),
        user_payload={
            "probe": "B3 Qwen/Bailian live-call verification",
            "required_answer": "State that Qwen is used only for language critique, not numeric gates.",
        },
        schema=schema,
        fallback=fallback,
    )
    adapter_meta = response.get("_qwen_adapter", {})
    live_ok = adapter_meta.get("mode") == "qwen_openai_compatible" and not adapter_meta.get("error_type")
    base_url = str(status["base_url"])
    return {
        "schema_version": "b3-qwen-proof-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "live_connection_ok" if live_ok else "dry_run_fallback",
        "live_ok": live_ok,
        "provider": status["provider"],
        "model": status["model"],
        **endpoint_proof_metadata(base_url),
        "mode": status["mode"],
        "enabled": status["enabled"],
        "api_key_present": status["api_key_present"],
        "credential_policy": status["credential_policy"],
        "fallback_reason": _sanitize_endpoint_strings(
            status.get("fallback_reason"), base_url
        ),
        "adapter_meta": _sanitize_endpoint_strings(adapter_meta, base_url),
        "response_without_secrets": {
            key: value for key, value in response.items() if key != "_qwen_adapter"
        },
    }


def write_outputs(probe: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "qwen_connection_check_live" if probe["live_ok"] else "qwen_connection_check_dry_run"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Qwen/Bailian Connection Check",
                "",
                f"- checked_at: `{probe['checked_at']}`",
                f"- status: `{probe['status']}`",
                f"- provider: `{probe['provider']}`",
                f"- model: `{probe['model']}`",
                f"- endpoint_scope: `{probe['endpoint_scope']}`",
                f"- endpoint_display: `{probe['endpoint_display']}`",
                f"- endpoint_sha256: `{probe['endpoint_sha256']}`",
                f"- mode: `{probe['mode']}`",
                f"- enabled: `{probe['enabled']}`",
                f"- api_key_present: `{probe['api_key_present']}`",
                f"- credential_policy: {probe['credential_policy']}",
                "",
                "## Response Without Secrets",
                "",
                "```json",
                json.dumps(probe["response_without_secrets"], ensure_ascii=False, indent=2),
                "```",
                "",
                "## Safety Boundary",
                "",
                "This proof records only credential-free metadata. Dedicated endpoint hosts and workspace IDs are masked; only their endpoint scope and SHA-256 fingerprint are persisted. Shared public endpoints may be recorded verbatim. API keys, account IDs, and access tokens are never written to disk.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the optional Qwen/Bailian route and write a credential-safe proof.")
    parser.add_argument(
        "--model",
        default=os.getenv("B3_QWEN_MODEL") or DEFAULT_MODEL,
        choices=sorted(REVIEWED_MODELS),
        help="Reviewed dated Qwen model; Max is the default.",
    )
    parser.add_argument(
        "--endpoint",
        default=f"{os.getenv('B3_QWEN_BASE_URL') or DEFAULT_BASE_URL}/chat/completions",
        help="Official OpenAI-compatible base URL or /chat/completions endpoint.",
    )
    parser.add_argument("--require-live", action="store_true", help="Exit non-zero unless a real Qwen/Bailian call succeeds.")
    default_output_dir = ROOT / "proofs" if (ROOT / "release_manifest.json").exists() else ROOT / "b3" / "proofs"
    parser.add_argument("--output-dir", default=str(default_output_dir), help="Directory for credential-safe proof files.")
    parser.add_argument("--no-write", action="store_true", help="Do not write proof files; print JSON only.")
    args = parser.parse_args()

    configure_cli_route(args.model, args.endpoint)
    if args.require_live:
        os.environ["B3_QWEN_ENABLED"] = "1"
    probe = make_probe()
    if not args.no_write:
        json_path, md_path = write_outputs(probe, Path(args.output_dir))
        probe["written_files"] = [str(json_path), str(md_path)]
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    if args.require_live and not probe["live_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
