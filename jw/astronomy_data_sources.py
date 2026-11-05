"""Small, bounded query adapters for public astronomy data services."""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

_USER_AGENT = "JW-Astronomy-Data/1.0"
_MAX_RESPONSE_BYTES = 5_000_000
_MAX_ROWS = 200

ASTRONOMY_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "exoplanet-archive-tap",
        "title_zh": "NASA Exoplanet Archive",
        "provider": "NASA / Caltech-IPAC",
        "category": "astronomy",
        "query_mode": "tap",
        "authority_url": "https://exoplanetarchive.ipac.caltech.edu/TAP/sync",
        "docs_url": "https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html",
        "default_query": "SELECT TOP 100 pl_name, hostname, discoverymethod, disc_year, pl_rade, pl_bmasse FROM ps ORDER BY pl_name",
        "description": "系外行星与宿主星参数，支持 TAP/ADQL 风格的只读表查询。",
    },
    {
        "id": "gaia-archive-tap",
        "title_zh": "ESA Gaia Archive",
        "provider": "ESA Gaia / ESAC",
        "category": "astronomy",
        "query_mode": "tap",
        "authority_url": "https://gea.esac.esa.int/tap-server/tap/sync",
        "docs_url": "https://www.cosmos.esa.int/web/gaia-users/archive",
        "default_query": "SELECT TOP 100 source_id, ra, dec, parallax, pmra, pmdec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE random_index < 1000000 AND parallax IS NOT NULL",
        "description": "Gaia DR3 天体测量与测光星表，支持 TAP/ADQL 查询。",
    },
    {
        "id": "mast-archive",
        "title_zh": "MAST 任务档案",
        "provider": "NASA / STScI",
        "category": "astronomy",
        "query_mode": "mast",
        "authority_url": "https://mast.stsci.edu/api/v0/invoke",
        "docs_url": "https://mast.stsci.edu/api/v0/",
        "default_query": "M31",
        "description": "按天体名称解析坐标并检索 Hubble、TESS、JWST 等任务观测。",
    },
    {
        "id": "jpl-horizons",
        "title_zh": "JPL Horizons",
        "provider": "NASA / JPL Solar System Dynamics",
        "category": "astronomy",
        "query_mode": "horizons",
        "authority_url": "https://ssd.jpl.nasa.gov/api/horizons.api",
        "docs_url": "https://ssd-api.jpl.nasa.gov/doc/horizons.html",
        "default_query": "Mars",
        "description": "查询太阳系天体标识与基本资料；当前面板查询对象元数据。",
    },
    {
        "id": "noaa-goes-xrs",
        "title_zh": "NOAA GOES XRS 近一天",
        "provider": "NOAA Space Weather Prediction Center",
        "category": "solar",
        "query_mode": "goes",
        "authority_url": "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json",
        "docs_url": "https://www.swpc.noaa.gov/products/goes-x-ray-flux",
        "default_query": "",
        "description": "GOES 软 X 射线观测的近一天时间序列；历史耀斑研究需使用归档产品。",
    },
)

_BY_ID = {str(source["id"]): source for source in ASTRONOMY_DATA_SOURCES}


def describe_astronomy_data_sources() -> list[dict[str, Any]]:
    return [dict(source) for source in ASTRONOMY_DATA_SOURCES]


