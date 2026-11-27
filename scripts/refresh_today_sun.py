"""Publish a daily Today Sun snapshot independently of the JW services."""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

from solar_cache import WORKSPACE, cache_lock, download, publish, utc_time
from solar_products import PRODUCTS, collect_products

BEIJING = timezone(timedelta(hours=8))
SDO = "https://sdo.gsfc.nasa.gov/assets/img/browse/"
NOAA = "https://services.swpc.noaa.gov/json/"
CHANNELS = ("0171", "0193", "0304", "HMII", "HMIB")
FRAME = re.compile(r"(\d{8}_\d{6})_512_(0171|0193|0304|HMII|HMIB)\.jpg")


def image_frames(now: datetime) -> list[dict]:
    frames: dict[str, tuple[datetime, str]] = {}
    for offset in (0, 1):
        day = now - timedelta(days=offset)
        directory = SDO + day.strftime("%Y/%m/%d/")
        try:
            listing = download(directory).decode("ascii", errors="ignore")
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        for match in FRAME.finditer(listing):
            stamp, channel = match.groups()
            observed = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            if observed <= now and (channel not in frames or observed > frames[channel][0]):
                frames[channel] = (observed, directory + match.group())
        if len(frames) == len(CHANNELS):
            break

    if "0193" not in frames:
        raise ValueError("NASA SDO has no recent AIA 193 Å image")

    def read_frame(channel: str) -> dict:
        observed, url = frames[channel]
        data = download(url, limit=500_000)
        if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
            raise ValueError(f"Invalid JPEG: {url}")
        return {
            "channel": channel,
            "observedAt": observed.isoformat(),
            "url": url,
            "jpeg": base64.b64encode(data).decode("ascii"),
        }

    with ThreadPoolExecutor(max_workers=len(CHANNELS)) as pool:
        return list(pool.map(read_frame, (channel for channel in CHANNELS if channel in frames)))


def xray_flux() -> dict:
    rows = json.loads(download(NOAA + "goes/primary/xrays-1-day.json"))
    points = {}
    for row in rows:
        if row["energy"] != "0.1-0.8nm":
            continue
        timestamp = utc_time(row["time_tag"])
        value = row["flux"]
        points[timestamp] = (
            value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None
        )
    valid = [timestamp for timestamp, value in points.items() if value is not None]
    if not valid:
        raise ValueError("NOAA GOES has no valid flux samples")
    end = max(valid)
    start = end - timedelta(hours=24)
    return {
        "startAt": start.isoformat(),
        "endAt": end.isoformat(),
        "points": [[int(t.timestamp() * 1000), points[t]] for t in sorted(points) if start <= t <= end],
    }


def flare_events() -> list[dict]:
    rows = json.loads(download(NOAA + "goes/primary/xray-flares-7-day.json"))
    events = {}
    for row in rows:
        start = utc_time(row["begin_time"])
        peak = row.get("max_time")
        grade = row.get("max_class")
        events[start] = {
            "startAt": start.isoformat(),
            "peakAt": utc_time(peak).isoformat() if peak else None,
            "endAt": utc_time(row["end_time"]).isoformat() if row.get("end_time") else None,
            "grade": grade if isinstance(grade, str) and re.fullmatch(r"[ABCMX]\d+(?:\.\d+)?", grade) else None,
        }
    return [events[start] for start in sorted(events, reverse=True)]


def active_regions() -> dict:
    rows = json.loads(download(NOAA + "solar_regions.json"))
    date = max(row["observed_date"] for row in rows)
    regions = {
        row["region"]: {
            "number": row["region"],
            "location": row.get("location"),
            "spots": row.get("number_spots"),
            "area": row.get("area"),
            "spotClass": row.get("spot_class"),
            "magneticClass": row.get("mag_class"),
        }
        for row in rows
        if row["observed_date"] == date and row.get("location")
    }
    return {"reportDate": date, "rows": [regions[number] for number in sorted(regions)]}


def read_previous(target: Path) -> dict:
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) and data.get("version") in (1, 2) else {}


def refresh(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "snapshot.json"
    with cache_lock(cache_dir / "refresh.lock"):
        now = datetime.now(timezone.utc)
        date = now.astimezone(BEIJING).date().isoformat()
        previous = read_previous(target)
        same_day = previous.get("date") == date
        previous_products = previous.get("products", {})
        current_products = {
            key for key, product in previous_products.items()
            if utc_time(product["cachedAt"]).astimezone(BEIJING).date() == now.astimezone(BEIJING).date()
        }
        pending = PRODUCTS.keys() - current_products
        if same_day and not pending:
            print(f"Snapshot for {date} (UTC+8) already exists; skipped network requests.")
            return

        if same_day:
            snapshot = previous
            products = collect_products(previous_products, pending)
        else:
            with ThreadPoolExecutor(max_workers=5) as pool:
                images = pool.submit(image_frames, now)
                flux = pool.submit(xray_flux)
                flares = pool.submit(flare_events)
                regions = pool.submit(active_regions)
                additions = pool.submit(collect_products, previous_products, pending)
                products = additions.result()
                snapshot = {
                    "date": date,
                    "images": images.result(),
                    "flux": flux.result(),
                    "flares": flares.result(),
                    "regions": regions.result(),
                }
        snapshot.update(
            version=2,
            products={**previous_products, **products},
            fetchedAt=datetime.now(timezone.utc).isoformat(),
        )
        publish(target, snapshot)
        print(f"Saved snapshot for {date} (UTC+8): {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=WORKSPACE,
        help="JW workspace directory (defaults to JW_WORKSPACE_DIR or the checkout's workspace)",
    )
    args = parser.parse_args()
    refresh(args.workspace.expanduser().resolve() / "today-sun")
