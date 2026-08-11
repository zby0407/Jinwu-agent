"""Tests for ``JW.utils.load_subagents``.

Focused on schema-validation paths that are easy to silently misuse from
yaml — primarily the ``async:`` flag type check that prevents quoted-string
or integer values from being misinterpreted as booleans.
"""

from __future__ import annotations

import textwrap

import pytest

from jw.utils import load_subagents


def _write_yaml(tmp_path, name: str, body: str):
    """Write ``body`` to ``tmp_path/name`` and return the directory path."""
    (tmp_path / name).write_text(textwrap.dedent(body))
    return tmp_path


def test_async_flag_accepts_real_bool(tmp_path):
    """``async: true`` (real yaml boolean) is accepted and carried through."""
    config_path = _write_yaml(
        tmp_path,
        "writing.yaml",
        """
        writing-agent:
          description: Drafts reports
          system_prompt: ""
          tools: []
          async: true
        """,
    )
    subs = load_subagents(config_path, tool_registry={})
    assert len(subs) == 1
    assert subs[0]["name"] == "writing-agent"
    assert subs[0]["_async"] is True


def test_async_flag_defaults_to_false_when_omitted(tmp_path):
    """No ``async:`` field → ``_async`` defaults to False."""
    config_path = _write_yaml(
        tmp_path,
        "planner.yaml",
        """
        planner-agent:
          description: Plans experiments
          system_prompt: ""
          tools: []
        """,
    )
    subs = load_subagents(config_path, tool_registry={})
    assert subs[0]["_async"] is False


def test_async_flag_rejects_quoted_string(tmp_path):
    """``async: "false"`` (quoted) is a real user trap — bool("false") is True.

    Without the explicit isinstance check, this would silently flip the agent
    into async mode. We require the validator to fail loud instead.
    """
    config_path = _write_yaml(
        tmp_path,
        "bad.yaml",
        """
        bad-agent:
          description: ""
          system_prompt: ""
          tools: []
          async: "false"
        """,
    )
    with pytest.raises(ValueError, match=r"'async' must be a boolean"):
        load_subagents(config_path, tool_registry={})


def test_async_flag_rejects_integer(tmp_path):
    """``async: 1`` is also rejected — yaml integers are not booleans."""
    config_path = _write_yaml(
        tmp_path,
        "bad.yaml",
        """
        bad-agent:
          description: ""
          system_prompt: ""
          tools: []
          async: 1
        """,
    )
    with pytest.raises(ValueError, match=r"'async' must be a boolean"):
        load_subagents(config_path, tool_registry={})


def test_async_flag_error_includes_agent_name(tmp_path):
    """Error message must include the offending agent name for triage."""
    config_path = _write_yaml(
        tmp_path,
        "bad.yaml",
        """
        my-bad-agent:
          description: ""
          system_prompt: ""
          tools: []
          async: "yes"
        """,
    )
    with pytest.raises(ValueError, match=r"my-bad-agent"):
        load_subagents(config_path, tool_registry={})


def test_model_call_limit_accepts_positive_integer(tmp_path):
    """A specialist may request a bounded per-agent model-call budget."""
    config_path = _write_yaml(
        tmp_path,
        "hypothesis.yaml",
        """
        hypothesis-agent:
          description: ""
          system_prompt: ""
          tools: []
          model_call_limit: 32
        """,
    )

    subagent = load_subagents(config_path, tool_registry={})[0]

    assert subagent["_model_call_limit"] == 32


@pytest.mark.parametrize("invalid_value", ["true", "0", "-1", '"32"'])
def test_model_call_limit_rejects_invalid_values(tmp_path, invalid_value):
    """The override must be an unquoted positive integer, never a bool/string."""
    config_path = _write_yaml(
        tmp_path,
        "bad-limit.yaml",
        f"""
        hypothesis-agent:
          description: ""
          system_prompt: ""
          tools: []
          model_call_limit: {invalid_value}
        """,
    )

    with pytest.raises(
        ValueError,
        match=r"hypothesis-agent.*'model_call_limit' must be a positive integer",
    ):
        load_subagents(config_path, tool_registry={})


