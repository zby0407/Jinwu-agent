"""Report field coverage, release rejection and renderer fallback regressions."""

from __future__ import annotations

import json
import re

import pytest

from automatic_experiment import reporting
from jw.prompts import REPORT_TEMPLATE, get_system_prompt
from jw.report_contract import report_sections, validate_report_fields
from jw.research_review import ResearchReviewStore


COMPLETE_REPORT = """# A bounded investigation of an unresolved observation

## Paper Abstract
Background: the observation is unresolved. Methods: a controlled comparison is
planned. Expected results: distinguish competing explanations. No result exists yet.

## Problem Statement
Existing observations do not distinguish the proposed explanations.

## Rationale
Compare their distinct observable consequences without asserting either is true.

## Technical Details
The proposed statistic and its uncertainty method still require registration.

## Datasets
No dataset has been qualified in this synthetic report-contract fixture.

### Source
Historical observations are unavailable; no data-based claim is made.

### Target
Future paired observations need a frozen coverage and sampling specification.

## Methods
Freeze the protocol before acquisition and keep exploration separate from validation.

## Experiments
A controlled comparison is proposed and has not been executed.

### Baselines
The comparison baseline is not registered, so superiority cannot be claimed.

### Metrics
The evaluation statistic and uncertainty procedure have not been frozen.

## Results
Not executed: this fixture has neither measurements nor a feasibility conclusion.

## References
No verifiable paper has been bound; authors, dates and DOIs are not invented.
"""


def test_required_fields_reach_report_generation():
    fields = (
        "Problem Statement",
        "Rationale",
        "Technical Details",
        "Datasets",
        "Source",
        "Target",
        "Paper Title",
        "Paper Abstract",
        "Methods",
        "Experiments",
        "Baselines",
        "Metrics",
        "Results",
        "References",
    )
    for text in (
        REPORT_TEMPLATE,
        get_system_prompt(),
    ):
        assert all(field in text for field in fields)
    validate_report_fields(COMPLETE_REPORT)


@pytest.mark.parametrize(
    "heading",
    [
        "Paper Abstract",
        "Problem Statement",
        "Rationale",
        "Technical Details",
        "Datasets",
        "Source",
        "Target",
        "Methods",
        "Experiments",
        "Baselines",
        "Metrics",
        "Results",
        "References",
    ],
)
def test_omitted_field_is_rejected(heading):
    draft = re.sub(rf"(?m)^#{{2,3}} {re.escape(heading)}\n", "", COMPLETE_REPORT)
    with pytest.raises(ValueError, match=re.escape(heading)):
        validate_report_fields(draft)


@pytest.mark.parametrize("placeholder", ["", "TBD", "N/A", "待补充", "不适用"])
def test_empty_or_placeholder_content_is_rejected(placeholder):
    draft = COMPLETE_REPORT.replace(
        "The evaluation statistic and uncertainty procedure have not been frozen.",
        placeholder,
    )
    with pytest.raises(ValueError, match="Metrics"):
        validate_report_fields(draft)


def test_title_and_subfield_parent_are_required():
    with pytest.raises(ValueError, match="Paper Title"):
        validate_report_fields(COMPLETE_REPORT.split("\n", 1)[1])
    draft = COMPLETE_REPORT.replace("### Source", "## Source")
    with pytest.raises(ValueError, match="Source"):
        validate_report_fields(draft)
    with pytest.raises(ValueError, match="Paper Title"):
        validate_report_fields("# Paper Title\n" + COMPLETE_REPORT.split("\n", 1)[1])


def test_headings_in_code_or_comments_cannot_supply_report_fields():
    for fake in (
        f"```markdown\n{COMPLETE_REPORT}\n```",
        f"<!--\n{COMPLETE_REPORT}\n-->",
    ):
        with pytest.raises(ValueError, match="Problem Statement"):
            validate_report_fields("# Real report\n\n" + fake)


@pytest.mark.parametrize(
    "outcome",
    [
        "completed_interpretable",
        "partial_result",
        "input_missing",
        "technical_failure",
        "cancelled_by_user",
        "boundary_blocked",
    ],
)
def test_native_report_covers_fields_even_without_results(outcome):
    record = {
        "task": "检验两组观测的差异",
        "outcome": outcome,
        "worker_result": None,
        "outcome_reason": "必要输入尚未完整提供，因此没有形成可解释的结果。",
    }
    report = reporting.render_report(record)
    validate_report_fields(report)
    sections = report_sections(report)
    assert "尚未" in sections["target"] or "未单独登记" in sections["target"]
    assert "未登记" in sections["references"]
    assert "不能" in sections["results"]


def test_native_report_preserves_verified_values_methods_and_extra_sections():
    body = "# 受限的观测比较\n\n## 摘要\n\n已测差值为 2.5。\n\n## 数据与方法\n\n按既定公式计算差值。\n\n## 结果\n\n实际差值为 2.5。\n\n```text\nverified value = 2.5\n```\n\n## 局限性\n\n不外推到其他总体。\n"
    design = {
        "research_frame": {
            "primary_question": "两组观测是否存在差异？",
            "literature_basis": "未引用外部论文。",
        },
        "method_decisions": [
            {"decision": "计算配对差值", "rationale": "配对观测共享测量条件"}
        ],
        "measurement_plan": [
            {
                "name": "difference",
                "display_name": "配对差值",
                "scientific_meaning": "同一对象的两次测量之差",
            }
        ],
        "paired_comparison_audits": [{"baseline_measurement": "difference"}],
    }
    report = reporting._with_required_report_fields(body, {"task": "比较观测"}, design)
    validate_report_fields(report)
    assert "实际差值为 2.5。" in report
    assert "verified value = 2.5" in report
    assert "按既定公式计算差值。" in report
    assert "不外推到其他总体。" in report
    assert "配对观测共享测量条件" in report_sections(report)["rationale"]
    assert "配对差值" in report_sections(report)["baselines"]


def test_emergency_finalization_keeps_required_fields(tmp_path, monkeypatch):
    record = {
        "run_id": "fixture",
        "task": "尚无可解释结果的计算",
        "outcome": "technical_failure",
        "worker_result": None,
        "replay": {"pi_command": "unused fixture command"},
    }
    (tmp_path / "record.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(reporting, "generate_report_assets", lambda *a: ([], {}))
    monkeypatch.setattr(reporting, "render_audit", lambda *a, **k: "fixture audit")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(reporting, "render_report", fail)
    entry = reporting.finalize_report(tmp_path, record)
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    validate_report_fields(report)
    assert entry["user_display_markdown"] == report
    assert "synthetic render failure" not in report


def test_release_rejects_incomplete_draft_before_creating_artifact(
    tmp_path, monkeypatch
):
    store = ResearchReviewStore(tmp_path, "report-contract-fixture")
    integration = {
        "artifact_id": "integration-artifact",
        "version": 1,
        "artifact_sha256": "a" * 64,
        "claims": [{"claim_id": "accepted-fixture"}],
    }
    monkeypatch.setattr(store, "latest_artifact", lambda mode: integration)
    monkeypatch.setattr(
        store,
        "matching_verdict",
        lambda *a: {
            "decision": "accept",
            "accepted_claims": ["accepted-fixture"],
            "carry_forward_limits": [],
        },
    )
    with pytest.raises(ValueError, match="Problem Statement"):
        store.prepare_release(
            "# Incomplete report\n\nNo scientific result exists.",
            [
                {
                    "claim_id": "accepted-fixture",
                    "draft_excerpt": "No scientific result exists.",
                }
            ],
        )
    assert not (store.root / "artifacts/final_release-artifact").exists()
