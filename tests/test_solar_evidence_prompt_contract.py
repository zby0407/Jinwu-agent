from __future__ import annotations

from pathlib import Path


def test_evidence_prompt_spells_out_retry_repairs_for_atomic_round() -> None:
    text = Path("jw/subagents/solar/solar_evidence.yaml").read_text(encoding="utf-8")
    assert "gap 的 source_ref 必须写 null" in text
    assert "accepted_claims 必须列出" in text
    assert (
        "release_candidate 只能在 conclusion_cap 同为 release_candidate 时使用" in text
    )


def test_evidence_prompt_caps_experiment_design_before_execution() -> None:
    text = Path("jw/subagents/solar/solar_evidence.yaml").read_text(encoding="utf-8")
    assert "experiment_design 阶段还没有真实实验结果" in text
    assert "quality_status 和 conclusion_cap 都不得使用 release_candidate" in text


def test_final_release_review_owns_semantic_reader_quality() -> None:
    text = Path("jw/subagents/solar/solar_evidence.yaml").read_text(encoding="utf-8")
    assert "semantic coverage, not verbatim string matching" in text
    assert "raw JSON, internal IDs, hashes, debug records, or workflow status" in text
