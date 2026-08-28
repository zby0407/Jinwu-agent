from jw.middleware.research_review_orchestration import build_a2a_handoff_envelope


def test_a2a_handoff_envelope_carries_stage_contract_and_boundaries() -> None:
    envelope = build_a2a_handoff_envelope(
        task_id="task-1",
        action={
            "kind": "producer",
            "stage": "data",
            "phase": "bounded_data",
            "revision_review_id": None,
        },
        specialist="solar-data",
        analysis_protocol="silso_cycle_morphology_v1",
        accepted_upstream_refs=["planning-artifact@v1:abc"],
        data_context={
            "receipt_ref": "receipts/datasets/data-context.json",
            "context_sha256": "ctx",
            "must_stop": False,
            "eligible_inputs": [
                {
                    "dataset_id": "silso-cycle-extrema-v2",
                    "path": "data/extrema.txt",
                    "sha256": "abc",
                }
            ],
        },
    )

    assert envelope["schema_version"] == "a2a-handoff-v1"
    assert envelope["task_id"] == "task-1"
    assert envelope["owner"] == "solar-data"
    assert envelope["stage"] == "data"
    assert envelope["analysis_protocol"] == "silso_cycle_morphology_v1"
    assert envelope["accepted_upstream_refs"] == ["planning-artifact@v1:abc"]
    assert envelope["data_context"]["receipt_ref"].endswith("data-context.json")
    assert (
        envelope["data_context"]["eligible_inputs"][0]["dataset_id"]
        == "silso-cycle-extrema-v2"
    )
    assert "blocked" in envelope["return_contract"]["allowed_statuses"]
    assert "invent" in envelope["return_contract"]["hard_boundary"]
