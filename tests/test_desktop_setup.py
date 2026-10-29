"""Tests for the non-interactive desktop setup JSON interface."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jw import desktop_setup
from types import SimpleNamespace
from unittest.mock import AsyncMock


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect config/data access and remove credentials inherited from CI."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("# isolated test workspace\n", encoding="utf-8")
    monkeypatch.delenv("JW_DASHSCOPE_PROXY_MODE", raising=False)
    for name in (
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "SILICONFLOW_API_KEY",
        "OPENROUTER_API_KEY",
        "MINIMAX_API_KEY",
        "MOONSHOT_API_KEY",
        "NVIDIA_API_KEY",
        "CUSTOM_OPENAI_API_KEY",
        "CUSTOM_ANTHROPIC_API_KEY",
        "CUSTOM_OPENAI_BASE_URL",
        "CUSTOM_ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_status_is_unconfigured_on_a_fresh_machine(isolated_config) -> None:
    status = desktop_setup.configuration_status()

    assert status["configured"] is False
    assert status["configExists"] is False
    assert status["provider"] == "dashscope"
    assert len(status["schema"]) >= 10
    assert "api_key" not in json.dumps(status)


def test_proxy_default_persistence_and_blank_key_retention(isolated_config):
    assert desktop_setup.proxy_status()["proxyMode"] == "env"
    desktop_setup.configure_from_payload(
        {"provider": "dashscope", "api_key": "synthetic-key", "proxy_mode": "direct"}
    )
    desktop_setup.configure_from_payload(
        {"provider": "dashscope", "api_key": "", "proxy_mode": "env"}
    )
    cfg = desktop_setup.load_config()
    assert cfg.dashscope_api_key == "synthetic-key"
    assert cfg.dashscope_proxy_mode == "env"


def test_workspace_proxy_override_matches_actual_configuration(
    isolated_config, monkeypatch
):
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    (isolated_config / ".env").write_text(
        "JW_DASHSCOPE_PROXY_MODE=direct\n", encoding="utf-8"
    )
    monkeypatch.setenv("JW_DASHSCOPE_PROXY_MODE", "env")
    status = desktop_setup.configuration_status()
    assert status["proxyMode"] == "direct"
    assert status["proxyModeSource"] == "workspace_env"
    assert status["proxyModeOverridden"] is True
    assert desktop_setup.get_effective_config().dashscope_proxy_mode == "direct"


@pytest.mark.asyncio
async def test_connection_uses_one_draft_request_without_saving(
    isolated_config, monkeypatch
):
    import jw.llm

    calls = []
    model = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content="OK"))
    )

    def build(**kwargs):
        calls.append(kwargs)
        assert os.environ["DASHSCOPE_API_KEY"] == "synthetic-draft-key"
        assert os.environ["JW_DASHSCOPE_PROXY_MODE"] == "direct"
        return model

    monkeypatch.setattr(jw.llm, "get_chat_model", build)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-saved-key")
    monkeypatch.setenv("JW_DASHSCOPE_PROXY_MODE", "")
    result = await desktop_setup.test_connection(
        {
            "provider": "dashscope",
            "api_key": "synthetic-draft-key",
            "proxy_mode": "direct",
        }
    )
    assert result["success"] is True
    assert model.ainvoke.await_count == 1
    assert calls[0]["max_retries"] == 0
    assert not desktop_setup.get_config_path().exists()
    assert "synthetic" not in json.dumps(result)


def test_protocol_unexpected_exception_never_returns_secret(monkeypatch, capsys):
    def fail():
        raise ValueError("synthetic-secret")

    monkeypatch.setattr(desktop_setup, "configuration_status", fail)
    assert desktop_setup.main(["status"]) == 1
    assert "synthetic-secret" not in capsys.readouterr().out


def test_configure_dashscope_persists_secret_free_summary(isolated_config) -> None:
    result = desktop_setup.configure_from_payload(
        {
            "provider": "dashscope",
            "model": "qwen3.7-plus",
            "api_key": "sk-test-secret-value",
        }
    )

    assert result["configured"] is True
    assert result["provider"] == "dashscope"
    assert "api_key" not in result
    assert "sk-test-secret-value" not in json.dumps(result)

    config_path = Path(result["configPath"])
    assert config_path.is_file()
    text = config_path.read_text(encoding="utf-8")
    assert "dashscope_api_key: sk-test-secret-value" in text
    if os.name != "nt" and config_path.stat().st_mode & 0o077:
        pytest.fail("config.yaml must not be readable by other users")


def test_status_reads_key_from_environment(isolated_config, monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-environment")

    status = desktop_setup.configuration_status()

    assert status["configured"] is True
    assert "sk-from-environment" not in json.dumps(status)


def test_configure_custom_openai_requires_base_url(isolated_config) -> None:
    with pytest.raises(desktop_setup.SetupError, match="Base URL"):
        desktop_setup.configure_from_payload(
            {
                "provider": "custom-openai",
                "api_key": "sk-test",
            }
        )


def test_configure_custom_openai_persists_base_url(isolated_config) -> None:
    result = desktop_setup.configure_from_payload(
        {
            "provider": "custom-openai",
            "model": "qwen3.7-plus",
            "api_key": "sk-test",
            "base_url": "https://example.test/v1/",
        }
    )

    assert result["configured"] is True
    config_path = Path(result["configPath"])
    text = config_path.read_text(encoding="utf-8")
    assert "custom_openai_base_url: https://example.test/v1" in text
    assert "custom_openai_api_key: sk-test" in text


def test_configure_rejects_unknown_provider(isolated_config) -> None:
    with pytest.raises(desktop_setup.SetupError, match="provider"):
        desktop_setup.configure_from_payload(
            {"provider": "not-a-provider", "api_key": "sk-test"}
        )


def test_configure_rejects_missing_api_key(isolated_config) -> None:
    with pytest.raises(desktop_setup.SetupError, match="API Key"):
        desktop_setup.configure_from_payload(
            {"provider": "openai", "model": "gpt-5.6-terra"}
        )


def test_main_status_emits_json(isolated_config, capsys) -> None:
    exit_code = desktop_setup.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["configured"] is False


def test_main_error_is_json_without_traceback(isolated_config, capsys) -> None:
    exit_code = desktop_setup.main(["unknown-mode"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Traceback" not in payload["error"]
