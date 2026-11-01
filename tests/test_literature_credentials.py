from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from jw.langgraph_dev import literature_credentials_api as api
from jw.langgraph_dev.http import app
from jw.literature_credentials import (
    credential_status,
    credential_value,
    update_credentials,
)
from jw.research_influence.providers.base import ProviderPolicy


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in (
        "OPENALEX_API_KEY",
        "OPENALEX_MAILTO",
        "CROSSREF_MAILTO",
        "UNPAYWALL_EMAIL",
        "ADS_API_TOKEN",
        "SEMANTIC_SCHOLAR_API_KEY",
        "CORE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_saved_key_is_masked_and_persisted_with_owner_only_permissions(tmp_path):
    result = update_credentials({"openalex": "secret-key-1234"})

    assert result["status"] == "ok"
    item = next(i for i in result["items"] if i["provider"] == "openalex")
    assert item["configured"] is True
    assert item["source"] == "config"
    assert item["masked"] == "••••1234"
    assert "secret-key" not in str(result)

    path = tmp_path / "config" / "jw" / "config.yaml"
    assert path.exists()
    if os.name != "nt":
        # Windows reports pseudo-permission bits, so the owner-only mode cannot be
        # asserted there; save_config's chmod is best-effort by design.
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "secret-key-1234" in path.read_text(encoding="utf-8")


def test_environment_wins_over_config_and_is_marked_as_override(monkeypatch):
    update_credentials({"openalex": "config-key-1111"})
    monkeypatch.setenv("OPENALEX_API_KEY", "env-key-9999")

    assert credential_value("openalex") == "env-key-9999"
    item = next(i for i in credential_status() if i["provider"] == "openalex")
    assert item["source"] == "environment"
    assert item["env_override"] is True
    assert item["masked"] == "••••9999"


def test_email_is_validated_before_anything_is_written(tmp_path):
    result = update_credentials({"crossref": "not-an-email"})

    assert result["status"] == "error"
    assert "邮箱" in result["error"]
    assert not (tmp_path / "config" / "jw" / "config.yaml").exists()


def test_empty_value_clears_the_stored_secret():
    update_credentials({"ads": "token-123456"})
    result = update_credentials({"ads": ""})

    assert result["status"] == "ok"
    item = next(i for i in result["items"] if i["provider"] == "ads")
    assert item["configured"] is False
    assert item["source"] == ""


def test_provider_policy_reads_config_without_touching_the_environment(monkeypatch):
    update_credentials({"openalex": "config-key-1234", "crossref": "ops@example.org"})
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    policy = ProviderPolicy.from_environment()

    assert policy.credential("openalex") == "config-key-1234"
    assert policy.email == "ops@example.org"
    assert policy.headers() == {"From": "ops@example.org"}
    assert "OPENALEX_API_KEY" not in os.environ


def test_unknown_provider_is_rejected():
    result = update_credentials({"scihub": "nope"})

    assert result["status"] == "error"
    assert "未知来源" in result["error"]


def test_write_is_refused_for_a_remote_client(monkeypatch):
    monkeypatch.delenv("JW_CREDENTIALS_ALLOW_REMOTE", raising=False)
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))

    assert api._writes_allowed(request) is False

    monkeypatch.setenv("JW_CREDENTIALS_ALLOW_REMOTE", "1")
    assert api._writes_allowed(request) is True


def test_write_is_allowed_for_a_loopback_client(monkeypatch):
    monkeypatch.delenv("JW_CREDENTIALS_ALLOW_REMOTE", raising=False)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    assert api._writes_allowed(request) is True


def test_http_surface_saves_but_never_returns_the_secret():
    client = TestClient(app)

    saved = client.put(
        "/api/literature/credentials",
        json={"credentials": {"openalex": "http-secret-7788"}},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["status"] == "ok"
    assert "http-secret-7788" not in saved.text
    item = next(i for i in body["items"] if i["provider"] == "openalex")
    assert item["configured"] is True
    assert item["masked"] == "••••7788"

    listed = client.get("/api/literature/credentials")
    assert listed.status_code == 200
    assert "http-secret-7788" not in listed.text
    assert listed.json()["status"] == "ok"


def test_http_surface_rejects_an_invalid_payload():
    client = TestClient(app)

    response = client.put(
        "/api/literature/credentials",
        json={"credentials": {"openalex": 123}},
    )

    assert response.status_code == 400
    assert "字符串" in response.json()["error"]
