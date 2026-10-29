"""Tool surface: the influence pipeline reachable as JSON tools.

No test here touches the network.  The two tools that would make a request must
report ``network_disabled`` and explain the refusal instead of sending anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.tools import influence as tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    return tmp_path


def _load(text: str) -> dict:
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def _demo_cohort(tmp_path, name="demo", count=60, seed=3) -> Path:
    payload = _load(
        tools.infl_snapshot_synthetic.invoke(
            {"name": name, "count": count, "seed": seed}
        )
    )
    assert payload["status"] == "ok", payload
    return Path(payload["path"])


def test_capabilities_state_what_is_refused(workspace):
    payload = _load(tools.infl_capabilities.invoke({}))
    assert payload["status"] == "ok"
    assert len(payload["ladder"]) == 10
    refusals = " ".join(payload["refusals"])
    assert "Sci-Hub" in refusals
    assert "names" in refusals
    assert payload["not_attempted"]["author_demographics"]
    assert len(payload["audits"]) == 10


def test_snapshot_synthetic_is_reproducible(workspace):
    first = _load(
        tools.infl_snapshot_synthetic.invoke({"name": "a", "count": 40, "seed": 9})
    )
    second = _load(
        tools.infl_snapshot_synthetic.invoke({"name": "b", "count": 40, "seed": 9})
    )
    assert first["fingerprint"] == second["fingerprint"]
    assert first["papers"] == 40
    assert "pipeline validation only" in first["honesty_note"]


def test_cohorts_lists_what_was_frozen(workspace):
    _demo_cohort(workspace, name="demo", count=40)
    payload = _load(tools.infl_cohorts.invoke({}))
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["cohorts"][0]["papers"] == 40
    assert payload["cohorts"][0]["synthetic"] is True


def test_network_is_refused_by_default(workspace):
    search = _load(tools.infl_snapshot_search.invoke({"query": "supernova cosmology"}))
    assert search["status"] == "error"
    assert "network_disabled" in search["error"]
    fetch = _load(tools.infl_fetch_paper.invoke({"doi": "10.1093/mnras/stv1947"}))
    assert fetch["status"] == "error"
    assert "network_disabled" in fetch["error"]


def test_run_requires_a_reference_population(workspace):
    payload = _load(tools.infl_run.invoke({"cohort": ""}))
    assert payload["status"] == "error"
    assert "reference population" in payload["error"]


def test_run_produces_a_self_describing_bundle(workspace):
    cohort = _demo_cohort(workspace, count=80)
    payload = _load(
        tools.infl_run.invoke(
            {
                "cohort": str(cohort),
                "seed": 1,
                "max_subjects": 3,
            }
        )
    )
    assert payload["status"] == "ok", payload
    directory = Path(payload["package_dir"])
    assert (directory / "report.md").is_file()
    assert payload["papers"] == 80
    # Right-censored papers are excluded from training, never silently labelled.
    assert 0 < payload["metrics_summary"]["labelled"] <= 80
    assert payload["metrics_summary"]["interval_coverage"] is not None
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "80% 区间" in report
    assert "偏差" in report


def test_status_report_bias_explain_and_package(workspace):
    cohort = _demo_cohort(workspace, count=80)
    run = _load(
        tools.infl_run.invoke({"cohort": str(cohort), "max_subjects": 2, "seed": 2})
    )
    run_id = run["run_id"]

    status = _load(tools.infl_status.invoke({"run_id": run_id}))
    assert status["status"] == "ok"
    assert "model" in status["run"]["stages"]

    report = _load(tools.infl_report.invoke({"run_id": run_id}))
    assert "6. 可复现性" in report["report"]

    bias = _load(tools.infl_bias.invoke({"run_id": run_id}))
    assert bias["status"] == "ok"
    assert bias["count"] == 10
    assert (
        "作者人口学（不做）" in "".join(bias["cannot_assess"]) or bias["cannot_assess"]
    )
    unassessable = _load(
        tools.infl_bias.invoke({"run_id": run_id, "status": "cannot_assess"})
    )
    assert unassessable["count"] >= 1

    explain = _load(tools.infl_explain.invoke({"run_id": run_id}))
    assert explain["status"] == "ok"
    assert explain["factors"]
    assert explain["prediction"]["paper_id"] == explain["paper_id"]
    assert explain["comparators"]

    package = _load(tools.infl_package.invoke({"run_id": run_id}))
    assert package["status"] == "ok"
    roles = {item["role"] for item in package["manifest"]["artifacts"]}
    assert {"papers", "predictions", "factors", "bias_audit", "report"} <= roles


def test_unknown_run_is_an_error(workspace):
    payload = _load(tools.infl_status.invoke({"run_id": "nope"}))
    assert payload["status"] == "error"
    assert "unknown run" in payload["error"]


def test_parse_pdf_refuses_an_empty_list(workspace):
    payload = _load(tools.infl_parse_pdf.invoke({"paths": []}))
    assert payload["status"] == "error"
    assert "at least one PDF" in payload["error"]


def test_acquisition_request_lists_legitimate_routes(workspace):
    payload = _load(
        tools.infl_acquisition_request.invoke({"doi": "10.1093/mnras/stv1947"})
    )
    assert payload["status"] == "ok"
    routes = [step["route"] for step in payload["steps"]["steps"]]
    assert routes[0] == "interlibrary_loan"
    assert "shadow-library mirrors" in payload["refused"]


def test_paths_outside_the_workspace_are_refused(workspace):
    payload = _load(tools.infl_parse_pdf.invoke({"paths": ["/etc/hosts"]}))
    assert payload["status"] == "error"
    assert "path escaped the task workspace" in payload["error"]
