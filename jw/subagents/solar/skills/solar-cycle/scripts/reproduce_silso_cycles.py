#!/usr/bin/env python3
"""Reproduce SILSO cycle extrema from official Version 2.0 products.

This helper deliberately uses only the Python standard library.  It downloads
the official 13-month smoothed monthly series and the official cycle
minimum/maximum table, recomputes extrema inside neighboring-extrema windows,
and preserves both results when they differ.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

SMOOTHED_URL = "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv"
EXTREMA_URL = "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt"
SOURCE_VERSION = "WDC-SILSO Sunspot Number Version 2.0"


@dataclass(frozen=True)
class Extremum:
    year: int
    month: int
    sunspot_number: float

    @property
    def year_month(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True)
class OfficialCycle:
    cycle: int
    minimum: Extremum
    maximum: Extremum | None


def month_delta(start: Extremum, end: Extremum) -> int:
    """Return the calendar-month distance from *start* to *end*."""
    return (end.year - start.year) * 12 + end.month - start.month


def parse_cycle_selector(value: str) -> list[int]:
    """Parse selectors such as ``21-24`` or ``21,23,24``."""
    selected: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"invalid descending cycle range: {token}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    if not selected:
        raise ValueError("at least one cycle must be selected")
    return sorted(selected)


def parse_official_cycles(text: str) -> dict[int, OfficialCycle]:
    """Parse SILSO's whitespace-separated official extrema table."""
    cycles: dict[int, OfficialCycle] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) < 4:
            continue
        cycle = int(fields[0])
        minimum = Extremum(int(fields[1]), int(fields[2]), float(fields[3]))
        maximum = None
        if len(fields) >= 7:
            maximum = Extremum(int(fields[4]), int(fields[5]), float(fields[6]))
        cycles[cycle] = OfficialCycle(cycle, minimum, maximum)
    return cycles


def parse_smoothed_series(text: str) -> dict[tuple[int, int], float]:
    """Parse the official semicolon-separated smoothed monthly series."""
    rows: dict[tuple[int, int], float] = {}
    for fields in csv.reader(text.splitlines(), delimiter=";"):
        if len(fields) < 4:
            continue
        year, month = int(fields[0]), int(fields[1])
        value = float(fields[3])
        if value >= 0:
            rows[(year, month)] = value
    return rows


def _month_key(extremum: Extremum) -> int:
    return extremum.year * 12 + extremum.month


def recompute_cycle_extrema(
    previous_cycle: OfficialCycle,
    cycle: OfficialCycle,
    next_cycle: OfficialCycle,
    series: dict[tuple[int, int], float],
) -> tuple[Extremum, Extremum]:
    """Recompute extrema in non-overlapping neighboring-extrema windows.

    A minimum cannot be found by taking the minimum over the whole
    minimum-to-minimum cycle: the late declining phase can legitimately fall
    below the starting minimum.  Instead, bracket the minimum by the previous
    and current official maxima, and bracket the maximum by the current and
    next official minima.
    """
    if previous_cycle.maximum is None or cycle.maximum is None:
        raise ValueError(f"cycle {cycle.cycle} lacks neighboring extrema")
    minimum_start = _month_key(previous_cycle.maximum)
    minimum_stop = _month_key(cycle.maximum)
    maximum_start = _month_key(cycle.minimum)
    maximum_stop = _month_key(next_cycle.minimum)
    minimum_window = [
        (year, month, value)
        for (year, month), value in series.items()
        if minimum_start < year * 12 + month < minimum_stop
    ]
    maximum_window = [
        (year, month, value)
        for (year, month), value in series.items()
        if maximum_start <= year * 12 + month < maximum_stop
    ]
    if not minimum_window or not maximum_window:
        raise ValueError(f"no smoothed rows found for cycle {cycle.cycle}")
    min_year, min_month, min_value = min(minimum_window, key=lambda row: row[2])
    max_year, max_month, max_value = max(maximum_window, key=lambda row: row[2])
    return (
        Extremum(min_year, min_month, min_value),
        Extremum(max_year, max_month, max_value),
    )


