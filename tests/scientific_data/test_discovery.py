"""Discovery must find candidates without ever inventing a reference."""

from __future__ import annotations

import pytest

from jw.scientific_data.contracts import RetrievalState, SourceRole
from jw.scientific_data.discovery import (
    discover_sources,
    discovery_query,
    literature_searcher,
)
from jw.scientific_data.workflow import contract_from_request


def _contract(**overrides):
    payload = {
        "research_goal": "Assemble V band light curves of nearby Type Ia supernovae",
        "domain_plugin": "snia_light_curve",
        "observables": ["apparent_magnitude", "flux_density"],
    }
    payload.update(overrides)
    return contract_from_request(payload)


def _hit(**overrides):
    hit = {
        "source_id": "ads:2005ApJ",
        "title": "Optical photometry of SN 2005cf",
        "year": 2005,
        "doi": "10.1086/123456",
        "url": "https://ui.adsabs.harvard.edu/link/2005ApJ",
        "is_refereed": True,
        "is_retracted": False,
    }
    hit.update(overrides)
    return hit


def _payload(*hits, status="ok"):
    return {
        "status": status,
        "source": "all",
        "count": len(hits),
        "raw_count": len(hits),
        "providers_queried": ["ads", "openalex"],
        "results": list(hits),
    }


def test_query_combines_goal_and_observables():
    query = discovery_query(_contract())
    assert "Type Ia supernovae" in query
    assert "apparent magnitude" in query


def test_hits_become_catalog_candidates():
    report = discover_sources(_contract(), searcher=lambda q, n: _payload(_hit()))
    assert report.status == "ok"
    (record,) = report.found
    assert record.role is SourceRole.PRIMARY_PUBLISHED
    assert record.retrieval is RetrievalState.CANDIDATE
    assert record.trust == pytest.approx(0.9)
    assert record.identifier == "10.1086/123456"
    assert record.discovered_via == "literature_search:all"
    assert "refereed" in record.notes


def test_preprints_are_trusted_less_than_refereed_papers():
    report = discover_sources(
        _contract(),
        searcher=lambda q, n: _payload(
            _hit(),
            _hit(
                source_id="arxiv:2005.00001",
                doi="",
                url="https://arxiv.org/abs/2005.00001",
                is_refereed=False,
                source_version="preprint",
            ),
        ),
    )
    trust = {record.url: record.trust for record in report.found}
    assert trust["https://ui.adsabs.harvard.edu/link/2005ApJ"] == pytest.approx(0.9)
    assert trust["https://arxiv.org/abs/2005.00001"] < 0.9


def test_format_hint_follows_the_url():
    report = discover_sources(
        _contract(),
        searcher=lambda q, n: _payload(
            _hit(url="https://example.org/table1.csv"),
            _hit(url="https://example.org/figure1.png", doi=""),
        ),
    )
    hints = {record.format_hint for record in report.found}
    assert hints == {"csv", "png"}


def test_doi_is_used_when_a_hit_has_no_url():
    report = discover_sources(
        _contract(),
        searcher=lambda q, n: _payload(_hit(url="", doi="10.1000/xyz")),
    )
    assert report.found[0].url == "https://doi.org/10.1000/xyz"


def test_retracted_papers_are_skipped_not_registered():
    report = discover_sources(
        _contract(), searcher=lambda q, n: _payload(_hit(is_retracted=True))
    )
    assert report.found == ()
    assert report.skipped == ("ads:2005ApJ",)
    assert report.status == "skipped_all"
    assert report.issues


def test_network_is_refused_by_default():
    called: list[str] = []

    def searcher(query, limit):  # pragma: no cover - must not be reached
        called.append(query)
        return _payload()

    report = discover_sources(_contract(), searcher=None)
    assert called == []
    assert report.status == "policy_blocked"
    assert report.found == ()
    assert report.issues and report.issues[0].code.value == "source_host_denied"
    assert "network access" in report.issues[0].detail


def test_provider_failure_is_reported_as_an_outage():
    def explode(query, limit):
        raise RuntimeError("ADS is down")

    report = discover_sources(_contract(), searcher=explode)
    assert report.status == "unavailable"
    assert report.found == ()
    assert "ADS is down" in report.issues[0].detail
    assert "invented" in report.note


def test_empty_result_is_not_a_silent_success():
    report = discover_sources(_contract(), searcher=lambda q, n: _payload())
    assert report.status == "no_results"
    assert report.issues


def test_partial_provider_status_is_carried_through():
    report = discover_sources(
        _contract(),
        searcher=lambda q, n: _payload(_hit(), status="partial"),
    )
    assert report.status == "partial"
    assert report.provider_status["providers_queried"] == ["ads", "openalex"]


def test_user_supplied_urls_survive_a_blocked_search():
    report = discover_sources(
        _contract(), extra_urls=["https://example.org/my_table.csv"]
    )
    assert report.status == "policy_blocked"
    (record,) = report.found
    assert record.discovered_via == "user_input"
    assert record.trust == pytest.approx(0.8)


def test_report_is_serialisable():
    report = discover_sources(_contract(), searcher=lambda q, n: _payload(_hit()))
    payload = report.to_dict()
    assert payload["status"] == "ok"
    assert payload["found"][0]["url"]
    assert payload["steps"]


def test_default_searcher_is_lazy():
    # Importing the module must not require the knowledge-base extra.
    assert callable(literature_searcher)
