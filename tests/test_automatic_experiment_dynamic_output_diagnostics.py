"""Explain incomplete structural fixtures; never infer missing scientific values."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from automatic_experiment import contracts, policy


def worker(measurements):
    return {
        "schema_version": contracts.WORKER_RESULT_VERSION,
        "execution_completed": True,
        "measurements": measurements,
        "result_items": [],
        "artifacts": [],
        "warnings": [],
        "endpoint_results": [],
        "scientific_payload": {
            "primary_estimand": "Structural fixture only, not research.",
            "estimate": None,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": [],
        },
    }


def test_runtime_null_measurement_is_rejected_but_omission_only_passes_row_schema():
    measurement = {
        "name": "fixture_value",
        "value": None,
        "unit": "",
        "role": "diagnostic",
        "source_artifact": None,
    }
    with pytest.raises(contracts.ContractError, match="must be numeric"):
        contracts.validate_worker_result(worker([measurement]))
    assert contracts.validate_worker_result(worker([]))["measurements"] == []


def test_dynamic_rows_report_real_missing_and_extra_fields_without_coverage():
    code = """
def run_experiment(context):
    measurements = []
    results = []
    for name in ['fixture_measurement']:
        measurements.append({'name': name, 'value': None, 'source_artifact': None, 'note': 'unavailable'})
    for identifier in ['fixture_result']:
        results.append({'id': identifier, 'value': 'unknown', 'source_artifact': None, 'note': 'unavailable'})
    return {'measurements': measurements, 'result_items': results}
"""
    tree = ast.parse(code)
    errors = policy._validate_worker_return_literals(
        tree, dynamic_unproven_fields={"measurements", "result_items"}
    )
    text = "; ".join(errors)
    assert "does not statically prove required output coverage" in text
    assert "missing=['role', 'unit'], unknown=['note']" in text
    assert (
        "missing=['display_name', 'role', 'unit', 'value_kind'], unknown=['note']"
        in text
    )
    assert ".value is null" in text
    assert "unknown=['source_artifact']" not in text
    assert policy._declared_worker_refs(tree)[:2] == (set(), set())


def test_returned_alias_extend_is_diagnosed_but_unrelated_list_is_not():
    code = """
def run_experiment(context):
    measurement_rows = []
    alias = measurement_rows
    unused = []
    unused.append({'wrong': 'not part of worker result'})
    alias.extend(build_rows())
    return {'measurements': measurement_rows, 'result_items': []}
"""
    errors = policy._validate_worker_return_literals(
        ast.parse(code), dynamic_unproven_fields={"measurements"}
    )
    text = "; ".join(errors)
    assert "dynamic extend" in text
    assert "unused" not in text
    assert "unknown=['wrong']" not in text


def test_complete_literal_row_keeps_original_static_coverage():
    code = """
def run_experiment(context):
    measurements = [{'name': 'fixture_measurement', 'value': calculated, 'unit': 'count', 'role': 'diagnostic', 'source_artifact': None}]
    results = [{'id': 'fixture_result', 'display_name': 'Fixture', 'value_kind': 'text', 'value': calculated_text, 'unit': '', 'role': 'diagnostic', 'source_artifact': None}]
    return {'measurements': measurements, 'result_items': results}
"""
    tree = ast.parse(code)
    assert policy._declared_worker_refs(tree)[:2] == (
        {"fixture_measurement"},
        {"fixture_result"},
    )
    assert not any(
        "populated dynamically" in error
        for error in policy._validate_worker_return_literals(tree)
    )


def test_diagnostics_do_not_change_already_covered_code_acceptance():
    payload = repr(worker([])).replace(
        "'measurements': []", "'measurements': measurement_records"
    )
    payload = payload.replace("'result_items': []", "'result_items': result_records")
    source = (
        "def run_experiment(context):\n"
        "    measurement_records = []\n"
        "    measurement_records.append({'name': 'fixture_measurement', 'value': context['seed'], 'unit': 'count', 'role': 'diagnostic', 'source_artifact': None})\n"
        "    result_records = []\n"
        "    result_records.append({'id': 'fixture_result', 'display_name': 'Fixture', 'value_kind': 'text', 'value': 'Structural fixture only', 'unit': '', 'role': 'diagnostic', 'source_artifact': None})\n"
        f"    return {payload}\n"
    )
    assert (
        policy.scan_python(
            source,
            "experiment.py",
            required_measurements={"fixture_measurement"},
            required_results={"fixture_result"},
        )
        == set()
    )
