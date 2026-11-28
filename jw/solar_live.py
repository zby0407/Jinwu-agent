"""Live solar observations shared by the data center and the solar dashboard.

Every product here is an official machine-readable NOAA SWPC feed or a
NASA/NOAA browse image; nothing is scraped from presentation websites.  A
small in-process cache keeps one copy per product so the data-center query
panel and the "实时观测" dashboard read the same sample, and a failed upstream
refresh falls back to the last good value instead of blanking the UI.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

_USER_AGENT = "JW-Solar-Live/1.0"
_TIMEOUT_SECONDS = 20.0
_MAX_JSON_BYTES = 8_000_000
_MAX_IMAGE_BYTES = 6_000_000
JSON_TTL_SECONDS = 120.0
IMAGE_TTL_SECONDS = 600.0

SWPC = "https://services.swpc.noaa.gov/"
XRAYS_URL = SWPC + "json/goes/primary/xrays-1-day.json"
FLARES_URL = SWPC + "json/goes/primary/xray-flares-7-day.json"
PROTONS_URL = SWPC + "json/goes/primary/integral-protons-3-day.json"
REGIONS_URL = SWPC + "json/solar_regions.json"
_SDO_LATEST = "https://sdo.gsfc.nasa.gov/assets/img/latest/"
_CCOR1_DIR = SWPC + "products/ccor1/jpegs/"
_CCOR2_DIR = SWPC + "products/ccor2/jpegs/"
_LASCO_C2 = "https://soho.nascom.nasa.gov/data/realtime/c2/1024/latest.jpg"

PROTON_ENERGIES = (10, 50, 100)


@dataclass(frozen=True)
class LiveImage:
    label: str
    instrument: str
    description: str
    credit: str
    url: str | None = None
    directory: str | None = None
    pattern: str | None = None


LIVE_IMAGES: dict[str, LiveImage] = {
    "hmi": LiveImage(
        "HMI 连续谱",
        "SDO/HMI",
        "光球白光强度图，用于识别黑子群",
        "NASA/SDO 与 HMI 科学团队",
        url=_SDO_LATEST + "latest_1024_HMIIF.jpg",
    ),
    "aia193": LiveImage(
        "AIA 193 Å",
        "SDO/AIA",
        "日冕极紫外，冕洞表现为暗区",
        "NASA/SDO 与 AIA 科学团队",
        url=_SDO_LATEST + "latest_1024_0193.jpg",
    ),
    "aia131": LiveImage(
        "AIA 131 Å",
        "SDO/AIA",
        "高温耀斑等离子体",
        "NASA/SDO 与 AIA 科学团队",
        url=_SDO_LATEST + "latest_1024_0131.jpg",
    ),
    "ccor1": LiveImage(
        "CCOR-1",
        "GOES-19/CCOR-1",
        "白光日冕仪",
        "NOAA SWPC",
        directory=_CCOR1_DIR,
        pattern=r"(\d{8}_\d{4})_ccor1_1024by960\.jpg",
    ),
    "ccor2": LiveImage(
        "CCOR-2",
        "SOLAR-1/CCOR-2",
        "日地 L1 点白光日冕仪",
        "NOAA SWPC",
        directory=_CCOR2_DIR,
        pattern=r"(\d{8}_\d{4})_ccor2_1024by960\.jpg",
    ),
    "lasco": LiveImage(
        "LASCO C2",
        "SOHO/LASCO",
        "白光日冕仪",
        "ESA/NASA SOHO LASCO",
        url=_LASCO_C2,
    ),
}


class _Entry:
    __slots__ = ("error", "fetched", "lock", "value")

    def __init__(self) -> None:
        self.value: Any = None
        self.fetched = 0.0
        self.error: str | None = None
        self.lock = threading.Lock()


_CACHE: dict[str, _Entry] = {}
_CACHE_GUARD = threading.Lock()


def _cached(
    key: str, ttl: float, loader: Callable[[], Any]
) -> tuple[Any, float, str | None]:
    """Return ``(value, fetched_monotonic, last_error)`` with stale fallback."""
    with _CACHE_GUARD:
        entry = _CACHE.setdefault(key, _Entry())
    with entry.lock:
        if entry.value is not None and time.monotonic() - entry.fetched < ttl:
            return entry.value, entry.fetched, entry.error
        try:
            entry.value = loader()
            entry.fetched = time.monotonic()
            entry.error = None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            entry.error = str(exc) or exc.__class__.__name__
            if entry.value is None:
                raise
            # Retry a failing upstream at most once per TTL window.
            entry.fetched = time.monotonic()
        return entry.value, entry.fetched, entry.error


def clear_cache() -> None:
    with _CACHE_GUARD:
        _CACHE.clear()


def _open(url: str, limit: int) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        data = response.read(limit + 1)
        modified = response.headers.get("Last-Modified") if response.headers else None
    if len(data) > limit:
        raise ValueError(f"upstream response exceeds the size limit: {url}")
    return data, modified


def _json_rows(url: str) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        payload = json.loads(_open(url, _MAX_JSON_BYTES)[0])
        if not isinstance(payload, list):
            raise ValueError(f"unexpected payload from {url}")
        return [row for row in payload if isinstance(row, dict)]

    return _cached(url, JSON_TTL_SECONDS, load)[0]


def xray_rows() -> list[dict[str, Any]]:
    return _json_rows(XRAYS_URL)


def flare_rows() -> list[dict[str, Any]]:
    return _json_rows(FLARES_URL)


def proton_rows() -> list[dict[str, Any]]:
    return _json_rows(PROTONS_URL)


def region_rows() -> list[dict[str, Any]]:
    return _json_rows(REGIONS_URL)


def _utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _millis(stamp: datetime) -> int:
    return int(stamp.timestamp() * 1000)


def _positive(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return None


def flare_class(flux: float | None) -> str | None:
    """GOES class label (e.g. ``C2.1``) for a 0.1–0.8 nm flux in W/m²."""
    if flux is None or flux <= 0:
        return None
    for letter, base in (
        ("X", 1e-4),
        ("M", 1e-5),
        ("C", 1e-6),
        ("B", 1e-7),
        ("A", 1e-8),
    ):
        if flux >= base or letter == "A":
            value = flux / base
            return (
                f"{letter}{math.floor(value * 10) / 10:.1f}"
                if value < 10
                else f"{letter}{value:.0f}"
            )
    return None


def xray_summary(
    rows: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    samples: dict[int, float | None] = {}
    for row in rows:
        if row.get("energy") != "0.1-0.8nm" or not row.get("time_tag"):
            continue
        samples[_millis(_utc(str(row["time_tag"])))] = _positive(row.get("flux"))
    valid = [stamp for stamp, value in samples.items() if value is not None]
    if not valid:
        raise ValueError("NOAA GOES returned no valid 0.1-0.8 nm samples")
    end = max(valid)
    points = [
        [stamp, samples[stamp]]
        for stamp in sorted(samples)
        if stamp >= end - 86_400_000
    ]

    def peak(hours: float) -> float | None:
        cutoff = end - int(hours * 3_600_000)
        values = [
            value for stamp, value in points if stamp >= cutoff and value is not None
        ]
        return max(values) if values else None

    current = samples[end]
    return {
        "points": points,
        "latest_at": datetime.fromtimestamp(end / 1000, UTC).isoformat(),
        "current": {"flux": current, "class": flare_class(current)},
        "max_2h": {"flux": peak(2), "class": flare_class(peak(2))},
        "max_24h": {"flux": peak(24), "class": flare_class(peak(24))},
        "source_url": XRAYS_URL,
    }


def recent_flares(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        begin = row.get("begin_time")
        grade = row.get("max_class")
        if (
            not begin
            or not isinstance(grade, str)
            or not re.fullmatch(r"[ABCMX]\d+(?:\.\d+)?", grade)
        ):
            continue
        events[str(begin)] = {
            "begin_at": _utc(str(begin)).isoformat(),
            "peak_at": _utc(str(row["max_time"])).isoformat()
            if row.get("max_time")
            else None,
            "end_at": _utc(str(row["end_time"])).isoformat()
            if row.get("end_time")
            else None,
            "class": grade,
        }
    return [events[key] for key in sorted(events, reverse=True)[:limit]]


def proton_series(rows: list[dict[str, Any]]) -> dict[str, Any]:
    series: dict[str, list[list[Any]]] = {}
    for energy in PROTON_ENERGIES:
        label = f">={energy} MeV"
        samples = {
            _millis(_utc(str(row["time_tag"]))): _positive(row.get("flux"))
            for row in rows
            if row.get("energy") == label and row.get("time_tag")
        }
        series[f"gte{energy}"] = [[stamp, samples[stamp]] for stamp in sorted(samples)]
    if not any(value is not None for points in series.values() for _, value in points):
        raise ValueError("NOAA GOES returned no valid proton samples")
    latest = next(
        (value for _, value in reversed(series["gte10"]) if value is not None), None
    )
    return {
        "series": series,
        "latest_gte10": latest,
        "s_scale": _s_scale(latest),
        "source_url": PROTONS_URL,
    }


def _s_scale(flux: float | None) -> int:
    if flux is None:
        return 0
    return sum(flux >= threshold for threshold in (10, 100, 1e3, 1e4, 1e5))


def solar_b0_degrees(at: datetime) -> float:
    """Heliographic latitude of the disk centre (B0), Meeus low-precision."""
    jd = at.timestamp() / 86_400 + 2_440_587.5
    t = (jd - 2_451_545.0) / 36_525
    mean_longitude = 280.46646 + 36_000.76983 * t
    anomaly = math.radians(357.52911 + 35_999.05029 * t)
    centre = (1.914602 - 0.004817 * t) * math.sin(anomaly) + 0.019993 * math.sin(
        2 * anomaly
    )
    true_longitude = mean_longitude + centre
    apparent = (
        true_longitude
        - 0.00569
        - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
    )
    node = 73.6667 + 1.3958333 * (jd - 2_396_758.0) / 36_525
    inclination = math.radians(7.25)
    return math.degrees(
        math.asin(math.sin(math.radians(apparent - node)) * math.sin(inclination))
    )


def _synodic_rate(latitude: float) -> float:
    """Sunspot-tracer differential rotation (Newton & Nunn 1951), deg/day synodic."""
    return 14.368 - 2.69 * math.sin(math.radians(latitude)) ** 2 - 0.9856


def project_region(latitude: float, west_longitude: float, b0: float) -> dict[str, Any]:
    """Orthographic disk position in solar radii (x → west, y → north)."""
    lat, lon, tilt = (math.radians(value) for value in (latitude, west_longitude, b0))
    x = math.cos(lat) * math.sin(lon)
    y = math.sin(lat) * math.cos(tilt) - math.cos(lat) * math.cos(lon) * math.sin(tilt)
    depth = math.cos(lat) * math.cos(lon) * math.cos(tilt) + math.sin(lat) * math.sin(
        tilt
    )
    return {"x": round(x, 4), "y": round(y, 4), "visible": depth > 0}


def active_regions(
    rows: list[dict[str, Any]], *, at: datetime | None = None
) -> dict[str, Any]:
    dated = [row for row in rows if row.get("observed_date")]
    if not dated:
        raise ValueError("NOAA solar regions feed is empty")
    report_date = max(str(row["observed_date"]) for row in dated)
    # SRS positions are valid at 24:00 UT of the observed day.
    reference = datetime.fromisoformat(report_date).replace(tzinfo=UTC) + timedelta(
        days=1
    )
    moment = at or datetime.now(UTC)
    elapsed_days = (moment - reference).total_seconds() / 86_400
    b0 = solar_b0_degrees(moment)
    regions = []
    for row in dated:
        if str(row["observed_date"]) != report_date or not row.get("location"):
            continue
        latitude, east_longitude = row.get("latitude"), row.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(
            east_longitude, (int, float)
        ):
            continue
        west_longitude = (
            -float(east_longitude) + _synodic_rate(float(latitude)) * elapsed_days
        )
        regions.append(
            {
                "number": row.get("region"),
                "location": row.get("location"),
                "latitude": latitude,
                "longitude_west": round(west_longitude, 2),
                "spots": row.get("number_spots"),
                "area": row.get("area"),
                "spot_class": row.get("spot_class"),
                "mag_class": row.get("mag_class"),
                "c_probability": row.get("c_flare_probability"),
                "m_probability": row.get("m_flare_probability"),
                "x_probability": row.get("x_flare_probability"),
                "first_date": row.get("first_date"),
                "disk": project_region(float(latitude), west_longitude, b0),
            }
        )
    regions.sort(key=lambda region: region["number"] or 0)
    return {
        "report_date": report_date,
        "reference_at": reference.isoformat(),
        "projected_at": moment.isoformat(),
        "b0_deg": round(b0, 3),
        "rows": regions,
        "source_url": REGIONS_URL,
    }


def _latest_directory_image(spec: LiveImage) -> tuple[str, datetime]:
    assert spec.directory
    assert spec.pattern
    listing = _open(spec.directory, 2_000_000)[0].decode("ascii", errors="ignore")
    matches = sorted(set(re.findall(rf"({spec.pattern})", listing)))
    if not matches:
        raise ValueError(f"no images listed in {spec.directory}")
    name, stamp = matches[-1]
    observed = datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=UTC)
    return spec.directory + name, observed


def live_image(key: str) -> dict[str, Any]:
    """Latest JPEG bytes and metadata for one ``LIVE_IMAGES`` product."""
    spec = LIVE_IMAGES[key]

    def load() -> dict[str, Any]:
        observed: datetime | None = None
        if spec.directory:
            url, observed = _latest_directory_image(spec)
        else:
            assert spec.url
            url = spec.url
        data, modified = _open(url, _MAX_IMAGE_BYTES)
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError(f"invalid JPEG from {url}")
        if observed is None and modified:
            observed = parsedate_to_datetime(modified).astimezone(UTC)
        return {
            "bytes": data,
            "url": url,
            "observed_at": observed.isoformat() if observed else None,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    return _cached(f"image:{key}", IMAGE_TTL_SECONDS, load)[0]


def _section(builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "ok", **builder()}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc) or exc.__class__.__name__}


def _image_meta(key: str) -> dict[str, Any]:
    spec = LIVE_IMAGES[key]
    image = live_image(key)
    return {
        "label": spec.label,
        "instrument": spec.instrument,
        "description": spec.description,
        "credit": spec.credit,
        "source_url": image["url"],
        "observed_at": image["observed_at"],
        "fetched_at": image["fetched_at"],
    }


HISTORY_ROOT = "https://sdo.gsfc.nasa.gov/assets/img/browse"
#: Browsable SDO quick-look archive channels, keyed like the live image keys.
HISTORY_CHANNELS = {
    "hmi": {"token": "HMIIF", "label": "HMI 连续谱", "instrument": "SDO/HMI"},
    "hmimag": {"token": "HMIBC", "label": "HMI 彩色磁图", "instrument": "SDO/HMI"},
    "aia193": {"token": "0193", "label": "AIA 193 Å", "instrument": "SDO/AIA"},
    "aia131": {"token": "0131", "label": "AIA 131 Å", "instrument": "SDO/AIA"},
}
HISTORY_FIRST_DATE = "2010-05-01"  # SDO prime-mission start of the browse archive
HISTORY_LISTING_TTL = 12 * 3600  # frozen archive days; relisted twice daily
HISTORY_IMAGE_TTL = 30 * 86400  # archived quick-looks never change
_HISTORY_STAMP = re.compile(r"^(\d{8})_(\d{6})$")
_HISTORY_FILE = re.compile(r"(\d{8}_\d{6})_1024_(HMIIF|HMIBC|0193|0131)\.jpg")


def _validate_history_date(date: str) -> str:
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError(f"invalid history date: {date!r}") from None
    first = datetime.strptime(HISTORY_FIRST_DATE, "%Y-%m-%d").replace(tzinfo=UTC)
    if parsed < first or parsed.date() > datetime.now(UTC).date():
        raise ValueError(f"history date outside the SDO archive: {date}")
    return date


def history_listing(date: str) -> dict[str, Any]:
    """Available quick-look timestamps per channel for one UTC archive day."""
    date = _validate_history_date(date)

    def load() -> dict[str, Any]:
        year, month, day = date.split("-")
        try:
            listing = _open(f"{HISTORY_ROOT}/{year}/{month}/{day}/", _MAX_JSON_BYTES)[
                0
            ].decode("ascii", errors="ignore")
        except OSError as exc:
            raise ValueError(f"no SDO browse archive for {date}") from exc
        stamps: dict[str, set[str]] = {key: set() for key in HISTORY_CHANNELS}
        for stamp, token in _HISTORY_FILE.findall(listing):
            if not stamp.startswith(date.replace("-", "")):
                continue
            for key, spec in HISTORY_CHANNELS.items():
                if token == spec["token"]:
                    stamps[key].add(stamp.split("_", 1)[1])
        if not any(stamps.values()):
            raise ValueError(f"no SDO browse images listed for {date}")
        return {key: sorted(times) for key, times in stamps.items()}

    found = _cached(f"history:{date}", HISTORY_LISTING_TTL, load)[0]
    year, month, day = date.split("-")
    url = f"{HISTORY_ROOT}/{year}/{month}/{day}/"
    return {
        "status": "ok",
        "date": date,
        "channels": {
            key: {
                "label": spec["label"],
                "instrument": spec["instrument"],
                "times": found.get(key, []),
                "count": len(found.get(key, [])),
            }
            for key, spec in HISTORY_CHANNELS.items()
        },
        "source_url": url,
    }


def history_image(channel: str, stamp: str) -> dict[str, Any]:
    """JPEG bytes for one archived quick-look, validated against the archive layout."""
    if channel not in HISTORY_CHANNELS:
        raise ValueError(f"unknown history channel: {channel}")
    match = _HISTORY_STAMP.fullmatch(stamp)
    if not match:
        raise ValueError(f"invalid history timestamp: {stamp!r}")
    day, _tod = match.groups()
    try:
        date = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError(f"invalid history timestamp: {stamp!r}") from None
    _validate_history_date(date.strftime("%Y-%m-%d"))
    token = HISTORY_CHANNELS[channel]["token"]
    url = f"{HISTORY_ROOT}/{day[:4]}/{day[4:6]}/{day[6:8]}/{stamp}_1024_{token}.jpg"

    def load() -> dict[str, Any]:
        data, _modified = _open(url, _MAX_IMAGE_BYTES)
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError(f"invalid JPEG from {url}")
        return {"bytes": data, "url": url}

    return _cached(f"history-image:{channel}:{stamp}", HISTORY_IMAGE_TTL, load)[0]


def build_live_overview() -> dict[str, Any]:
    """Assemble every dashboard panel; one failing feed never hides the rest."""
    with ThreadPoolExecutor(max_workers=len(LIVE_IMAGES) + 4) as pool:
        # Warm every upstream concurrently; ``_section`` then reads the cache.
        warm = [
            pool.submit(_section, lambda key=key: _image_meta(key))
            for key in LIVE_IMAGES
        ]
        warm += [
            pool.submit(loader)
            for loader in (xray_rows, flare_rows, proton_rows, region_rows)
        ]
        images = dict(
            zip(
                LIVE_IMAGES,
                (future.result() for future in warm[: len(LIVE_IMAGES)]),
                strict=True,
            )
        )
        for future in warm[len(LIVE_IMAGES) :]:
            future.exception()
    observed = images["hmi"].get("observed_at")
    projected_at = _utc(observed) if isinstance(observed, str) else None
    return {
        "status": "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "xray": _section(lambda: xray_summary(xray_rows())),
        "flares": _section(
            lambda: {"events": recent_flares(flare_rows()), "source_url": FLARES_URL}
        ),
        "protons": _section(lambda: proton_series(proton_rows())),
        "regions": _section(lambda: active_regions(region_rows(), at=projected_at)),
        "images": images,
    }


__all__ = [
    "HISTORY_CHANNELS",
    "HISTORY_FIRST_DATE",
    "LIVE_IMAGES",
    "PROTONS_URL",
    "REGIONS_URL",
    "XRAYS_URL",
    "active_regions",
    "build_live_overview",
    "clear_cache",
    "flare_class",
    "history_image",
    "history_listing",
    "live_image",
    "project_region",
    "proton_rows",
    "proton_series",
    "region_rows",
    "solar_b0_degrees",
    "xray_rows",
    "xray_summary",
]
