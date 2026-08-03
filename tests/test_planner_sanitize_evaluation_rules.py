"""Sanitizer must coerce mechanical criterion_basis violations to schema-valid form."""

from jw.tools.research_planner import _sanitize_evaluation_rules


def _basis(kind, sources, artifacts):
    return {
        "criterion": "c",
        "criterion_basis": {
            "kind": kind,
            "basis_text": "t",
            "evidence_source_ids": sources,
            "artifact_ids": artifacts,
        },
    }


def test_alias_kinds_are_normalized_and_id_lists_enforced():
    value = [
        _basis("exact_user_requirement", ["ES-1"], ["ART-1"]),
        _basis("planned_data", ["ES-1"], []),
        _basis("qualitative_check", ["ES-9"], ["ART-9"]),
        _basis("source_based", ["ES-3"], ["ART-5"]),
        _basis("data_based", ["ES-2"], ["ART-2"]),
    ]
    fixed = _sanitize_evaluation_rules([dict(v) for v in value])
    kinds = [v["criterion_basis"]["kind"] for v in fixed]
    assert kinds == ["request_based", "data_based", "qualitative", "source_based", "data_based"]
    # source_based keeps sources, drops artifacts; data_based keeps artifacts, drops sources
    assert fixed[3]["criterion_basis"]["evidence_source_ids"] == ["ES-3"]
    assert fixed[3]["criterion_basis"]["artifact_ids"] == []
    assert fixed[4]["criterion_basis"]["evidence_source_ids"] == []
    assert fixed[4]["criterion_basis"]["artifact_ids"] == ["ART-2"]
    # request_based / qualitative must have empty id lists
    for i in (0, 2):
        assert fixed[i]["criterion_basis"]["evidence_source_ids"] == []
        assert fixed[i]["criterion_basis"]["artifact_ids"] == []


def test_non_list_value_passes_through_unchanged():
    assert _sanitize_evaluation_rules({"not": "a list"}) == {"not": "a list"}
    assert _sanitize_evaluation_rules("str") == "str"
