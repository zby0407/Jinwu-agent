from __future__ import annotations

import json
from pathlib import Path

from jw.tools import (
    SCIENTIFIC_HYPOTHESIS_TOOLS,
    get_builtin_tool_registry,
    get_main_agent_tools,
    get_tool_bundles,
    knowledge_base,
    research_planner,
)
from jw.utils import load_subagents

ROOT = Path(__file__).resolve().parents[1]


def _tool_name(tool: object) -> str:
    value = getattr(tool, "name", None)
    assert isinstance(value, str)
    return value


def test_main_and_subagent_registry_share_hypothesis_tools() -> None:
    registry = get_builtin_tool_registry()
    bundles = get_tool_bundles()
    main_tool_names = [_tool_name(tool) for tool in get_main_agent_tools()]
    hypothesis_tool_names = [_tool_name(tool) for tool in SCIENTIFIC_HYPOTHESIS_TOOLS]

    assert len(main_tool_names) == len(set(main_tool_names))
    assert [_tool_name(tool) for tool in bundles["scientific-hypothesis"]] == (
        hypothesis_tool_names
    )
    assert set(hypothesis_tool_names).issubset(registry)
    assert set(hypothesis_tool_names).issubset(main_tool_names)
    assert "scientific_hypothesis_get_status" in hypothesis_tool_names


def test_yaml_subagent_resolves_tools_from_canonical_registry() -> None:
    registry = get_builtin_tool_registry()
    specs = load_subagents(
        ROOT / "jw" / "subagents",
        tool_registry=registry,
        tool_bundles=get_tool_bundles(),
    )
    hypothesis = next(spec for spec in specs if spec["name"] == "solar-hypothesis")
    evidence = next(spec for spec in specs if spec["name"] == "solar-evidence")

    hypothesis_contract_tools = {
        "scientific_hypothesis_bind_request",
        "scientific_hypothesis_bind_evidence",
        "scientific_hypothesis_bind_wiki_evidence",
        "scientific_hypothesis_build_literature_bundle",
        "scientific_hypothesis_bind_literature_evidence",
        "scientific_hypothesis_update_draft",
        "scientific_hypothesis_get_draft",
        "scientific_hypothesis_validate_response",
        "scientific_hypothesis_checkpoint_draft",
        "scientific_hypothesis_get_status",
        "scientific_hypothesis_freeze",
    }
    assert {
        *hypothesis_contract_tools,
        "kb_query",
        "kb_read",
        "lit_bundle_build",
        "lit_bundle_read",
    }.issubset(_tool_name(tool) for tool in hypothesis["tools"])
    assert hypothesis_contract_tools.issubset(
        _tool_name(tool) for tool in evidence["tools"]
    )
    assert hypothesis["_restrict_tools"] is True
    assert {
        "kb_propose",
        "kb_promote",
        "kb_deprecate",
        "kb_review_decide",
        "kb_import",
        "lit_fetch",
        "lit_distill",
    }.isdisjoint(_tool_name(tool) for tool in hypothesis["tools"])
    hypothesis_yaml = (
        ROOT / "jw" / "subagents" / "solar" / "solar_hypothesis.yaml"
    ).read_text(encoding="utf-8")
    assert not any(
        line.strip().startswith("tools:") for line in hypothesis_yaml.splitlines()
    )


def test_tool_modules_resolve_project_root_from_their_location() -> None:
    assert knowledge_base._PROJECT_ROOT == ROOT
    assert research_planner._PROJECT_ROOT == ROOT


def test_kb_query_delegates_to_readonly_search_service(monkeypatch) -> None:
    store = object()
    seen: dict[str, object] = {}

    def fake_search(selected_store, query, **filters):
        seen.update(store=selected_store, query=query, filters=filters)
        return {"status": "ok", "results": [{"entry_id": "kb_test_001"}]}

    monkeypatch.setattr(knowledge_base, "_get_store", lambda: store)
    monkeypatch.setattr(knowledge_base.service, "search", fake_search)

    result = json.loads(
        knowledge_base.kb_query.invoke(
            {
                "query": "polar field",
                "type": "mechanism",
                "status": "canonical",
                "limit": 3,
            }
        )
    )

    assert result["results"][0]["entry_id"] == "kb_test_001"
    assert seen == {
        "store": store,
        "query": "polar field",
        "filters": {
            "entry_type": "mechanism",
            "status": "canonical",
            "confidence": "",
            "valid_range": "",
            "limit": 3,
        },
    }
