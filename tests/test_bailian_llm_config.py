import pytest

from jw.solar_agent_src.bailian_llm import DEFAULT_BASE_URL, load_bailian_config


@pytest.fixture(autouse=True)
def _ignore_local_dotenv(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_args, **_kwargs: False)


def test_bailian_uses_shared_dashscope_base_url(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://proxy.example/v1")
    monkeypatch.delenv("BAILIAN_BASE_URL", raising=False)

    assert load_bailian_config()["base_url"] == "https://proxy.example/v1"


def test_bailian_specific_base_url_has_priority(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("BAILIAN_BASE_URL", "https://bailian.example/v1")

    assert load_bailian_config()["base_url"] == "https://bailian.example/v1"


def test_bailian_standard_default(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("BAILIAN_BASE_URL", raising=False)

    assert load_bailian_config()["base_url"] == DEFAULT_BASE_URL