def build_comparison(
    selected: list[int],
    official: dict[int, OfficialCycle],
    series: dict[tuple[int, int], float],
) -> list[dict[str, object]]:
    """Build source-preserving official-versus-recomputed comparison rows."""
    rows: list[dict[str, object]] = []
    for cycle_number in selected:
        previous_cycle = official.get(cycle_number - 1)
        cycle = official.get(cycle_number)
        next_cycle = official.get(cycle_number + 1)
        if (
            previous_cycle is None
            or cycle is None
            or next_cycle is None
            or cycle.maximum is None
        ):
            raise ValueError(
                f"cycle {cycle_number} lacks complete neighboring official extrema"
            )
        computed_min, computed_max = recompute_cycle_extrema(
            previous_cycle, cycle, next_cycle, series
        )
        official_rise = month_delta(cycle.minimum, cycle.maximum)
        computed_rise = month_delta(computed_min, computed_max)
        minimum_matches = computed_min == cycle.minimum
        maximum_matches = computed_max == cycle.maximum
        differences: list[str] = []
        if not minimum_matches:
            if computed_min.sunspot_number == cycle.minimum.sunspot_number:
                differences.append(
                    "The recomputation selected a different month with the same "
                    "minimum smoothed value; both dates are retained."
                )
            else:
                differences.append(
                    "The recomputed minimum date or value differs from the official "
                    "table; both records are retained without inferring a cause."
                )
        if not maximum_matches:
            if computed_max.sunspot_number == cycle.maximum.sunspot_number:
                differences.append(
                    "The recomputation selected a different month with the same "
                    "maximum smoothed value; both dates are retained."
                )
            else:
                differences.append(
                    "The recomputed maximum date or value differs from the official "
                    "table; both records are retained without inferring a cause."
                )
        rows.append(
            {
                "cycle": cycle_number,
                "official_minimum": asdict(cycle.minimum)
                | {"year_month": cycle.minimum.year_month},
                "official_maximum": asdict(cycle.maximum)
                | {"year_month": cycle.maximum.year_month},
                "official_rise_months": official_rise,
                "recomputed_minimum": asdict(computed_min)
                | {"year_month": computed_min.year_month},
                "recomputed_maximum": asdict(computed_max)
                | {"year_month": computed_max.year_month},
                "recomputed_rise_months": computed_rise,
                "minimum_matches_official": minimum_matches,
                "maximum_matches_official": maximum_matches,
                "difference_explanation": (
                    "Official and recomputed extrema agree."
                    if not differences
                    else " ".join(differences)
                ),
            }
        )
    return rows


def fetch_text(url: str, *, attempts: int = 3) -> str:
    """Download text with bounded retries for SILSO's occasional TLS resets."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "JW-SILSO-reproduction/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt)
    assert last_error is not None
    raise last_error


def _read_or_fetch(path: str | None, url: str) -> str:
    return Path(path).read_text(encoding="utf-8") if path else fetch_text(url)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    columns = [
        "cycle",
        "official_minimum",
        "official_minimum_sn",
        "official_maximum",
        "official_maximum_sn",
        "official_rise_months",
        "recomputed_minimum",
        "recomputed_minimum_sn",
        "recomputed_maximum",
        "recomputed_maximum_sn",
        "recomputed_rise_months",
        "minimum_matches_official",
        "maximum_matches_official",
        "difference_explanation",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            official_min = row["official_minimum"]
            official_max = row["official_maximum"]
            computed_min = row["recomputed_minimum"]
            computed_max = row["recomputed_maximum"]
            assert isinstance(official_min, dict)
            assert isinstance(official_max, dict)
            assert isinstance(computed_min, dict)
            assert isinstance(computed_max, dict)
            writer.writerow(
                {
                    "cycle": row["cycle"],
                    "official_minimum": official_min["year_month"],
                    "official_minimum_sn": official_min["sunspot_number"],
                    "official_maximum": official_max["year_month"],
                    "official_maximum_sn": official_max["sunspot_number"],
                    "official_rise_months": row["official_rise_months"],
                    "recomputed_minimum": computed_min["year_month"],
                    "recomputed_minimum_sn": computed_min["sunspot_number"],
                    "recomputed_maximum": computed_max["year_month"],
                    "recomputed_maximum_sn": computed_max["sunspot_number"],
                    "recomputed_rise_months": row["recomputed_rise_months"],
                    "minimum_matches_official": row["minimum_matches_official"],
                    "maximum_matches_official": row["maximum_matches_official"],
                    "difference_explanation": row["difference_explanation"],
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare official and recomputed SILSO Version 2.0 cycle extrema"
    )
    parser.add_argument(
        "--cycles",
        default="21-24",
        help="Cycle selector, for example 21-24 or 21,23,24",
    )
    parser.add_argument(
        "--smoothed-file",
        help="Use a local SN_ms_tot_V2.0.csv instead of downloading it",
    )
    parser.add_argument(
        "--extrema-file",
        help="Use a local TableCyclesMiMa.txt instead of downloading it",
    )
    parser.add_argument(
        "--output-dir",
        default="./artifacts/silso-cycle-reproduction",
        help="Directory for JSON, CSV, and exact downloaded source files",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    smoothed_text = _read_or_fetch(args.smoothed_file, SMOOTHED_URL)
    extrema_text = _read_or_fetch(args.extrema_file, EXTREMA_URL)

    (output_dir / "SN_ms_tot_V2.0.csv").write_text(smoothed_text, encoding="utf-8")
    (output_dir / "TableCyclesMiMa.txt").write_text(extrema_text, encoding="utf-8")

    selected = parse_cycle_selector(args.cycles)
    rows = build_comparison(
        selected,
        parse_official_cycles(extrema_text),
        parse_smoothed_series(smoothed_text),
    )
    payload = {
        "source": SOURCE_VERSION,
        "source_urls": {
            "smoothed_monthly_total": SMOOTHED_URL,
            "official_cycle_extrema": EXTREMA_URL,
        },
        "boundary_rule": (
            "Each minimum is searched between the previous and current official "
            "maxima; each maximum is searched from the current official minimum "
            "up to (but excluding) the next official minimum."
        ),
        "cycles": rows,
    }
    (output_dir / "cycle_extrema_comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "cycle_extrema_comparison.csv", rows)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
