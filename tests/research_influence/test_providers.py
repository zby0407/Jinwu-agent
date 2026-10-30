"""Provider adapters must parse real payload shapes and fail honestly."""

from __future__ import annotations

import pytest

from jw.research_influence.contracts import ProviderStatus, normalise_doi
from jw.research_influence.providers import (
    arxiv,
    crossref,
    europepmc,
    openalex,
    optional,
    unpaywall,
)
from jw.research_influence.providers.base import ProviderPolicy

from .conftest import FakeTransport

OPENALEX_WORK = {
    "id": "https://openalex.org/W1967959247",
    "doi": "https://doi.org/10.1093/mnras/stv1947",
    "title": "A light curve of SN 2005cf",
    "publication_year": 2015,
    "publication_date": "2015-09-01",
    "type": "article",
    "language": "en",
    "authorships": [
        {
            "author": {
                "display_name": "Jane Q. Astro",
                "orcid": "https://orcid.org/0000-0002-1825-0097",
            },
            "institutions": [
                {
                    "display_name": "Institute of Astronomy",
                    "country_code": "GB",
                    "type": "education",
                }
            ],
            "author_position": "first",
            "is_corresponding": True,
            "countries": ["GB"],
            "raw_affiliation_strings": ["Madingley Road, Cambridge"],
        },
        {"author": {"display_name": "Second Author"}, "institutions": []},
    ],
    "cited_by_count": 1220,
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    "counts_by_year": [{"year": 2020, "cited_by_count": 40}],
    "topics": [
        {
            "id": "T1",
            "display_name": "Supernovae",
            "score": 0.91,
            "field": {"display_name": "Physics"},
        }
    ],
    "open_access": {"is_oa": True, "oa_status": "green"},
    "primary_location": {
        "source": {
            "display_name": "Monthly Notices of the Royal Astronomical Society",
            "host_organization_name": "Oxford University Press",
            "issn": ["0035-8711"],
            "type": "journal",
        }
    },
    "best_oa_location": {
        "pdf_url": "https://arxiv.org/pdf/1509.00001",
        "is_oa": True,
        "license": "cc-by",
        "version": "acceptedVersion",
        "host_type": "repository",
        "source": {"display_name": "arXiv"},
    },
    "locations": [
        {
            "landing_page_url": "https://academic.oup.com/mnras/article/1",
            "is_oa": True,
            "version": "publishedVersion",
            "host_type": "publisher",
            "license": "",
        }
    ],
    "abstract_inverted_index": {"We": [0], "present": [1], "photometry": [2]},
}


def test_openalex_parses_the_fields_the_model_needs():
    """Citation count, topics and locations all survive parsing."""
    parsed = openalex.parse_work(OPENALEX_WORK)
    assert parsed["doi"] == "10.1093/mnras/stv1947"
    assert parsed["year"] == 2015
    assert parsed["cited_by_count"] == 1220
    assert parsed["abstract"] == "We present photometry"
    assert parsed["authors"][0]["orcid"] == "0000-0002-1825-0097"
    assert parsed["authors"][0]["institution"] == "Institute of Astronomy"
    assert parsed["authors"][0]["country"] == "GB"
    assert parsed["venue"]["name"].startswith("Monthly Notices")
    assert parsed["topics"][0]["field"] == "Physics"
    assert len(parsed["referenced_works"]) == 2
    assert len(parsed["locations"]) == 2


def test_openalex_adds_the_polite_pool_mailto_and_select_list():
    """The request carries the select list so payloads stay small."""
    transport = FakeTransport().add("/works/doi:", OPENALEX_WORK)
    policy = ProviderPolicy(email="ops@example.org")
    result = openalex.fetch_work(
        "10.1093/mnras/stv1947", transport=transport, policy=policy
    )
    assert result.ok
    url = transport.calls[0][1]
    assert "select=" in url
    assert "cited_by_count" in url
    assert "mailto=ops%40example.org" in url


def test_openalex_without_doi_is_skipped_not_queried():
    """No DOI means no request at all."""
    transport = FakeTransport()
    result = openalex.fetch_work("", transport=transport, policy=ProviderPolicy())
    assert result.status is ProviderStatus.SKIPPED
    assert transport.calls == []