def test_non_dict_spec_raises(tmp_path):
    """Yaml entries that aren't mappings must fail loud, not be silently dropped.

    Previously ``_build_one`` had a ``if not isinstance(spec, dict): continue``
    fallback that swallowed malformed entries — users would see their agent
    quietly disappear with no error. Now caught during the merge loop.
    """
    config_path = _write_yaml(
        tmp_path,
        "bad.yaml",
        """
        bad-agent: 123
        """,
    )
    with pytest.raises(ValueError, match=r"must map to a spec dict"):
        load_subagents(config_path, tool_registry={})


def test_non_dict_spec_error_includes_filename_and_name(tmp_path):
    """Error must surface BOTH the offending file path and agent name."""
    config_path = _write_yaml(
        tmp_path,
        "weird.yaml",
        """
        weird-agent: "just a string"
        """,
    )
    with pytest.raises(ValueError, match=r"weird\.yaml.*weird-agent"):
        load_subagents(config_path, tool_registry={})


def test_tool_bundle_expands_without_copying_individual_names(tmp_path):
    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    first = _Tool("first_tool")
    second = _Tool("second_tool")
    config_path = _write_yaml(
        tmp_path,
        "bundled.yaml",
        """
        bundled-agent:
          description: Uses one capability
          system_prompt: ""
          tool_bundles: [example-capability]
          restrict_tools: true
        """,
    )

    subagent = load_subagents(
        config_path,
        tool_registry={},
        tool_bundles={"example-capability": (first, second)},
    )[0]

    assert subagent["tools"] == [first, second]
    assert subagent["_restrict_tools"] is True


def test_unknown_tool_bundle_fails_loud(tmp_path):
    config_path = _write_yaml(
        tmp_path,
        "unknown.yaml",
        """
        broken-agent:
          description: ""
          system_prompt: ""
          tool_bundles: [missing-capability]
        """,
    )

    with pytest.raises(ValueError, match=r"unknown tool bundle 'missing-capability'"):
        load_subagents(config_path, tool_registry={}, tool_bundles={})


def _evidence_yaml(tmp_path):
    return _write_yaml(
        tmp_path,
        "solar_evidence.yaml",
        """
        solar-evidence:
          description: "reviewer"
          tool_bundles: [reasoning]
          system_prompt: |
            Base closed-pass contract.
        """,
    )


def test_solar_evidence_two_pass_adds_falsification_and_web_search(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    reasoning = type("T", (), {"name": "think_tool"})()
    web = type("T", (), {"name": "tavily_search"})()
    subs = load_subagents(
        _evidence_yaml(tmp_path),
        tool_registry={},
        tool_bundles={"reasoning": [reasoning], "web-search": [web]},
    )
    ev = subs[0]
    assert "active-falsification" in ev["system_prompt"]
    assert "assessment_review_mode=two_pass" in ev["system_prompt"]
    assert "tavily_search" in {t.name for t in ev["tools"]}


def test_solar_evidence_closed_omits_falsification_and_web_search(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "closed")
    reasoning = type("T", (), {"name": "think_tool"})()
    web = type("T", (), {"name": "tavily_search"})()
    subs = load_subagents(
        _evidence_yaml(tmp_path),
        tool_registry={},
        tool_bundles={"reasoning": [reasoning], "web-search": [web]},
    )
    ev = subs[0]
    assert "active-falsification" not in ev["system_prompt"]
    assert "assessment_review_mode=closed" in ev["system_prompt"]
    assert "tavily_search" not in {t.name for t in ev["tools"]}


def test_solar_evidence_defaults_to_two_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("JW_EVIDENCE_REVIEW_MODE", raising=False)
    reasoning = type("T", (), {"name": "think_tool"})()
    web = type("T", (), {"name": "tavily_search"})()
    subs = load_subagents(
        _evidence_yaml(tmp_path),
        tool_registry={},
        tool_bundles={"reasoning": [reasoning], "web-search": [web]},
    )
    assert "active-falsification" in subs[0]["system_prompt"]
