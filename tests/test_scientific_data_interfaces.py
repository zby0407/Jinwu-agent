from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from jw.langgraph_dev import scientific_data_interfaces_api as api
from jw.langgraph_dev.http import app
from jw.scientific_data.interfaces import (
    interface_status,
    setting_value,
    update_interfaces,
)

_ENV_NAMES = (
    "JW_PDF_CLOUD_PROVIDER",
    "BAIDU_OCR_API_KEY",
    "BAIDU_OCR_SECRET_KEY",
    "JW_PDF_CLOUD_ENDPOINT",
    "JW_PDF_CLOUD_API_KEY",
    "JW_FIGURE_CLOUD_PROVIDER",
    "JW_FIGURE_CLOUD_ENDPOINT",
    "JW_FIGURE_CLOUD_API_KEY",
    "JW_FIGURE_CLOUD_MODEL",
    "JW_DATA_PROVIDERS",
    "JW_ZENODO_ENDPOINT",
    "JW_DATAVERSE_ENDPOINT",
    "JW_OPENALEX_ENDPOINT",
    "JW_SCIENTIFIC_DATA_ALLOW_REMOTE",
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _item(result, field):
    return next(item for item in result["items"] if item["field"] == field)


def test_saved_key_is_masked_and_persisted_with_owner_only_permissions(tmp_path):
    result = update_interfaces({"pdf_cloud_api_key": "secret-key-1234"})

    assert result["status"] == "ok"
    item = _item(result, "pdf_cloud_api_key")
    assert item["configured"] is True
    assert item["source"] == "config"
    assert item["masked"] == "••••1234"
    assert "secret-key" not in str(result)

    path = tmp_path / "config" / "jw" / "config.yaml"
    assert path.exists()
    if os.name != "nt":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "secret-key-1234" in path.read_text(encoding="utf-8")


def test_environment_wins_over_config_and_is_marked_as_override(monkeypatch):
    update_interfaces({"figure_cloud_api_key": "config-key-1111"})
    monkeypatch.setenv("JW_FIGURE_CLOUD_API_KEY", "env-key-9999")

    assert setting_value("figure_cloud_api_key") == "env-key-9999"
    item = _item(interface_status(), "figure_cloud_api_key")
    assert item["source"] == "environment"
    assert item["env_override"] is True
    assert item["masked"] == "••••9999"


def test_provider_must_be_one_of_the_offered_choices():
    result = update_interfaces({"pdf_cloud_provider": "tencent"})

    assert result["status"] == "error"
    assert "表格识别服务" in result["error"]
    assert "留空" in result["error"]


def test_url_is_validated_before_anything_is_written(tmp_path):
    result = update_interfaces({"figure_cloud_endpoint": "ftp://example.org/chart"})

    assert result["status"] == "error"
    assert "http(s)" in result["error"]
    assert not (tmp_path / "config" / "jw" / "config.yaml").exists()


def test_unknown_data_provider_is_rejected():
    result = update_interfaces({"data_providers": "zenodo,figshare"})

    assert result["status"] == "error"
    assert "figshare" in result["error"]


def test_data_provider_list_accepts_the_known_names():
    result = update_interfaces({"data_providers": "zenodo, openalex"})

    assert result["status"] == "ok"
    item = _item(result, "data_providers")
    assert item["value"] == "zenodo, openalex"
    assert item["configured"] is True


def test_empty_value_clears_the_stored_secret():
    update_interfaces({"baidu_ocr_secret_key": "secret-123456"})
    result = update_interfaces({"baidu_ocr_secret_key": ""})

    assert result["status"] == "ok"
    item = _item(result, "baidu_ocr_secret_key")
    assert item["configured"] is False
    assert item["source"] == ""


def test_unknown_field_is_rejected():
    result = update_interfaces({"not_a_field": "value"})

    assert result["status"] == "error"
    assert "未知设置项" in result["error"]


def test_write_is_refused_for_a_remote_client(monkeypatch):
    monkeypatch.delenv("JW_SCIENTIFIC_DATA_ALLOW_REMOTE", raising=False)
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))

    assert api._writes_allowed(request) is False

    monkeypatch.setenv("JW_SCIENTIFIC_DATA_ALLOW_REMOTE", "1")
    assert api._writes_allowed(request) is True


def test_write_is_allowed_for_a_loopback_client(monkeypatch):
    monkeypatch.delenv("JW_SCIENTIFIC_DATA_ALLOW_REMOTE", raising=False)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    assert api._writes_allowed(request) is True


def test_http_surface_saves_but_never_returns_the_secret():
    client = TestClient(app)

    saved = client.put(
        "/api/scientific-data/interfaces",
        json={"settings": {"pdf_cloud_api_key": "http-secret-7788"}},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["status"] == "ok"
    assert "http-secret-7788" not in saved.text
    item = _item(body, "pdf_cloud_api_key")
    assert item["configured"] is True
    assert item["masked"] == "••••7788"

    listed = client.get("/api/scientific-data/interfaces")
    assert listed.status_code == 200
    assert "http-secret-7788" not in listed.text
    payload = listed.json()
    assert payload["status"] == "ok"
    assert [group["id"] for group in payload["groups"]] == [
        "pdf",
        "figure",
        "discovery",
    ]


def test_saved_settings_reach_the_runtime_resolvers():
    from jw.scientific_data.cloud_figures import resolve_figure_config
    from jw.scientific_data.cloud_tables import resolve_cloud_config
    from jw.scientific_data.data_providers import resolve_provider_settings

    update_interfaces(
        {
            "pdf_cloud_provider": "generic",
            "pdf_cloud_endpoint": "https://ocr.example/table",
            "pdf_cloud_api_key": "table-key-1234",
            "figure_cloud_endpoint": "https://ocr.example/chart",
            "figure_cloud_api_key": "chart-key-5678",
            "data_providers": "zenodo, openalex",
            "zenodo_endpoint": "https://mirror.example/records",
        }
    )

    table = resolve_cloud_config()
    assert table.enabled is True
    assert table.provider == "generic"
    assert table.endpoint == "https://ocr.example/table"
    assert table.api_key == "table-key-1234"

    figure = resolve_figure_config()
    assert figure.enabled is True
    assert figure.endpoint == "https://ocr.example/chart"
    assert figure.api_key == "chart-key-5678"

    providers = resolve_provider_settings()
    assert providers.providers == ("zenodo", "openalex")
    assert providers.zenodo_endpoint == "https://mirror.example/records"


def test_http_surface_rejects_an_invalid_payload():
    client = TestClient(app)

    response = client.put(
        "/api/scientific-data/interfaces",
        json={"settings": {"pdf_cloud_api_key": 123}},
    )

    assert response.status_code == 400
    assert "字符串" in response.json()["error"]
