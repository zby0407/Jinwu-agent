"""Public daily products used by the Today Sun dashboard."""

import base64
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from solar_cache import download, fetch, utc_time

SWPC = "https://services.swpc.noaa.gov/"
F107 = SWPC + "products/10cm-flux-30-day.json"
PROTONS = SWPC + "json/goes/primary/integral-protons-1-day.json"
WIND = SWPC + "json/rtsw/rtsw_wind_1m.json"
MAG = SWPC + "json/rtsw/rtsw_mag_1m.json"
KP = SWPC + "products/noaa-planetary-k-index.json"
SYNOPTIC = SWPC + "images/synoptic-map.jpg"
CCOR = SWPC + "products/ccor1/jpegs/"
GONG = "https://farside.nso.edu"
CME = "https://www.sidc.be/cactus/out/cmecat.txt"


def finite(value, *, positive=False):
    if isinstance(value, (int, float)) and math.isfinite(value) and (not positive or value > 0):
        return value
    return None


def get_json(url):
    return json.loads(download(url))


def points(rows, field, *, positive=False):
    values = {
        int(utc_time(row["time_tag"]).timestamp() * 1000): finite(row.get(field), positive=positive)
        for row in rows
    }
    if not any(value is not None for value in values.values()):
        raise ValueError(f"No valid samples for {field}")
    return sorted(values.items())


def recent(rows, hours):
    cutoff = max(utc_time(row["time_tag"]) for row in rows) - timedelta(hours=hours)
    return [row for row in rows if utc_time(row["time_tag"]) >= cutoff]


def f107():
    return {"points": points(get_json(F107), "flux", positive=True)}


def protons():
    rows = get_json(PROTONS)
    return {
        f"gte{energy}": points(
            [row for row in rows if row["energy"] == f">={energy} MeV"], "flux", positive=True
        )
        for energy in (10, 100)
    }


def active_stream(url):
    rows = recent([row for row in get_json(url) if row["active"]], 24)
    # Keep the timestamps of flagged measurements so the chart leaves a gap.
    return rows, sorted({row["source"] for row in rows if row.get("source")})


def wind():
    rows, sources = active_stream(WIND)
    usable = [row if row.get("overall_quality") == 0 else {"time_tag": row["time_tag"]} for row in rows]
    return {
        "speed": points(usable, "proton_speed", positive=True),
        "density": points(usable, "proton_density", positive=True),
        "sources": sources,
    }


def magnetic_field():
    rows, sources = active_stream(MAG)
    usable = [row if row.get("overall_quality") == 0 else {"time_tag": row["time_tag"]} for row in rows]
    return {"bz": points(usable, "bz_gsm"), "sources": sources}


def kp():
    return {"points": points(recent(get_json(KP), 72), "Kp")}


def image(url, observed=None):
    data, modified = fetch(url, 8_000_000)
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ValueError(f"Invalid JPEG: {url}")
    return {
        "url": url,
        "observedAt": observed.isoformat() if observed else None,
        "modifiedAt": parsedate_to_datetime(modified).isoformat() if modified else None,
        "jpeg": base64.b64encode(data).decode("ascii"),
    }


def coronagraph():
    text = download(CCOR).decode("ascii", errors="ignore")
    name, stamp = max(re.findall(r'((\d{8}_\d{4})_ccor1_1024by960\.jpg)', text))
    return image(CCOR + name, datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc))


def far_side():
    text = download(GONG + "/").decode("ascii", errors="ignore")
    paths = re.findall(r'(/oQR/f6r/\d{6}/mrf6r\d{6}/mrf6r\d{6}t\d{4}\.jpg)', text)
    path = max(paths)
    stamp = re.search(r"(\d{6}t\d{4})\.jpg$", path)[1]
    return image(GONG + path, datetime.strptime(stamp, "%y%m%dt%H%M").replace(tzinfo=timezone.utc))


def cmes():
    catalog = download(CME).decode("ascii", errors="replace")
    issued = re.search(r"^:Issued:\s+(.+)$", catalog, re.M)
    starts = catalog.index("# CME |")
    stop = catalog.index("# Flow|", starts)
    events = []
    for line in catalog[starts:stop].splitlines()[1:]:
        fields = [part.strip() for part in line.split("|")]
        if len(fields) < 9 or not fields[0].isdigit():
            continue
        stamp = datetime.strptime(fields[1], "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
        events.append({"at": stamp.isoformat(), "angle": int(fields[4]), "speed": int(fields[5])})
    return {
        "issuedAt": datetime.strptime(issued[1], "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc).isoformat(),
        "events": sorted(events, key=lambda event: event["at"], reverse=True),
    }


PRODUCTS = {
    "f107": f107,
    "protons": protons,
    "wind": wind,
    "magnetic": magnetic_field,
    "kp": kp,
    "synoptic": lambda: image(SYNOPTIC),
    "coronagraph": coronagraph,
    "farSide": far_side,
    "cme": cmes,
}
def collect_products(previous, keys):
    products = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {key: pool.submit(PRODUCTS[key]) for key in keys}
        for key, future in futures.items():
            try:
                products[key] = {**future.result(), "cachedAt": datetime.now(timezone.utc).isoformat()}
            except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                print(f"{key} unavailable: {error}")
                if key in previous:
                    products[key] = previous[key]
    return products
