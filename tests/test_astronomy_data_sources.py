from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.astronomy_data_sources import (
    describe_astronomy_data_sources,
    query_astronomy_data_source,
)
from jw.langgraph_dev import data_sources_api
from jw.tools import scientific_data as scientific_data_tools


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


def test_catalog_contains_api_services_and_goes() -> None:
    sources = {source["id"]: source for source in describe_astronomy_data_sources()}
    assert set(sources) == {
        "exoplanet-archive-tap",
        "gaia-archive-tap",
        "mast-archive",
        "jpl-horizons",
        "noaa-goes-xrs",
    }
    assert sources["gaia-archive-tap"]["query_mode"] == "tap"
    assert sources["noaa-goes-xrs"]["category"] == "solar"


def test_tap_query_applies_row_cap_and_returns_csv_rows(monkeypatch) -> None:
    sent: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        sent["url"] = request.full_url
        sent["body"] = request.data.decode()
        sent["timeout"] = timeout
        return _Response(b"pl_name,hostname\nKepler-1 b,Kepler-1\n")

    monkeypatch.setattr("jw.astronomy_data_sources.urllib.request.urlopen", fake_urlopen)

    result = query_astronomy_data_source(
        "exoplanet-archive-tap",
        {"query": "SELECT DISTINCT TOP 500 pl_name, hostname FROM ps"},
    )

    assert result["status"] == "ok"
    assert result["columns"] == ["pl_name", "hostname"]
    assert result["rows"] == [{"pl_name": "Kepler-1 b", "hostname": "Kepler-1"}]
    assert "TOP+200" in str(sent["body"])
    assert "FORMAT=csv" in str(sent["body"])
    assert sent["timeout"] == 20.0


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM ps",
        "SELECT * FROM ps; DROP TABLE ps",
        "SELECT * FROM ps -- comment",
    ],
)
def test_tap_rejects_non_read_only_or_multi_statement_queries(query: str) -> None:
    with pytest.raises(ValueError, match=r"read-only|disallowed"):
        query_astronomy_data_source("exoplanet-archive-tap", {"query": query})


def test_mast_resolves_target_then_queries_cone(monkeypatch) -> None:
    calls: list[dict] = []
    payloads = [
        json.dumps({"resolvedCoordinate": [{"ra": 10.0, "decl": -20.0}]}).encode(),
        json.dumps({"data": [{"obs_id": "mast123", "obs_collection": "TESS"}]}).encode(),
    ]

    def fake_urlopen(request, *, timeout):
        del timeout
        import urllib.parse

        request_json = json.loads(
            urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)[
                "request"
            ][0]
        )
        calls.append(request_json)
        return _Response(payloads.pop(0))

    monkeypatch.setattr("jw.astronomy_data_sources.urllib.request.urlopen", fake_urlopen)

    result = query_astronomy_data_source(
        "mast-archive", {"target": "M31", "radius_arcmin": 3}
    )

    assert len(calls) == 2
    assert calls[0]["service"] == "Mast.Name.Lookup"
    assert calls[1]["service"] == "Mast.Caom.Cone"
    assert calls[1]["params"]["radius"] == 0.05
    assert result["rows"][0]["obs_collection"] == "TESS"


def test_data_source_api_lists_astronomy_entries_and_accepts_toggles(
    tmp_path, monkeypatch
) -> None:
    settings_path = tmp_path / "source-settings.json"
    monkeypatch.setattr(data_sources_api, "_latest_registrations", lambda: {})
    monkeypatch.setattr(
        "jw.data_source_settings.default_settings_path", lambda: settings_path
    )

    catalog = data_sources_api._fetch_data_sources()
    ids = {source["id"] for source in catalog["sources"]}
    assert "gaia-archive-tap" in ids
    assert "noaa-goes-xrs" in ids
    assert catalog["status"] == "ok"

    updated = data_sources_api._set_data_source_enabled("gaia-archive-tap", False)
    assert updated == {"id": "gaia-archive-tap", "enabled": False}


def test_agent_query_writes_csv_and_upstream_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(scientific_data_tools, "_workspace", lambda _config: tmp_path)
    monkeypatch.setattr("jw.data_source_settings.load_source_settings", lambda: {})
    monkeypatch.setattr(
        "jw.astronomy_data_sources.query_astronomy_data_source",
        lambda _source, _params: {
            "status": "ok",
            "source_id": "exoplanet-archive-tap",
            "query": "SELECT TOP 1 pl_name FROM ps",
            "columns": ["pl_name"],
            "rows": [{"pl_name": "Kepler-1 b"}],
            "row_count": 1,
            "truncated": False,
            "fetched_at": "2026-11-05T01:02:03+00:00",
            "source_url": "https://exoplanetarchive.ipac.caltech.edu/TAP/sync",
        },
    )

    result = json.loads(
        scientific_data_tools.sci_astronomy_query.invoke(
            {"source_id": "exoplanet-archive-tap", "params_json": "{}"}
        )
    )

    artifact = Path(result["artifact_path"])
    provenance = Path(result["provenance_path"])
    assert artifact.read_text(encoding="utf-8").splitlines() == [
        "pl_name",
        "Kepler-1 b",
    ]
    assert json.loads(provenance.read_text(encoding="utf-8"))["source_url"].endswith(
        "/TAP/sync"
    )