def _open(url: str, data: bytes | None = None, *, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json,text/csv,text/plain,*/*",
            **({"Content-Type": "application/x-www-form-urlencoded"} if data else {}),
        },
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("upstream response exceeds the 5 MB query limit")
    return payload


def _result(source_id: str, rows: list[dict[str, Any]], *, query: str) -> dict[str, Any]:
    columns = list(rows[0]) if rows else []
    return {
        "status": "ok",
        "source_id": source_id,
        "query": query,
        "columns": columns,
        "rows": rows[:_MAX_ROWS],
        "row_count": min(len(rows), _MAX_ROWS),
        "truncated": len(rows) > _MAX_ROWS,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source_url": str(_BY_ID[source_id]["authority_url"]),
    }


def _tap_query(source_id: str, query: str) -> dict[str, Any]:
    text = " ".join(query.strip().split())
    if not text or len(text) > 5_000:
        raise ValueError("query must contain 1-5000 characters")
    if not re.match(r"(?is)^SELECT\b", text) or ";" in text or "--" in text or "/*" in text:
        raise ValueError("only one read-only SELECT statement is allowed")
    if re.search(r"(?i)\b(insert|update|delete|drop|create|alter|grant|revoke|call)\b", text):
        raise ValueError("query contains a disallowed SQL keyword")
    top_match = re.search(r"(?i)\bTOP\s+(\d+)\b", text)
    if top_match:
        limit = min(max(1, int(top_match.group(1))), _MAX_ROWS)
        text = text[: top_match.start(1)] + str(limit) + text[top_match.end(1) :]
    else:
        text = re.sub(
            r"(?i)^SELECT\s+(DISTINCT\s+)?",
            lambda match: f"SELECT {match.group(1) or ''}TOP {_MAX_ROWS} ",
            text,
            count=1,
        )
    endpoint = str(_BY_ID[source_id]["authority_url"])
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": text}).encode()
    decoded = _open(endpoint, body).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = [dict(row) for row in reader if row]
    return _result(source_id, rows, query=text)


def _mast_query(target: str, radius_arcmin: Any) -> dict[str, Any]:
    name = str(target or "").strip()
    if not name or len(name) > 120:
        raise ValueError("target must contain 1-120 characters")
    try:
        radius = float(radius_arcmin or 2.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("radius_arcmin must be a number from 0.01 to 60") from exc
    if not 0.01 <= radius <= 60:
        raise ValueError("radius_arcmin must be a number from 0.01 to 60")
    endpoint = str(_BY_ID["mast-archive"]["authority_url"])
    lookup = {"service": "Mast.Name.Lookup", "format": "json", "params": {"input": name}}
    lookup_url = endpoint + "?" + urllib.parse.urlencode({"request": json.dumps(lookup)})
    resolved = json.loads(_open(lookup_url))
    hits = resolved.get("resolvedCoordinate") or []
    if not hits:
        raise ValueError(f"MAST could not resolve target {name!r}")
    ra, dec = hits[0].get("ra"), hits[0].get("decl")
    if not isinstance(ra, (int, float)) or not isinstance(dec, (int, float)):
        raise ValueError("MAST name resolution returned no valid coordinates")
    cone = {
        "service": "Mast.Caom.Cone",
        "format": "json",
        "params": {"ra": ra, "dec": dec, "radius": radius / 60, "pagesize": _MAX_ROWS, "page": 1},
    }
    cone_url = endpoint + "?" + urllib.parse.urlencode({"request": json.dumps(cone)})
    payload = json.loads(_open(cone_url))
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    rows = [dict(row) for row in rows if isinstance(row, dict)]
    return _result("mast-archive", rows, query=f"target={name}; radius_arcmin={radius:g}; ra={ra}; dec={dec}")


def _horizons_query(target: str) -> dict[str, Any]:
    command = str(target or "").strip()
    if not command or len(command) > 120:
        raise ValueError("target must contain 1-120 characters")
    params = urllib.parse.urlencode({"format": "json", "COMMAND": command, "OBJ_DATA": "YES", "MAKE_EPHEM": "NO"})
    payload = json.loads(_open(str(_BY_ID["jpl-horizons"]["authority_url"]) + "?" + params))
    result_text = str(payload.get("result") or "")
    rows = [{"line": line} for line in result_text.splitlines() if line.strip()][:200]
    if payload.get("error"):
        raise ValueError(str(payload["error"]))
    return _result("jpl-horizons", rows, query=command)


def _goes_query() -> dict[str, Any]:
    payload = json.loads(_open(str(_BY_ID["noaa-goes-xrs"]["authority_url"])))
    if not isinstance(payload, list):
        raise ValueError("NOAA GOES endpoint returned an unexpected payload")
    rows = [dict(row) for row in payload[-_MAX_ROWS:] if isinstance(row, dict)]
    return _result("noaa-goes-xrs", rows, query="latest one-day GOES XRS")


def query_astronomy_data_source(source_id: str, params: dict[str, Any]) -> dict[str, Any]:
    source = _BY_ID.get(source_id)
    if source is None:
        raise KeyError(f"unknown astronomy data source: {source_id}")
    mode = source["query_mode"]
    if mode == "tap":
        return _tap_query(source_id, str(params.get("query") or source["default_query"]))
    if mode == "mast":
        return _mast_query(params.get("target", source["default_query"]), params.get("radius_arcmin", 2))
    if mode == "horizons":
        return _horizons_query(str(params.get("target") or source["default_query"]))
    if mode == "goes":
        return _goes_query()
    raise ValueError(f"unsupported query mode: {mode}")


__all__ = ["ASTRONOMY_DATA_SOURCES", "describe_astronomy_data_sources", "query_astronomy_data_source"]
