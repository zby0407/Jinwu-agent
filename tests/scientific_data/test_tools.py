"""Tool surface: the same pipeline, reachable by an agent as JSON tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.tools import scientific_data as tools

SOURCE_A = """\
name,mjd,band,mag,mag_err
SN 2005cf,53520.0,V,15.10,0.05
SN 2005cf,53522.0,V,14.80,0.04
SN 2005cf,53522.0,V,14.80,0.04
SN 2005cf,53524.0,V,14.60,0.04
"""

SOURCE_B = """\
object,time,filter,magnitude
SN 2005cf,53520.0,V,15.40
SN 2005cf,53526.0,V,14.50
"""

FIGURE = {
    "figure_id": "fig-a",
    "pixel_noise": 1.0,
    "x_ticks": [{"pixel": 100, "value": 53536}, {"pixel": 300, "value": 53576}],
    "y_ticks": [{"pixel": 400, "value": 16.0}, {"pixel": 200, "value": 15.0}],
    "y_scale": "linear",
    "unit": "mag",
    "observable": "apparent_magnitude",
    "entity": "SN 2005cf",
    "series": [{"label": "V", "points": [[200, 350]]}],
}

REQUEST = {
    "research_goal": "SN Ia V-band light curve from published tables",
    "domain": "snia_light_curve",
    "observables": ["apparent_magnitude", "time", "object", "band"],
    "entity_class": "supernova",
}


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    return tmp_path


def _json(text: str) -> dict:
    payload = json.loads(text)
    assert "status" in payload
    return payload


def _run(workspace: Path, **overrides) -> dict:
    first = workspace / "a.csv"
    first.write_text(SOURCE_A, encoding="utf-8")
    second = workspace / "b.csv"
    second.write_text(SOURCE_B, encoding="utf-8")
    payload = {
        "request_json": json.dumps(REQUEST),
        "inputs": [str(first), str(second)],
        "run_id": "tool-run",
    }
    payload.update(overrides)
    return _json(tools.sci_data_run.invoke(payload))


def test_capabilities_explain_the_boundary(workspace):
    payload = _json(tools.sci_data_capabilities.invoke({}))
    assert payload["status"] == "ok"
    assert "align" in payload["stages"]
    assert "duplicate_exact" in payload["auto_repair_codes"]
    assert "cross_source_conflict" not in payload["auto_repair_codes"]
    assert {item["name"] for item in payload["plugins"]} >= {"snia_light_curve"}


def test_contract_and_plan_are_inspectable(workspace):
    contract = _json(
        tools.sci_data_contract.invoke({"request_json": json.dumps(REQUEST)})
    )
    assert contract["status"] == "ok"
    assert contract["fingerprint"]
    plan = _json(tools.sci_data_plan.invoke({"contract_json": json.dumps(REQUEST)}))
    assert plan["status"] == "ok"
    roles = [step["role"] for step in plan["steps"]]
    assert "primary_published" in roles or "authored_table" in roles


def test_run_writes_package_and_reports_questions(workspace):
    payload = _run(workspace)
    assert payload["status"] == "ok"
    assert payload["summary"]["counts"]["records"] > 0
    assert payload["open_questions"]
    codes = {question["code"] for question in payload["open_questions"]}
    assert "cross_source_conflict" in codes
    package = _json(tools.sci_data_package.invoke({"run_id": "tool-run"}))
    joined = {item["role"] for item in package["artifacts"]}
    assert "observations" in joined


def test_issues_can_be_filtered_to_one_code(workspace):
    _run(workspace)
    payload = _json(
        tools.sci_data_issues.invoke(
            {"run_id": "tool-run", "code": "cross_source_conflict"}
        )
    )
    assert payload["status"] == "ok"
    assert payload["total"] >= 1
    assert all(item["code"] == "cross_source_conflict" for item in payload["issues"])
    assert payload["issues"][0]["blocking"] is True


def test_status_counts_stages(workspace):
    _run(workspace)
    payload = _json(tools.sci_data_status.invoke({"run_id": "tool-run"}))
    assert payload["status"] == "ok"
    assert "quality" in payload["stages"]
    assert payload["counts"]["records"] > 0


def test_answer_rewrites_the_package(workspace):
    first = _run(workspace)
    question = next(
        item
        for item in first["open_questions"]
        if item["code"] == "cross_source_conflict"
    )
    keep = next(option for option in question["options"] if option.startswith("keep:"))
    payload = _json(
        tools.sci_data_answer.invoke(
            {
                "run_id": "tool-run",
                "answers_json": json.dumps({question["request_id"]: keep}),
            }
        )
    )
    assert payload["status"] == "ok"
    remaining = {item["request_id"] for item in payload["open_questions"]}
    assert question["request_id"] not in remaining


def test_digitise_reports_calibration_and_overlay(workspace):
    payload = _json(tools.sci_data_digitise.invoke({"figure_json": json.dumps(FIGURE)}))
    assert payload["status"] == "ok"
    assert payload["records"][0]["value_kind"] == "digitized"
    assert payload["records"][0]["unit"] == "mag"
    assert payload["overlay"]["checked"] == 1
    assert "re-projects" in payload["verification_note"]


def test_run_records_the_figure_as_digitised_evidence(workspace):
    payload = _run(workspace, figures_json=json.dumps([FIGURE]))
    assert payload["status"] == "ok"
    assert "figure_overlays/fig-a" in payload["package_files"]


def test_runs_lists_previous_runs(workspace):
    _run(workspace)
    payload = _json(tools.sci_data_runs.invoke({}))
    assert payload["status"] == "ok"
    assert [item["run_id"] for item in payload["runs"]] == ["tool-run"]


def test_column_overrides_json_is_applied(workspace: Path):
    path = workspace / "cryptic.csv"
    path.write_text("KEY,FLT,EPOCH,MAG\nSN 2018oh,V,58160.30,15.10\n", encoding="utf-8")
    payload = _json(
        tools.sci_data_run.invoke(
            {
                "request_json": json.dumps(REQUEST),
                "inputs": [str(path)],
                "column_overrides_json": json.dumps(
                    [
                        {
                            "source": "cryptic.csv",
                            "column": "KEY",
                            "observable": "object",
                        },
                        {
                            "source": "cryptic.csv",
                            "column": "EPOCH",
                            "observable": "time",
                        },
                    ]
                ),
                "run_id": "override",
            }
        )
    )
    assert payload["status"] == "ok"
    assert payload["summary"]["counts"]["records"] == 1


def test_unknown_run_is_an_error_not_a_crash(workspace):
    payload = _json(tools.sci_data_status.invoke({"run_id": "missing"}))
    assert payload["status"] == "error"
    assert "unknown run" in payload["error"]


def test_remote_sources_are_refused_without_an_allowlist(workspace):
    payload = _json(
        tools.sci_data_acquire_only.invoke(
            {
                "request_json": json.dumps(REQUEST),
                "urls_json": json.dumps([{"url": "https://example.org/t.csv"}]),
            }
        )
    )
    assert payload["status"] == "ok"
    assert payload["results"][0]["status"] == "denied"
    assert payload["issues"][0]["code"] == "source_host_denied"


def test_discover_refuses_the_network_by_default(workspace):
    payload = _json(
        tools.sci_data_discover.invoke({"request_json": json.dumps(REQUEST)})
    )
    assert payload["status"] == "ok"
    discovery = payload["discovery"]
    assert discovery["status"] == "policy_blocked"
    assert payload["urls"] == []


def test_discover_registers_user_supplied_urls_without_network(workspace):
    payload = _json(
        tools.sci_data_discover.invoke(
            {
                "request_json": json.dumps(REQUEST),
                "extra_urls_json": json.dumps(["https://example.org/table.csv"]),
            }
        )
    )
    assert payload["urls"] == ["https://example.org/table.csv"]
    found = payload["discovery"]["found"]
    assert found[0]["discovered_via"] == "user_input"