def test_forbidden_and_rate_limited_are_typed():
    """HTTP failures become provider statuses, never exceptions."""
    forbidden = openalex.fetch_work(
        "10.1/x",
        transport=FakeTransport().add("/works/doi:", None, status=403),
        policy=ProviderPolicy(),
    )
    limited = openalex.fetch_work(
        "10.1/x",
        transport=FakeTransport().add("/works/doi:", None, status=429),
        policy=ProviderPolicy(),
    )
    assert forbidden.status is ProviderStatus.FORBIDDEN
    assert limited.status is ProviderStatus.RATE_LIMITED


def test_crossref_strips_jats_markup_and_keeps_deposited_references():
    """Crossref abstracts arrive as JATS XML; references arrive as deposits."""
    payload = {
        "message": {
            "DOI": "10.1093/MNRAS/STV1947",
            "title": ["A title"],
            "abstract": "<jats:p>We <jats:italic>present</jats:italic> photometry.</jats:p>",
            "issued": {"date-parts": [[2015, 9, 1]]},
            "author": [
                {
                    "given": "Jane",
                    "family": "Astro",
                    "affiliation": [{"name": "IoA"}],
                    "sequence": "first",
                }
            ],
            "container-title": ["MNRAS"],
            "publisher": "OUP",
            "ISSN": ["0035-8711"],
            "is-referenced-by-count": 12,
            "reference-count": 2,
            "reference": [
                {
                    "index": 1,
                    "DOI": "10.1/a",
                    "unstructured": "Ref one",
                    "year": "2001",
                },
                {
                    "index": 2,
                    "article-title": "Ref two",
                    "journal-title": "ApJ",
                    "year": "2003",
                },
            ],
        }
    }
    parsed = crossref.parse_work(payload)
    assert parsed["abstract"] == "We present photometry."
    assert parsed["doi"] == "10.1093/mnras/stv1947"
    assert parsed["year"] == 2015
    assert parsed["authors"][0]["institution"] == "IoA"
    assert parsed["cited_by_count"] == 12
    assert len(parsed["references"]) == 2
    assert parsed["references"][0]["doi"] == "10.1/a"


def test_unpaywall_refuses_to_guess_an_email():
    """Without a contact email the provider reports NOT_CONFIGURED."""
    result = unpaywall.fetch_work(
        "10.1/x", transport=FakeTransport(), policy=ProviderPolicy(email="")
    )
    assert result.status is ProviderStatus.NOT_CONFIGURED
    assert "UNPAYWALL_EMAIL" in result.note


def test_unpaywall_parses_locations_when_configured(monkeypatch):
    """With an email the locations are parsed and deduplicated."""
    monkeypatch.setenv("UNPAYWALL_EMAIL", "ops@example.org")
    payload = {
        "doi": "10.1/x",
        "is_oa": True,
        "oa_status": "green",
        "best_oa_location": {
            "url_for_pdf": "https://arxiv.org/pdf/1",
            "host_type": "repository",
            "version": "acceptedVersion",
            "license": "cc-by",
        },
        "oa_locations": [
            {"url_for_pdf": "https://arxiv.org/pdf/1", "host_type": "repository"},
            {
                "url_for_landing_page": "https://example.org/landing",
                "host_type": "publisher",
            },
        ],
    }
    transport = FakeTransport().add("api.unpaywall.org", payload)
    result = unpaywall.fetch_work(
        "10.1/x", transport=transport, policy=ProviderPolicy.from_environment()
    )
    assert result.ok
    parsed = unpaywall.parse_work(result.payload)
    assert len(parsed["locations"]) == 2


def test_arxiv_parses_atom_and_prefers_the_pdf_link():
    """The Atom feed is parsed with stdlib XML, version suffix stripped."""
    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1509.00001v2</id>
    <title>A  light
      curve</title>
    <summary> We measure  things. </summary>
    <published>2015-09-01T00:00:00Z</published>
    <author><name>Jane Astro</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/1509.00001v2"/>
    <arxiv:journal_ref>MNRAS 455, 1</arxiv:journal_ref>
  </entry>
