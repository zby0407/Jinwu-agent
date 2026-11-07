"""Offline engineering fixtures; these are not scientific observations."""

import csv
import json

import pytest

from jw.scientific_data.contracts import DataRequirementContract, IssueCode
from jw.scientific_data.domain import get_plugin, plugin_for_contract
from jw.scientific_data.ledger_io import load_ledger
from jw.scientific_data.workflow import (
    WorkflowOptions,
    contract_from_request,
    run_integration,
)


def request(**changes):
    payload = {
        "research_goal": "Engineering fixture for configurable table ingestion",
        "domain": "generic",
        "entity_class": "fixture",
        "identifier_fields": ["sample_id"],
        "time_field": "elapsed",
        "time_system": "days_relative",
        "reference_mjd": 51544,
        "observables": [
            {"name": "sample_id", "kind": "identifier", "aliases": ["specimen"]},
            {"name": "elapsed", "kind": "time", "aliases": ["tick"]},
            {"name": "length", "unit": "m", "aliases": ["reading"]},
            {"name": "group", "kind": "label", "aliases": ["batch"]},
        ],
        "value_ranges": {"length": [0, 2]},
    }
    payload.update(changes)
    return contract_from_request(payload)


def test_custom_fields_flow_through_package_and_reload(tmp_path):
    source = tmp_path / "fixture.csv"
    source.write_text(
        "specimen,tick,reading [cm],batch\na,0,100,x\na,1,300,y\n",
        encoding="utf-8",
    )
    result = run_integration(
        workspace=tmp_path,
        contract=request(),
        inputs=[source],
        options=WorkflowOptions(auto_discover=False),
    )
    assert len(result.records) == 2
    assert [row.value for row in result.records] == pytest.approx([1, 3])
    assert all(row.unit == "m" for row in result.records)
    assert result.records[0].time == "2000-01-01"
    assert result.records[1].time == "2000-01-02"
    assert result.records[0].attributes["group"] == "x"
    assert result.records[0].value_raw == "100"
    assert result.records[0].time_raw == "0"
    assert result.records[0].provenance
    assert any(issue.code == IssueCode.OUT_OF_RANGE for issue in result.issues)
    assert result.package is not None
    with open(
        result.package.files["mappings"], encoding="utf-8-sig", newline=""
    ) as stream:
        exported = next(
            row for row in csv.DictReader(stream) if row["observable"] == "elapsed"
        )
    assert exported["time_system"] == "days_relative"
    assert float(exported["reference_mjd"]) == 51544
    ledger = load_ledger(
        tmp_path / "scientific_data" / "runs" / result.ledger.run_id / "ledger.json"
    )
    clock = next(item for item in ledger.mappings if item.observable == "elapsed")
    assert clock.time_system == "days_relative"
    assert clock.reference_mjd == 51544
    assert ledger.contract.time_system == "days_relative"
    assert get_plugin("generic").observable("length") is None


def test_source_overrides_accept_contract_fields_and_time_reference(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("specimen,tick,reading,batch\na,0,100,x\n", encoding="utf-8")
    second.write_text("specimen,tick,reading,batch\na,86400,200,y\n", encoding="utf-8")
    overrides = tuple(
        {"source": name, "column": "reading", "observable": "length", "unit": "cm"}
        for name in (first.name, second.name)
    ) + (
        {
            "source": second.name,
            "column": "tick",
            "observable": "elapsed",
            "time_system": "seconds",
            "reference_mjd": 51545,
        },
    )
    result = run_integration(
        workspace=tmp_path,
        contract=request(),
        inputs=[first, second],
        options=WorkflowOptions(auto_discover=False, column_overrides=overrides),
    )
    assert len(result.records) == 2
    assert [row.time for row in result.records] == ["2000-01-01", "2000-01-03"]
    assert [row.value for row in result.records] == pytest.approx([1, 2])
    assert not any(
        issue.code == IssueCode.TIME_SYSTEM_AMBIGUOUS for issue in result.issues
    )


def test_missing_relative_reference_stays_explicit(tmp_path):
    source = tmp_path / "fixture.csv"
    source.write_text("specimen,tick,reading [m],batch\na,1,1,x\n", encoding="utf-8")
    result = run_integration(
        workspace=tmp_path,
        contract=request(reference_mjd=None),
        inputs=[source],
        options=WorkflowOptions(auto_discover=False),
    )
    assert len(result.records) == 1
    assert result.records[0].time_raw == "1"
    assert any(issue.code == IssueCode.TIME_SYSTEM_AMBIGUOUS for issue in result.issues)


def test_contract_roundtrip_and_plugin_overlay_do_not_mutate_registry():
    original = request()
    restored = DataRequirementContract.from_dict(original.to_dict())
    plugin = plugin_for_contract(restored)
    assert plugin.observable("sample_id").kind == "identifier"
    assert plugin.observable("elapsed").kind == "time"
    assert plugin.observable("length").canonical_unit == "m"
    assert restored.fingerprint == original.fingerprint
    assert get_plugin("generic").observable("elapsed") is None


def test_agent_tool_accepts_the_same_request_without_network(tmp_path, monkeypatch):
    from jw.tools import scientific_data as tools

    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    source = tmp_path / "fixture.csv"
    source.write_text("specimen,tick,reading [cm],batch\na,0,100,x\n", encoding="utf-8")
    response = json.loads(
        tools.sci_data_run.invoke(
            {
                "request_json": json.dumps(request().to_dict()),
                "inputs": [source.name],
                "auto_discover": False,
                "allow_network": False,
                "run_id": "contract-tool-fixture",
            }
        )
    )
    assert response["status"] == "ok"
    assert response["summary"]["counts"]["records"] == 1
