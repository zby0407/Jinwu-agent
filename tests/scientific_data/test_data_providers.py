"""Multi-source data discovery: repositories, budgets, and refusals."""

from __future__ import annotations

import json
import urllib.request

import pytest

from jw.scientific_data.contracts import (
    DataRequirementContract,
    ObservationField,
    SourceRole,
)
from jw.scientific_data.data_providers import (
    DataProviderSettings,
    ProviderError,
    discover_data_sources,
    openalex_search,
    resolve_provider_settings,
    zenodo_search,
)
from jw.scientific_data.policy import AcquisitionPolicy


def contract(**overrides):
    fields = {
        "contract_id": "contract:discover",
        "research_goal": "SN 2011fe V-band photometry",
        "entity_class": "supernova",
        "observables": (
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="apparent_magnitude", kind="quantity", unit="mag"),
        ),
        "identifier_fields": ("object",),
        "time_field": "time",
        "domain_plugin": "snia_light_curve",
        "budget": {"max_sources": 8, "max_fetches": 4, "max_tables": 20},
    }
    fields.update(overrides)
    return DataRequirementContract(**fields)


class FakeOpener:
    """Routes request URLs to canned bodies so tests never touch the network."""

    def __init__(self, routes: dict[str, bytes | Exception]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> bytes:
        del timeout
        self.calls.append(request.full_url)
        for prefix, payload in self.routes.items():
            if request.full_url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise ProviderError("http_404", request.full_url)


def _zenodo_payload(*, files: list[dict], doi: str = "10.5281/zenodo.1") -> bytes:
    return json.dumps(
        {
            "hits": {
                "hits": [
                    {
                        "id": 1,
                        "doi": doi,
                        "doi_url": f"https://doi.org/{doi}",
                        "metadata": {
                            "title": "SN 2011fe photometry",
                            "publication_date": "2012-01-01",
                            "license": {"id": "cc-by-4.0"},
                        },
                        "files": files,
                    }
                ]
            }
        }
    ).encode()


def _zenodo_file(key: str, size: int, url: str) -> dict:
    return {"key": key, "size": size, "links": {"self": url}}


def test_zenodo_files_become_downloadable_hits():
    settings = DataProviderSettings(providers=("zenodo",))
    opener = FakeOpener(
        {
            settings.zenodo_endpoint: _zenodo_payload(
                files=[
                    _zenodo_file(
                        "lc.csv",
                        1200,
                        "https://zenodo.org/api/records/1/files/lc.csv/content",
                    ),
                    _zenodo_file(
                        "meta.json",
                        200,
                        "https://zenodo.org/api/records/1/files/meta.json/content",
                    ),
                ]
            )
        }
    )

    result = zenodo_search("SN 2011fe", 5, opener=opener, settings=settings)

    assert result.status == "ok"
    assert [hit.format_hint for hit in result.hits] == ["csv", "json"]
    assert all(hit.role is SourceRole.AUTHORED_TABLE for hit in result.hits)
    assert result.hits[0].url.endswith("/files/lc.csv/content")
    assert result.hits[0].identifier == "10.5281/zenodo.1"
    assert "zenodo" in result.hits[0].notes


def test_zenodo_skips_files_above_the_size_budget():
    settings = DataProviderSettings(providers=("zenodo",), max_bytes=1_000)
    opener = FakeOpener(
        {
            settings.zenodo_endpoint: _zenodo_payload(
                files=[
                    _zenodo_file(
                        "big.zip",
                        4_000,
                        "https://zenodo.org/api/records/1/files/big.zip/content",
                    ),
                    _zenodo_file(
                        "lc.csv",
                        500,
                        "https://zenodo.org/api/records/1/files/lc.csv/content",
                    ),
                ]
            )
        }
    )

    result = zenodo_search("SN 2011fe", 5, opener=opener, settings=settings)

    assert result.status == "partial"
    assert result.skipped == ["zenodo:1/big.zip (too_large:4000)"]
    assert [hit.format_hint for hit in result.hits] == ["csv"]


def test_zenodo_record_without_files_falls_back_to_its_landing_page():
    settings = DataProviderSettings(providers=("zenodo",))
    opener = FakeOpener({settings.zenodo_endpoint: _zenodo_payload(files=[])})

    result = zenodo_search("SN 2011fe", 5, opener=opener, settings=settings)

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.role is SourceRole.CATALOG
    assert "landing_page" in hit.notes
    assert hit.url.startswith("https://doi.org/")


def test_dataverse_lists_dataset_files():
    settings = DataProviderSettings(providers=("dataverse",))
    opener = FakeOpener(
        {
            f"{settings.dataverse_endpoint}/api/search": json.dumps(
                {
                    "status": "OK",
                    "data": {
                        "items": [
                            {
                                "name": "SN 2011fe replication data",
                                "global_id": "doi:10.7910/DVN/TEST01",
                                "url": "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/TEST01",
                            }
                        ]
                    },
                }
            ).encode(),
            f"{settings.dataverse_endpoint}/api/datasets/:persistentId": json.dumps(
                {
                    "status": "OK",
                    "data": {
                        "latestVersion": {
                            "files": [
                                {
                                    "dataFile": {
                                        "id": 42,
                                        "filename": "photometry.tab",
                                        "filesize": 900,
                                    }
                                }
                            ]
                        }
                    },
                }
            ).encode(),
        }
    )

    report = discover_data_sources(
        contract(),
        providers=("dataverse",),
        allow_network=True,
        opener=opener,
        settings=settings,
    )

    assert report.status == "ok"
    record = report.found[0]
    assert record.url.endswith("/api/access/datafile/42")
    assert record.provider == "dataverse"
    assert record.role is SourceRole.AUTHORED_TABLE


def test_dataverse_listing_failure_keeps_the_landing_page():
    settings = DataProviderSettings(providers=("dataverse",))
    opener = FakeOpener(
        {
            f"{settings.dataverse_endpoint}/api/search": json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "name": "SN 2011fe replication data",
                                "global_id": "doi:10.7910/DVN/TEST01",
                                "url": "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/TEST01",
                            }
                        ]
                    }
                }
            ).encode(),
            f"{settings.dataverse_endpoint}/api/datasets/:persistentId": ProviderError(
                "timeout", "listing timed out"
            ),
        }
    )

    report = discover_data_sources(
        contract(),
        providers=("dataverse",),
        allow_network=True,
        opener=opener,
        settings=settings,
    )

    assert report.status == "ok"
    assert report.provider_status["dataverse"]["status"] == "partial"
    record = report.found[0]
    assert record.format_hint == "html"
    assert "file_listing_failed" in record.notes


