from __future__ import annotations

from pathlib import Path


def test_literature_bundle_exposes_retrieval_without_wiki_mutation_tools() -> None:
    import jw.tools.knowledge_base  # noqa: F401
    from jw.tools.registry import get_tool_bundles

    names = {tool.name for tool in get_tool_bundles()["knowledge-base-literature"]}
    assert {
        "lit_bind_task",
        "lit_search",
        "lit_fetch",
        "lit_bundle_build",
        "lit_bundle_read",
    } <= names
    assert not names.intersection(
        {"kb_propose", "kb_promote", "kb_deprecate", "lit_distill"}
    )


def test_solar_data_prompt_uses_only_read_only_literature_bundle_tools() -> None:
    prompt = (
        Path(__file__).resolve().parents[1] / "jw/subagents/solar/solar_data.yaml"
    ).read_text(encoding="utf-8")

    for tool_name in (
        "lit_bind_task",
        "lit_search",
        "lit_fetch",
        "lit_bundle_build",
        "lit_bundle_read",
    ):
        assert tool_name in prompt
    assert "lit_distill" not in prompt
