from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "jw/subagents/solar/skills/solar-cycle/scripts/run_cycle_morphology_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("cycle_morphology_experiment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _month_parts(index: int) -> tuple[int, int]:
    return index // 12, index % 12 + 1


def _write_synthetic_silso(tmp_path: Path) -> tuple[Path, Path]:
    extrema = tmp_path / "TableCyclesMiMa.txt"
    smoothed = tmp_path / "SN_ms_tot_V2.0.csv"
    minima = [1755 * 12 + 1]
    for cycle in range(1, 25):
        minima.append(minima[-1] + 108 + (cycle % 5) * 6)
    extrema_lines = []
    smoothed_lines = []
    for cycle in range(1, 25):
        min_year, min_month = _month_parts(minima[cycle - 1])
        maximum_index = minima[cycle - 1] + 36 + (cycle % 4) * 9
        max_year, max_month = _month_parts(maximum_index)
        table_peak = 100.0 + cycle
        series_peak = table_peak + (0.1 if cycle == 3 else 0.0)
        extrema_lines.append(
            f"{cycle:02d} {min_year:04d} {min_month:02d} 1.0 "
            f"{max_year:04d} {max_month:02d} {table_peak:.1f} 10 00"
        )
        smoothed_lines.append(
            f"{max_year:04d};{max_month:02d};{max_year:.3f};{series_peak:.1f};-1.0;-1;1"
        )
    year_25, month_25 = _month_parts(minima[24])
    extrema_lines.append(f"25 {year_25:04d} {month_25:02d} 1.0")
    extrema.write_text("\n".join(extrema_lines) + "\n", encoding="utf-8")
    smoothed.write_text("\n".join(smoothed_lines) + "\n", encoding="utf-8")
    return extrema, smoothed


def test_peak_uses_smoothed_series_at_official_maximum_date(tmp_path: Path) -> None:
    extrema, smoothed = _write_synthetic_silso(tmp_path)

    rows = MODULE.build_rows(extrema, smoothed)

    assert len(rows) == 24
    assert rows[2]["peak_smoothed_sunspot_number"] == 103.1
    assert "differs" in rows[2]["data_quality_note"]
    assert "18th-century" in rows[0]["data_quality_note"]


def test_missing_smoothed_value_at_official_maximum_is_explicit(tmp_path: Path) -> None:
    extrema, smoothed = _write_synthetic_silso(tmp_path)
    lines = smoothed.read_text(encoding="utf-8").splitlines()
    smoothed.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

    try:
        MODULE.build_rows(extrema, smoothed)
    except ValueError as exc:
        assert "missing 13-month smoothed value" in str(exc)
    else:
        raise AssertionError("missing official-date smoothed value was accepted")