def test_openalex_returns_the_open_pdf_and_marks_refereed():
    settings = DataProviderSettings(providers=("openalex",))
    opener = FakeOpener(
        {
            settings.openalex_endpoint: json.dumps(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "https://doi.org/10.1088/0004-637X/737/2/103",
                            "display_name": "SN 2011fe photometry",
                            "publication_date": "2011-06-01",
                            "open_access": {
                                "oa_status": "bronze",
                                "oa_url": "https://example.org/sn2011fe.html",
                            },
                            "locations": [
                                {
                                    "pdf_url": "https://example.org/sn2011fe.pdf",
                                    "source": {"type": "journal"},
                                },
                                {
                                    "pdf_url": "https://arxiv.org/pdf/1109.0001",
                                    "source": {"type": "repository"},
                                },
                            ],
                        }
                    ]
                }
            ).encode()
        }
    )

    result = openalex_search("SN 2011fe", 5, opener=opener, settings=settings)

    hit = result.hits[0]
    assert hit.url == "https://arxiv.org/pdf/1109.0001"
    assert hit.format_hint == "pdf"
    assert hit.role is SourceRole.PRIMARY_PUBLISHED
    assert hit.trust == pytest.approx(0.85)
    assert "oa_status:bronze" in hit.notes


def test_discovery_is_refused_without_network():
    opener = FakeOpener({})

    report = discover_data_sources(
        contract(),
        providers=("zenodo",),
        allow_network=False,
        opener=opener,
    )

    assert report.status == "policy_blocked"
    assert report.found == ()
    assert opener.calls == []
    assert report.issues[0].code.value == "source_host_denied"


def test_a_provider_failure_is_reported_not_hidden():
    settings = DataProviderSettings(providers=("zenodo", "dataverse"))
    opener = FakeOpener(
        {
            settings.zenodo_endpoint: ProviderError("timeout", "no route"),
            f"{settings.dataverse_endpoint}/api/search": json.dumps(
                {"data": {"items": []}}
            ).encode(),
        }
    )

    report = discover_data_sources(
        contract(),
        allow_network=True,
        opener=opener,
        settings=settings,
    )

    assert report.status == "partial"
    assert report.provider_status["zenodo"]["status"] == "unavailable"
    assert "timeout" in report.provider_status["zenodo"]["detail"]
    assert report.provider_status["dataverse"]["status"] == "ok"
    assert any("zenodo" in issue.detail for issue in report.issues)


def test_policy_allowlist_blocks_unlisted_provider_hosts():
    settings = DataProviderSettings(providers=("zenodo", "dataverse"))
    opener = FakeOpener({})
    policy = AcquisitionPolicy(
        allow_network=True, allowed_hosts=("dataverse.harvard.edu",)
    )

    report = discover_data_sources(
        contract(),
        allow_network=True,
        opener=opener,
        settings=settings,
        policy=policy,
    )

    assert report.provider_status["zenodo"]["status"] == "policy_blocked"
    assert not any("zenodo" in call for call in opener.calls)


def test_env_settings_override_endpoints_and_provider_set():
    settings = resolve_provider_settings(
        environ={
            "JW_DATA_PROVIDERS": "zenodo, openalex",
            "JW_ZENODO_ENDPOINT": "https://mirror.example/records",
            "OPENALEX_MAILTO": "ops@example.org",
        }
    )

    assert settings.providers == ("zenodo", "openalex")
    assert settings.zenodo_endpoint == "https://mirror.example/records"
    assert settings.mailto == "ops@example.org"
    assert settings.dataverse_endpoint == DataProviderSettings().dataverse_endpoint


def test_default_settings_query_each_repository_instead_of_unknown_combined_name():
    settings = resolve_provider_settings(environ={})
    opener = FakeOpener(
        {
            settings.zenodo_endpoint: b'{"hits":{"hits":[]}}',
            f"{settings.dataverse_endpoint}/api/search": b'{"data":{"items":[]}}',
            settings.openalex_endpoint: b'{"results":[]}',
        }
    )
    report = discover_data_sources(
        contract(), allow_network=True, opener=opener, settings=settings
    )
    assert set(report.provider_status) == {"zenodo", "dataverse", "openalex"}
    assert len(opener.calls) == 3
    assert all(item["status"] == "ok" for item in report.provider_status.values())
