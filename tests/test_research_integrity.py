from __future__ import annotations

import json

import pytest

import jw.tools.research_integrity as integrity_tools
from jw.research_integrity import (
    accepted_evidence_receipts,
    derive_external_evidence_policy,
    finalize_task,
    normalize_tool_outcome,
    record_task_route,
    transition_task,
)


def _fetched_source(tmp_path, text: str) -> str:
    source = tmp_path / "work" / "evidence_sources" / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text(text, encoding="utf-8")
    receipt = tmp_path / "receipts" / "evidence" / "sources" / "source.json"
    receipt.parent.mkdir(parents=True)
    from jw.research_integrity import sha256_file

    receipt.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "fetched",
                "source_path": "work/evidence_sources/source.md",
                "source_sha256": sha256_file(source),
            }
        ),
        encoding="utf-8",
    )
    return "work/evidence_sources/source.md"


def test_business_error_is_not_transport_success() -> None:
    outcome = normalize_tool_outcome(
        json.dumps({"status": "error", "message": "provider unavailable"})
    )

    assert outcome.status == "error"
    assert not outcome.succeeded


def test_plain_specialist_result_is_not_success() -> None:
    outcome = normalize_tool_outcome("frozen")

    assert outcome.status == "error"
    assert outcome.error_code == "unstructured_tool_result"


def test_read_only_plain_result_may_succeed() -> None:
    outcome = normalize_tool_outcome("inputs/example.csv", allow_plain_success=True)

    assert outcome.status == "success"


def test_structured_success_keeps_artifacts_and_receipts() -> None:
    outcome = normalize_tool_outcome(
        {
            "status": "success",
            "summary": "verified",
            "artifact_refs": ["outputs/report.md"],
            "receipt_refs": ["receipts/experiment.json"],
        }
    )

    assert outcome.has_verified_receipt
    assert outcome.artifact_refs == ("outputs/report.md",)


def test_finalize_downgrades_without_report_or_receipts(tmp_path) -> None:
    (tmp_path / "task.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8"
    )

    task = finalize_task(
        tmp_path,
        requested_status="finalized",
        required_receipts=("receipts/experiment.json",),
    )

    assert task["status"] == "blocked"
    assert task["missing_receipts"] == ["receipts/experiment.json"]


def test_finalize_requires_verified_receipt_and_report(tmp_path) -> None:
    (tmp_path / "task.json").write_text(
        json.dumps({"status": "verifying"}), encoding="utf-8"
    )
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.md").write_text("# Report", encoding="utf-8")
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts" / "experiment.json").write_text(
        json.dumps({"status": "verified"}), encoding="utf-8"
    )

    task = finalize_task(
        tmp_path,
        requested_status="finalized",
        required_receipts=("receipts/experiment.json",),
    )

    assert task["status"] == "finalized"
    assert task["final_report"] == "outputs/report.md"


