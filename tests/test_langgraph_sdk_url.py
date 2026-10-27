"""Tests for the local LangGraph SDK URL contract."""

from __future__ import annotations

from types import SimpleNamespace

from jw.langgraph_dev.sdk import langgraph_dev_url


def test_langgraph_url_uses_loopback_ipv4() -> None:
    assert langgraph_dev_url(port=6174) == "http://127.0.0.1:6174"


def test_langgraph_url_reads_config() -> None:
    config = SimpleNamespace(langgraph_dev_port=6321)

    assert langgraph_dev_url(config) == "http://127.0.0.1:6321"
