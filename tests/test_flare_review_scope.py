"""Independent Data receives the source basis; negative decisions still stop."""

import hashlib
import json

import pytest

from jw.flare_forecast.native_data import (
    LIVE_REVIEW_DECISION_CONTRACT,
    quality_policy_input,
)
from jw.tools.flare_forecast import render_delivery


def source_contract(tmp_path):
    document = {
        "accepted_words": [1024],
        "review_authorship": {"operator_review_not_JW_result": True},
        "required_limits": ["not harmless", "not operationally validated"],
        "verified_code_chain": [{"exact_lines": {"44": "#define Q_NRT 0x40000000"}}],
    }
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    binding = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return (
        document,
        path,
        {
            "quality_policy_sha256": binding["sha256"],
            "quality_decision_file": dict(binding),
            "live_source": {
                "source_policy_proofs": {"quality_source_decision": dict(binding)}
            },
        },
    )


def test_complete_source_policy_preserves_original_authorship_limits_and_namespace(
    tmp_path,
):
    document, path, contract = source_contract(tmp_path)
    value = quality_policy_input(contract)
    assert value["document"] == document
    assert value["bytes"] == path.stat().st_size
    assert (
        value["document"]["review_authorship"]["operator_review_not_JW_result"] is True
    )
    assert "Q_NRT" in value["document"]["verified_code_chain"][0]["exact_lines"]["44"]
    assert value["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "binding",
    ["quality_policy_sha256", "quality_decision_file", "source_proof", "source_bytes"],
)
def test_policy_mismatches_are_rejected_before_provider_review(tmp_path, binding):
    _, path, contract = source_contract(tmp_path)
    if binding == "quality_policy_sha256":
        contract[binding] = "wrong"
    elif binding == "quality_decision_file":
        contract[binding]["sha256"] = "wrong"
    elif binding == "source_proof":
        contract["live_source"]["source_policy_proofs"]["quality_source_decision"][
            "sha256"
        ] = "wrong"
    else:
        path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bindings differ"):
        quality_policy_input(contract)


def test_live_contract_retains_rejection_and_does_not_preset_acceptance():
    text = json.dumps(LIVE_REVIEW_DECISION_CONTRACT)
    assert "not a preset independent acceptance" in text
    assert "Never downgrade a genuine concern" in text
    assert "source-policy basis" in text
    assert "bit30" in text and "bit31" in text
    assert "not_reviewed" in text
    assert "decision" not in LIVE_REVIEW_DECISION_CONTRACT


def test_actual_negative_review_reasons_and_original_path_reach_reader():
    result = {
        "status": "not_issued",
        "message": "本次未签发",
        "data_review_decision": "revise",
        "data_review_reasons": [
            "New version literal missing source basis",
            "Current patch input is incomplete",
        ],
        "data_review_path": "work/native/data_review.json",
        "failure_path": "work/failure.json",
    }
    text = render_delivery(result)
    assert "revise" in text
    assert all(reason in text for reason in result["data_review_reasons"])
    assert result["data_review_path"] in text
    assert "已签发" not in text
