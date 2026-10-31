import csv
import json

import pytest


class PublicSources:
    def __init__(self, *, conflict=False, fail=False):
        self.calls = []
        self.conflict = conflict
        self.fail = fail

    def fetch(self, url, *, policy):
        self.calls.append(url)
        if self.fail:
            return 503, b"unavailable", "text/plain"
        if "sidc.be" in url:
            text = "2009 12 2009.958 10.5 1.0 20\n2010 01 2010.042 19.5 2.0 30\n2010 02 2010.123 -1 -1 0\n"
            if self.conflict:
                text += "2010 01 2010.042 99.0 2.0 30\n"
            return 200, text.encode(), "text/plain"
        rows = [
            {"time-tag": "2010-01", "f10.7": 81.31, "smoothed_f10.7": 999},
            {"time-tag": "2010-02", "f10.7": -1},
        ]
        return 200, json.dumps(rows).encode(), "application/json"


def test_fetch_align_preserve_missing_and_replay(tmp_path):
    from jw.scientific_data.solar_monthly import integrate_solar_monthly, replay

    fetcher = PublicSources()
    result = integrate_solar_monthly(
        tmp_path / "run", "2010-01", "2010-03", allow_network=True, fetcher=fetcher
    )
    assert len(fetcher.calls) == 2
    assert result["status"] == "completed_with_missing"
    assert result["rows"] == 3
    with (tmp_path / "run/merged_monthly.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["sunspot_number"] == "19.5"
    assert rows[0]["f107_sfu"] == "81.31"
    assert rows[1]["sunspot_number"] == rows[1]["f107_sfu"] == ""
    assert rows[1]["sunspot_missing_reason"] == "source_sentinel"
    assert rows[2]["sunspot_missing_reason"] == "month_absent"
    assert rows[0]["sunspot_locator"] == "line:2"
    assert rows[0]["f107_locator"] == "json:0"
    replay(tmp_path / "run", tmp_path / "replay")
    assert (tmp_path / "replay/merged_monthly.csv").read_bytes() == (
        tmp_path / "run/merged_monthly.csv"
    ).read_bytes()


def test_network_denied_without_authorization(tmp_path):
    from jw.scientific_data.solar_monthly import integrate_solar_monthly

    fetcher = PublicSources()
    result = integrate_solar_monthly(tmp_path, "2010-01", "2010-03", fetcher=fetcher)
    assert result["status"] == "acquisition_failed"
    assert not fetcher.calls
    assert not (tmp_path / "merged_monthly.csv").exists()


def test_monthly_tool_returns_terminal_factual_receipt(tmp_path, monkeypatch):
    from jw.tools import scientific_data as tools

    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    assert tools.sci_data_solar_monthly.return_direct
    command = tools.sci_data_solar_monthly.invoke(
        {
            "name": "sci_data_solar_monthly",
            "type": "tool_call",
            "id": "monthly-1",
            "args": {"start_month": "2010-01", "end_month": "2010-03"},
        }
    )
    result = command.update["structured_response"]
    assert result["kind"] == "solar_monthly_integration"
    assert result["status"] == "acquisition_failed"
    assert "未完成" in result["summary"]


def test_download_failure_is_not_empty_success(tmp_path):
    from jw.scientific_data.solar_monthly import integrate_solar_monthly

    result = integrate_solar_monthly(
        tmp_path,
        "2010-01",
        "2010-03",
        allow_network=True,
        fetcher=PublicSources(fail=True),
    )
    assert result["status"] == "acquisition_failed"
    assert not (tmp_path / "merged_monthly.csv").exists()
    assert (tmp_path / "acquisition.json").is_file()


def test_terminal_receipt_survives_deepagents_delegation(tmp_path, monkeypatch):
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from jw.tools import scientific_data as tools

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    specialist = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sci_data_solar_monthly",
                        "id": "monthly-graph",
                        "args": {"start_month": "2010-01", "end_month": "2010-03"},
                    }
                ],
            )
        ]
    )
    parent = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "id": "delegate-graph",
                        "args": {
                            "subagent_type": "scientific-data",
                            "description": "monthly data",
                        },
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_deep_agent(
        model=parent,
        subagents=[
            {
                "name": "scientific-data",
                "description": "monthly integration",
                "system_prompt": "integrate",
                "model": specialist,
                "tools": [tools.sci_data_solar_monthly],
            }
        ],
    )
    state = graph.invoke({"messages": [HumanMessage("integrate")]})
    message = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    receipt = json.loads(message.content)
    assert receipt["kind"] == "solar_monthly_integration"
    assert receipt["status"] == "acquisition_failed"
    assert "未完成" in receipt["summary"]


def test_conflicting_month_is_not_silently_selected(tmp_path):
    from jw.scientific_data.solar_monthly import integrate_solar_monthly

    with pytest.raises(ValueError, match="Conflicting duplicate"):
        integrate_solar_monthly(
            tmp_path,
            "2010-01",
            "2010-03",
            allow_network=True,
            fetcher=PublicSources(conflict=True),
        )
    assert not (tmp_path / "merged_monthly.csv").exists()


@pytest.mark.parametrize(
    ("start", "end"), [("2010-13", "2011-01"), ("2011-01", "2010-01")]
)
def test_invalid_range_rejected_before_network(tmp_path, start, end):
    from jw.scientific_data.solar_monthly import integrate_solar_monthly

    fetcher = PublicSources()
    with pytest.raises(ValueError, match=r"unconverted data remains|precedes"):
        integrate_solar_monthly(
            tmp_path, start, end, allow_network=True, fetcher=fetcher
        )
    assert not fetcher.calls
