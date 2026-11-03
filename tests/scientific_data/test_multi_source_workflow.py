"""End-to-end: a research goal discovers, downloads, parses and merges data."""

from __future__ import annotations

import json

from jw.scientific_data.contracts import RetrievalState, SourceRole
from jw.scientific_data.data_providers import DataProviderSettings
from jw.scientific_data.policy import AcquisitionPolicy
from jw.scientific_data.workflow import (
    WorkflowOptions,
    contract_from_request,
    run_integration,
)

ZENODO_SEARCH = "https://zenodo.org/api/records?"
ZENODO_FILE = "https://zenodo.org/api/records/1/files/lc.csv/content"

CSV_BODY = """\
name,mjd,band,mag,mag_err
SN 2011fe,55800.0,V,15.30,0.05
SN 2011fe,55803.0,V,14.90,0.04
SN 2011fe,55806.0,V,14.60,0.04
"""


def _search_payload() -> bytes:
    return json.dumps(
        {
            "hits": {
                "hits": [
                    {
                        "id": 1,
                        "doi": "10.5281/zenodo.1",
                        "metadata": {
                            "title": "SN 2011fe photometry",
                            "publication_date": "2012-01-01",
                            "license": {"id": "cc-by-4.0"},
                        },
                        "files": [
                            {
                                "key": "lc.csv",
                                "size": len(CSV_BODY),
                                "links": {"self": ZENODO_FILE},
                            }
                        ],
                    }
                ]
            }
        }
    ).encode()


class FakeFetcher:
    """Serves the search response and the file without touching the network."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str, *, policy: AcquisitionPolicy) -> tuple[int, bytes, str]:
        del policy
        self.calls.append(url)
        if url.startswith(ZENODO_SEARCH):
            return 200, _search_payload(), "application/json"
        if url == ZENODO_FILE:
            return 200, CSV_BODY.encode(), "text/csv"
        return 404, b"", "text/plain"


def _contract():
    return contract_from_request(
        {
            "research_goal": "SN 2011fe V-band light curve from public archives",
            "domain": "snia_light_curve",
            "observables": [
                "apparent_magnitude",
                "time",
                "object",
                "band",
            ],
            "entity_class": "supernova",
        }
    )


def test_network_discovery_feeds_the_pipeline_end_to_end(tmp_path):
    fetcher = FakeFetcher()
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=_contract(),
        options=WorkflowOptions(
            allow_network=True,
            auto_discover=True,
            discovery_providers=("zenodo",),
            discovery_limit=1,
            discovery_settings=DataProviderSettings(providers=("zenodo",)),
            fetcher=fetcher,
        ),
    )

    ledger = result.ledger
    assert len(ledger.sources) == 1
    source = next(iter(ledger.sources.values()))
    assert source.provider == "zenodo"
    assert source.role is SourceRole.AUTHORED_TABLE
    assert source.retrieval is RetrievalState.ACQUIRED

    assert len(ledger.tables) == 1
    table = next(iter(ledger.tables.values()))
    assert len(table.rows) == 3
    assert len(ledger.records) == 3
    assert any(note.startswith("discovery:") for note in ledger.notes)
    assert fetcher.calls[0].startswith(ZENODO_SEARCH)
    assert fetcher.calls[1] == ZENODO_FILE


def test_auto_discover_can_be_switched_off(tmp_path):
    fetcher = FakeFetcher()
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=_contract(),
        options=WorkflowOptions(
            allow_network=True,
            auto_discover=False,
            fetcher=fetcher,
        ),
    )

    assert result.ledger.sources == {}
    assert fetcher.calls == []
