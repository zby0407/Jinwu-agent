"""Synthetic tests only: no real C scientific outcomes are computed here."""

import calendar
import json
import math
from pathlib import Path

import pytest

from jw import cross_series as c


@pytest.fixture
def params():
    return json.loads(
        (Path(__file__).parent / "fixtures/cross_series_parameters.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def bundle(tmp_path, params):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    monthly = {}
    lines = []
    for year in range(1749, 2027):
        for month in range(1, 13):
            value = ((year - 1749) % 11) * 2 + month
            monthly[year * 12 + month - 1] = {"value": float(value)}
            lines.append(f"{year} {month} {year + 0.5} {value} -1 -1")
    (inputs / "monthly.txt").write_text("\n".join(lines), encoding="utf-8")
    (inputs / "GNbb2_y.txt").write_text(
        "\n".join(
            f"{y}.5 {2 * c.annualize(monthly, y, 'calendar_days'):.14g} -1"
            for y in range(1749, 2016)
        ),
        encoding="utf-8",
    )
    (inputs / "boundaries.txt").write_text(
        "Cycle\n" + "\n".join(f"{i} {1755 + (i - 1) * 11} 2 0" for i in range(1, 26)),
        encoding="utf-8",
    )
    for name in ("smoothed.csv", "a_metrics.csv", "a_parameters.json"):
        (inputs / name).write_text("synthetic context only", encoding="utf-8")
    records = [
        {
            "key": key,
            "path": name,
            "bytes": (inputs / name).stat().st_size,
            "sha256": c.digest(inputs / name),
        }
        for key, name in [
            ("monthly", "monthly.txt"),
            ("gnbb2", "GNbb2_y.txt"),
            ("boundaries", "boundaries.txt"),
            ("smoothed", "smoothed.csv"),
            ("a_metrics", "a_metrics.csv"),
            ("a_parameters", "a_parameters.json"),
        ]
    ]
    c.save(
        inputs / "input_manifest.json",
        {"schema": "cross-series-inputs-v1", "files": records},
    )
    parameter = tmp_path / "parameters.json"
    c.save(parameter, params)
    return inputs, parameter


def test_day_weighting_leap_year_is_not_equal_month_weight():
    monthly = {2000 * 12 + i: {"value": 100.0 if i == 1 else 0.0} for i in range(12)}
    assert c.annualize(monthly, 2000, "calendar_days") == pytest.approx(100 * 29 / 366)
    assert c.annualize(monthly, 2000, "equal_months") == pytest.approx(100 / 12)


def test_complete_year_membership_no_boundary_double_count():
    years = list(range(1796, 1813))
    left = c.members(years, 1796 * 12, 1798 * 12 + 3, "whole_calendar_year")
    right = c.members(years, 1798 * 12 + 3, 1810 * 12 + 6, "whole_calendar_year")
    assert left == [1796, 1797]
    assert right == list(range(1799, 1810))
    assert not set(left) & set(right)
    assert 1798 in c.members(years, 1798 * 12 + 3, 1810 * 12 + 6, "midyear")
    assert 1810 not in c.members(years, 1798 * 12 + 3, 1810 * 12 + 6, "midyear")


def test_midyear_exact_boundary_assigned_to_next_cycle():
    assert c.members([2000], 1990 * 12, 2000 * 12 + 6, "midyear") == []
    assert c.members([2000], 2000 * 12 + 6, 2010 * 12, "midyear") == [2000]


def test_true_zero_and_missing_metadata_preserved(tmp_path):
    p = tmp_path / "monthly.txt"
    p.write_text("1800 1 1800.0 0 -1 -1\n1800 2 1800.1 1 0 0", "utf-8")
    rows = c.parse_monthly(p)
    assert rows[1800 * 12] == {"value": 0.0, "sigma": None, "observations": None}


@pytest.mark.parametrize(
    "text",
    [
        "1800 1 1800.0 0 -1 -1\n1800 1 1800.0 0 -1 -1",
        "1800 1 1800.0 0 -1 -1\n1800 3 1800.0 0 -1 -1",
        "1800 13 1800.0 0 -1 -1",
        "1800 1 1800.0 nan -1 -1",
        "1800 1 1800.0 -2 -1 -1",
    ],
)
def test_monthly_invalid_stops(tmp_path, text):
    p = tmp_path / "x"
    p.write_text(text, "utf-8")
    with pytest.raises(ValueError):
        c.parse_monthly(p)


@pytest.mark.parametrize(
    "text",
    [
        "1800.5 1 0\n1800.5 2 0",
        "1800.5 1 0\n1802.5 2 0",
        "1800.0 1 0",
        "1800.5 inf 0",
        "1800.5 1 -2",
    ],
)
def test_gn_invalid_stops(tmp_path, text):
    p = tmp_path / "x"
    p.write_text(text, "utf-8")
    with pytest.raises(ValueError):
        c.parse_gn(p)


def test_peak_ties_and_strict_low_threshold():
    s = c.summary([(1, 0.0), (2, 2.0), (3, 2.0), (4, 1.0)], 1.0)
    assert s["annual_peak_year"] == 2 and s["all_peak_years"] == "2|3"
    assert s["low_fraction"] == 0.25 and s["longest_low_years"] == 1
    assert s["annual_sum"] == s["annual_mean"] * s["year_count"]
    assert c.longest_low([(1, 0), (2, 0), (4, 0)], 1) == 2


def test_tolerance_direction_and_reversal(params):
    tol = params["tolerance"]
    assert c.sign(1.0, 1.0 + 1e-14, tol) == "numerically_tied"
    assert c.sign(1.0, 2.0, tol) == "lower"
    assert c.sign(2.0, 1.0, tol) == "higher"


def test_parameter_scope_rejected(params):
    params["cycles"].append(24)
    with pytest.raises(ValueError):
        c.validate_parameters(params)


def test_hash_failure_is_audited(bundle, tmp_path):
    inputs, p = bundle
    (inputs / "GNbb2_y.txt").write_text("tamper", "utf-8")
    with pytest.raises(ValueError, match="hash"):
        c.run(inputs, p, tmp_path / "failed", check_only=True)
    assert (
        json.loads((tmp_path / "failed/run_manifest.json").read_text("utf-8"))["status"]
        == "failed"
    )


def test_existing_output_refused(bundle, tmp_path):
    inputs, p = bundle
    out = tmp_path / "exists"
    out.mkdir()
    (out / "keep").write_text("yes")
    with pytest.raises(FileExistsError):
        c.run(inputs, p, out)
    assert (out / "keep").read_text() == "yes"


def test_inputs_not_mutated_check_only_no_scientific_results(bundle, tmp_path):
    inputs, p = bundle
    before = {q.name: c.digest(q) for q in inputs.iterdir()}
    r = c.run(inputs, p, tmp_path / "ready", check_only=True)
    assert r["status"] == "inputs_ready_no_C_results"
    assert not (tmp_path / "ready/annual_matched.csv").exists()
    assert not (tmp_path / "ready/hypothesis_Q3.json").exists()
    assert before == {q.name: c.digest(q) for q in inputs.iterdir()}


def test_missing_scientific_value_fails_even_with_updated_identity(bundle, tmp_path):
    inputs, p = bundle
    path = inputs / "GNbb2_y.txt"
    lines = path.read_text("utf-8").splitlines()
    lines[0] = "1749.5 -1 -1"
    path.write_text("\n".join(lines), "utf-8")
    manifest = json.loads((inputs / "input_manifest.json").read_text("utf-8"))
    for rec in manifest["files"]:
        if rec["key"] == "gnbb2":
            rec.update(sha256=c.digest(path), bytes=path.stat().st_size)
    c.save(inputs / "input_manifest.json", manifest)
    with pytest.raises(ValueError, match="GN year"):
        c.run(inputs, p, tmp_path / "failed", check_only=True)


def test_synthetic_calculation_counts_scaling_and_result_dependent_conclusion(
    bundle, params
):
    inputs, _ = bundle
    monthly, gn, bounds, years, _, _ = c.load_inputs(inputs, params)
    tables = c.calculate(monthly, gn, bounds, years, params)
    assert {k: len(v) for k, v in tables.items()} == {
        "annual_matched.csv": 267,
        "cycle_annual_metrics.csv": 184,
        "within_product_contrasts.csv": 240,
        "Q3_direction_agreement.csv": 120,
        "trough_windows.csv": 12,
    }
    metrics = tables["cycle_annual_metrics.csv"]
    base = [r for r in metrics if r["scenario"] == "calendar_days__whole_calendar_year"]
    for i in range(23):
        assert base[i + 23]["annual_mean"] == pytest.approx(2 * base[i]["annual_mean"])
        assert base[i + 23]["scaled_annual_mean"] == pytest.approx(
            base[i]["scaled_annual_mean"]
        )
    before = [
        r
        for r in tables["Q3_direction_agreement.csv"]
        if r["scenario"] == "calendar_days__whole_calendar_year"
        and r["cycle"] == 5
        and r["comparator"] == "SC4"
        and r["metric"] == "annual_mean"
    ][0]
    assert before["status"] == "both_tied"
    for y in c.members(years, bounds[5], bounds[6], "whole_calendar_year"):
        gn[y]["value"] *= 0.01
    after = c.calculate(monthly, gn, bounds, years, params)
    row = [
        r
        for r in after["Q3_direction_agreement.csv"]
        if r["scenario"] == "calendar_days__whole_calendar_year"
        and r["cycle"] == 5
        and r["comparator"] == "SC4"
        and r["metric"] == "annual_mean"
    ][0]
    assert row["status"] == "discordant" and row["GNbb2_direction"] == "lower"


def test_synthetic_full_run_repeats_byte_identically(bundle, tmp_path):
    inputs, p = bundle
    c.run(inputs, p, tmp_path / "one")
    c.run(inputs, p, tmp_path / "two")
    for path in (tmp_path / "one").glob("*.csv"):
        assert c.digest(path) == c.digest(tmp_path / "two" / path.name)
    for name in ("hypothesis_Q3.json", "C1_report.md"):
        assert c.digest(tmp_path / "one" / name) == c.digest(tmp_path / "two" / name)


def test_annual_weight_manual_arithmetic():
    monthly = {1900 * 12 + i: {"value": float(i * i)} for i in range(12)}
    total = sum(i * i * calendar.monthrange(1900, i + 1)[1] for i in range(12))
    assert math.isclose(
        c.annualize(monthly, 1900, "calendar_days"), total / 365, rel_tol=1e-15
    )