def test_terminal_task_cannot_be_reopened(tmp_path) -> None:
    (tmp_path / "task.json").write_text(
        json.dumps({"status": "finalized"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="terminal task status"):
        finalize_task(tmp_path, requested_status="error")

    task = transition_task(tmp_path, "running")
    assert task["status"] == "finalized"


def test_f107_route_requires_three_evidence_claims_and_all_receipt_kinds(
    tmp_path,
) -> None:
    (tmp_path / "task.json").write_text(
        json.dumps({"status": "created"}), encoding="utf-8"
    )
    record_task_route(
        tmp_path,
        {
            "mode": "verified_analysis",
            "source_mode": "mixed",
            "needs_computation": True,
            "requires_dataset_semantics": True,
            "requires_computation_receipt": True,
            "requires_external_evidence": True,
            "required_domain_adapter": "f107",
            "deliverable": "audited_report",
        },
    )
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.md").write_text("# Draft", encoding="utf-8")

    task = finalize_task(tmp_path, requested_status="finalized")

    assert task["status"] == "partial"
    assert "receipts/datasets/*.json" in task["missing_receipts"]
    assert "receipts/experiments/*.json" in task["missing_receipts"]
    assert "receipts/claims/*.json" in task["missing_receipts"]
    assert "evidence:f107_product_definition" in task["missing_receipts"]
    assert "evidence:f107_observatory_history" in task["missing_receipts"]
    assert "evidence:f107_1980_discontinuity" in task["missing_receipts"]


def test_local_literature_request_deterministically_requires_external_evidence() -> (
    None
):
    policy = derive_external_evidence_policy(
        "请使用本地数据，并查阅原始研究解释仪器校准变化",
        {
            "mode": "verified_analysis",
            "source_mode": "local",
            "needs_computation": True,
        },
        required_domain_adapter="none",
        deliverable="audited_report",
    )

    assert policy["requires_external_evidence"] is True
    assert "explicit_literature_request" in policy["external_evidence_reasons"]
    assert "causal_attribution" in policy["external_evidence_reasons"]


def test_analysis_year_range_alone_is_not_historical_evidence_request() -> None:
    policy = derive_external_evidence_policy(
        "计算 1947-2015 年的月均相关系数",
        {
            "mode": "verified_analysis",
            "source_mode": "local",
            "needs_computation": True,
        },
        required_domain_adapter="none",
        deliverable="audited_report",
    )

    assert policy["requires_external_evidence"] is False
    assert "historical_fact" not in policy["external_evidence_reasons"]


def test_f107_domain_policy_requires_fixed_claims_even_for_local_route() -> None:
    policy = derive_external_evidence_policy(
        "分析 F10.7 与太阳黑子数",
        {
            "mode": "verified_analysis",
            "source_mode": "local",
            "needs_computation": True,
        },
        required_domain_adapter="f107",
        deliverable="audited_report",
    )

    assert policy["requires_external_evidence"] is True
    assert [row["claim_id"] for row in policy["required_evidence_claims"]] == [
        "f107_product_definition",
        "f107_observatory_history",
        "f107_1980_discontinuity",
    ]


def test_v2_finalizer_ignores_legacy_verified_evidence(tmp_path) -> None:
    (tmp_path / "task.json").write_text(
        json.dumps(
            {
                "status": "verifying",
                "evidence_schema_version": 2,
                "required_receipt_kinds": ["evidence"],
                "required_evidence_claims": ["claim_history"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.md").write_text("# Report", encoding="utf-8")
    legacy = tmp_path / "receipts" / "evidence"
    legacy.mkdir(parents=True)
    (legacy / "claim_history.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_status": "verified",
                "claim_id": "claim_history",
            }
        ),
        encoding="utf-8",
    )

    task = finalize_task(tmp_path, requested_status="finalized")

    assert task["status"] == "partial"
    assert "evidence:claim_history" in task["missing_receipts"]
    assert accepted_evidence_receipts(tmp_path) == {}


def test_evidence_submission_is_pending_and_multiple_sources_do_not_overwrite(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        integrity_tools, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        integrity_tools,
        "resolve_scoped_path",
        lambda value, _config: tmp_path / str(value).lstrip("/"),
    )
    source_path = _fetched_source(
        tmp_path,
        "Official history states that the observatory moved in 1991. "
        "This paragraph is long enough for an auditable source.",
    )
    common = {
        "source_id": "official-history",
        "source_path": source_path,
        "source_url": "https://example.test/history",
        "source_class": "official",
        "locator_type": "section_paragraph",
        "locator_value": "History paragraph 1",
        "claim_id": "claim_history",
        "claim_text": "The observatory move occurred in 1991.",
        "relation": "supports",
        "scope": "Dates the observatory move only.",
        "confidence_limit": "Does not explain an earlier discontinuity.",
        "doi": "",
        "config": None,
    }
    first = json.loads(
        integrity_tools.submit_evidence_receipt.func(
            evidence_span="the observatory moved in 1991", **common
        )
    )
    second = json.loads(
        integrity_tools.submit_evidence_receipt.func(
            evidence_span="Official history states that the observatory moved in 1991",
            **common,
        )
    )

    assert first["submission_status"] == "pending"
    assert second["submission_status"] == "pending"
    assert first["receipt_id"] != second["receipt_id"]
    assert len(list((tmp_path / "receipts/evidence/submissions").rglob("*.json"))) == 2


def test_local_or_search_text_without_fetch_receipt_cannot_be_submitted(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        integrity_tools, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        integrity_tools,
        "resolve_scoped_path",
        lambda value, _config: tmp_path / str(value).lstrip("/"),
    )
    source = tmp_path / "work" / "evidence_sources" / "manual.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "A manually copied search snippet is not formal evidence.", encoding="utf-8"
    )

    result = json.loads(
        integrity_tools.submit_evidence_receipt.func(
            source_id="search-hit",
            source_path="work/evidence_sources/manual.md",
            source_url="https://example.test",
            source_class="secondary",
            locator_type="section_paragraph",
            locator_value="result",
            evidence_span="manually copied search snippet",
            claim_id="claim",
            claim_text="A historical event occurred at a stated date.",
            relation="supports",
            scope="Search result only.",
            confidence_limit="None.",
            config=None,
        )
    )

    assert result["status"] == "error"
    assert "formal fetched-source receipt" in result["summary"]


def test_only_independently_accepted_v2_evidence_satisfies_historical_claim(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        integrity_tools, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        integrity_tools,
        "resolve_scoped_path",
        lambda value, _config: tmp_path / str(value).lstrip("/"),
    )
    (tmp_path / "task.json").write_text(
        json.dumps(
            {
                "status": "verifying",
                "evidence_schema_version": 2,
                "required_evidence_claims": ["claim_history"],
            }
        ),
        encoding="utf-8",
    )
    source_path = _fetched_source(
        tmp_path,
        "Official observatory history confirms that the move occurred in 1991. "
        "The source does not attribute any discontinuity to that move.",
    )
    claim_text = "The observatory move occurred in 1991."
    submitted = json.loads(
        integrity_tools.submit_evidence_receipt.func(
            source_id="official-history",
            source_path=source_path,
            source_url="https://example.test/history",
            source_class="official",
            locator_type="section_paragraph",
            locator_value="History paragraph 1",
            evidence_span="the move occurred in 1991",
            claim_id="claim_history",
            claim_text=claim_text,
            relation="supports",
            scope="Dates the move only.",
            confidence_limit="Does not explain another event.",
            config=None,
        )
    )
    claim = {
        "claim_id": "claim_history",
        "kind": "historical",
        "text": claim_text,
        "evidence_ids": [submitted["receipt_id"]],
    }

    pending = json.loads(
        integrity_tools.validate_research_claims.func([claim], config=None)
    )
    assert pending["status"] == "blocked"

    reviewed = json.loads(
        integrity_tools.review_evidence_receipt.func(
            submission_ref=submitted["receipt_refs"][0],
            review_status="accepted",
            claim_relation_valid=True,
            source_class_valid=True,
            scope_valid=True,
            review_notes="The official source directly dates the move and makes no causal claim.",
            config=None,
        )
    )
    assert reviewed["review_status"] == "accepted"

    accepted = json.loads(
        integrity_tools.validate_research_claims.func([claim], config=None)
    )
    assert accepted["status"] == "success"
    assert accepted["receipt_refs"] == ["receipts/claims/claims-v2.json"]
