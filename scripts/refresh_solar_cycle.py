"""Publish a frozen solar-cycle archive. Run explicitly when replacing the archive."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from solar_cache import WORKSPACE, cache_lock, download, publish

SILSO = "https://www.sidc.be/SILSO/"
NOAA_INDICES = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"


def sunspots(product):
    records = []
    text = download(SILSO + f"INFO/{product}.php").decode("ascii")
    for row in text.splitlines():
        year, month, _, number, *_ = row.split(";")
        value = float(number)
        records.append([f"{year}-{month}", value if value >= 0 else None])
    if len(records) < 3000:
        raise ValueError("Incomplete SILSO series")
    return records


def cycle_bounds():
    records = []
    text = download(SILSO + "DATA/Cycles/TableCyclesMiMa.txt").decode("ascii")
    for line in text.splitlines():
        row = line.split()
        if not row or not row[0].isdigit():
            continue
        records.append({
            "number": int(row[0]),
            "start": f"{row[1]}-{row[2]}",
            "minimum": float(row[3]),
            "peakAt": f"{row[4]}-{row[5]}" if len(row) >= 7 else None,
            "peak": float(row[6]) if len(row) >= 7 else None,
        })
    if len(records) < 25:
        raise ValueError("Incomplete SILSO cycle table")
    return records


def radio_flux():
    rows = json.loads(download(NOAA_INDICES))
    return [[row["time-tag"], row["f10.7"]] for row in rows if row["f10.7"] >= 0]


def refresh(workspace):
    directory = workspace / "solar-cycle"
    directory.mkdir(parents=True, exist_ok=True)
    with cache_lock(directory / "cycle-refresh.lock"), ThreadPoolExecutor() as pool:
        monthly = pool.submit(sunspots, "snmtotcsv")
        smoothed = pool.submit(sunspots, "snmstotcsv")
        cycles = pool.submit(cycle_bounds)
        flux = pool.submit(radio_flux)
        data = {
            "version": 1,
            "frozenAt": datetime.now(timezone.utc).isoformat(),
            "monthly": monthly.result(),
            "smoothed": smoothed.result(),
            "cycles": cycles.result(),
            "f107": flux.result(),
        }
        publish(directory / "cycle.json", data)
    print(f"Saved frozen solar-cycle archive: {directory / 'cycle.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    refresh(parser.parse_args().workspace.expanduser().resolve())