</feed>
"""
    transport = FakeTransport().add(
        "export.arxiv.org", atom, content_type="application/atom+xml"
    )
    result = arxiv.lookup_ids(
        ("arXiv:1509.00001v2",), transport=transport, policy=ProviderPolicy()
    )
    assert result.ok
    entries = result.payload["entries"]
    assert entries[0]["arxiv_id"] == "1509.00001"
    assert entries[0]["title"] == "A light curve"
    assert entries[0]["pdf_url"] == "https://arxiv.org/pdf/1509.00001v2"
    assert entries[0]["journal_reference"] == "MNRAS 455, 1"


def test_arxiv_empty_feed_is_not_found_not_ok():
    """An empty feed is a typed absence."""
    atom = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    result = arxiv.search_by_title(
        "nothing",
        transport=FakeTransport().add("export.arxiv.org", atom),
        policy=ProviderPolicy(),
    )
    assert result.status is ProviderStatus.NOT_FOUND


def test_europepmc_reports_fulltext_links():
    """Europe PMC full-text links become location candidates."""
    payload = {
        "resultList": {
            "result": [
                {
                    "pmcid": "PMC123",
                    "pmid": "999",
                    "isOpenAccess": "Y",
                    "pubYear": "2015",
                    "title": "A paper",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {
                                "availability": "Open access",
                                "documentStyle": "pdf",
                                "site": "Europe_PMC",
                                "url": "https://europepmc.org/x.pdf",
                            },
                            {
                                "availability": "Open access",
                                "documentStyle": "html",
                                "site": "Europe_PMC",
                                "url": "https://europepmc.org/x",
                            },
                        ]
                    },
                }
            ]
        }
    }
    transport = FakeTransport().add("europepmc", payload)
    result = europepmc.fetch_work(
        "10.1/x", transport=transport, policy=ProviderPolicy()
    )
    assert result.ok
    parsed = europepmc.parse_work(result.payload)
    assert parsed["pmcid"] == "PMC123"
    assert len(parsed["locations"]) == 2
    assert parsed["locations"][0]["pdf_url"].endswith(".pdf")


def test_optional_providers_name_the_missing_variable(monkeypatch):
    """A missing credential is reported, never silently treated as 'nothing found'."""
    for name in ("CORE_API_KEY", "ADS_API_TOKEN", "ELSEVIER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    policy = ProviderPolicy()
    transport = FakeTransport()
    core = optional.core_lookup("10.1/x", transport=transport, policy=policy)
    ads = optional.ads_lookup("2015MNRAS.455..1A", transport=transport, policy=policy)
    tdm = optional.tdm_lookup("elsevier", "10.1/x", transport=transport, policy=policy)
    assert core.status is ProviderStatus.NOT_CONFIGURED
    assert "CORE_API_KEY" in core.note
    assert ads.status is ProviderStatus.NOT_CONFIGURED
    assert "ADS_API_TOKEN" in ads.note
    assert tdm.status is ProviderStatus.NOT_CONFIGURED
    assert transport.calls == [], "a missing credential must not cause a request"


def test_semantic_scholar_notes_the_unauthenticated_rate_limit():
    """Keyless access works but is flagged."""
    payload = {
        "title": "T",
        "citationCount": 5,
        "externalIds": {"DOI": "10.1/X"},
        "openAccessPdf": {"url": "https://x/pdf"},
    }
    transport = FakeTransport().add("api.semanticscholar.org", payload)
    result = optional.semantic_scholar_lookup(
        "10.1/x", transport=transport, policy=ProviderPolicy()
    )
    assert result.ok
    assert "unauthenticated_rate_limited" in result.note
    parsed = optional.parse_semantic_scholar(result.payload)
    assert parsed["cited_by_count"] == 5
    assert parsed["doi"] == "10.1/x"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1093/MNRAS/STV1947", "10.1093/mnras/stv1947"),
        ("doi:10.1/ABC", "10.1/abc"),
        ("  10.1/x  ", "10.1/x"),
        ("", ""),
    ],
)
def test_doi_normalisation(raw, expected):
    """DOI normalisation is canonical and idempotent."""
    assert normalise_doi(raw) == expected
    assert normalise_doi(normalise_doi(raw)) == expected
