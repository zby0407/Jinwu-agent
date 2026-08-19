"""文献管线测试（方案 §5.3）：网络打补丁喂固定 JSON/XML，db 用临时目录隔离。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_base import literature, service  # noqa: E402
from knowledge_base.contracts import ContractError  # noqa: E402
from knowledge_base.store import KnowledgeStore  # noqa: E402

ABSTRACT = (
    "The polar field strength at solar minimum is a robust precursor of the "
    "next solar cycle amplitude, and weak polar fields precede weak cycles."
)
ARXIV_ABSTRACT = "We show that the polar field precursor predicts cycle amplitude."


def inverted_index(text: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, word in enumerate(text.split()):
        index.setdefault(word, []).append(position)
    return index


OPENALEX_PAYLOAD = json.dumps(
    {
        "results": [
            {
                "id": "https://openalex.org/W1234567",
                "doi": "10.1029/2019SW002000",
                "title": "Polar field precursor of solar cycle 25",
                "publication_year": 2020,
                "type": "article",
                "authorships": [
                    {"author": {"display_name": "Alice Zhang"}},
                    {"author": {"display_name": "Bob Li"}},
                ],
                "primary_location": {"landing_page_url": "https://example.org/paper"},
                "abstract_inverted_index": inverted_index(ABSTRACT),
                "is_retracted": False,
            }
        ]
    }
).encode("utf-8")

ARXIV_PAYLOAD = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>  Polar precursor methods for cycle prediction </title>
    <summary>  {ARXIV_ABSTRACT}  </summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Carol Wang</name></author>
    <arxiv:doi>10.48550/arXiv.2401.12345</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1001.00001v1</id>
    <title>Old polar field note</title>
    <summary>An old note on polar fields.</summary>
    <published>2010-05-01T00:00:00Z</published>
    <author><name>Dave Sun</name></author>
  </entry>
</feed>
""".encode("utf-8")

CROSSREF_PAYLOAD = json.dumps(
    {
        "message": {
            "items": [
                {
                    "DOI": "10.5555/CROSSREF.1",
                    "title": ["Crossref solar-cycle precursor review"],
                    "author": [
                        {"given": "Dana", "family": "Researcher"},
                        {"given": "Eli", "family": "Sun"},
                    ],
                    "published": {"date-parts": [[2025, 3, 1]]},
                    "URL": "https://doi.org/10.5555/CROSSREF.1",
                    "abstract": (
                        "<jats:p>The polar field is a precursor of solar "
                        "cycle amplitude.</jats:p>"
                    ),
                    "indexed": {"timestamp": 1730000000000},
                }
            ]
        }
    }
).encode("utf-8")

ADS_PAYLOAD = json.dumps(
    {
        "response": {
            "docs": [
                {
                    "bibcode": "2026ApJ...999A...1S",
                    "title": ["A Refereed Polar-field Precursor Test"],
                    "author": ["Solar, Ada", "Cycle, Bo"],
                    "year": 2026,
                    "doi": ["10.1234/ads.solar.1"],
                    "abstract": ABSTRACT,
                    "pubdate": "2026-04-00",
                    "property": ["REFEREED", "ARTICLE"],
                    "doctype": "article",
                    "indexstamp": "2026-06-01T00:00:00Z",
                }
            ]
        }
    }
).encode("utf-8")


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            return self._payload
        return self._payload[:n]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def fake_urlopen(payload: bytes):
    def _open(request, timeout=0):
        return FakeResponse(payload)

    return _open


def make_store(tmp: str) -> KnowledgeStore:
    return KnowledgeStore(
        db_path=Path(tmp) / "knowledge.db", export_dir=Path(tmp) / "knowledge_base"
    )


def cache_openalex_source(store: KnowledgeStore) -> None:
    store.upsert_lit_source(
        {
            "source_id": "openalex:W1234567",
            "title": "Polar field precursor of solar cycle 25",
            "authors": ["Alice Zhang"],
            "year": 2020,
            "doi": "10.1029/2019SW002000",
            "url": "https://example.org/paper",
            "abstract": ABSTRACT,
        }
    )


def evidence(text: str, quote: str, location: str = "abstract") -> dict[str, str]:
    return {"text": text, "quote": quote, "location": location}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_lit_test_")
        self.store = make_store(self.tmp)
        self.addCleanup(self.store.close)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class TestLitSearchOpenAlex(StoreTestCase):
    def test_search_caches_results_and_second_hit_is_cached(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen(OPENALEX_PAYLOAD)):
            first = literature.search_literature(
                self.store, "polar field precursor", source="openalex"
            )
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["count"], 1)
            row = first["results"][0]
            self.assertEqual(row["source_id"], "openalex:W1234567")
            self.assertEqual(row["cached"], False)
            self.assertEqual(row["abstract_chars"], len(ABSTRACT))
            cached_row = self.store.get_lit_source("openalex:W1234567")
            self.assertIsNotNone(cached_row)
            self.assertEqual(cached_row["abstract"], ABSTRACT)
            second = literature.search_literature(
                self.store, "polar field precursor", source="openalex"
            )
            self.assertEqual(second["results"][0]["cached"], True)

    def test_search_year_filter_reaches_query(self):
        seen_urls = []

        def _open(request, timeout=0):
            seen_urls.append(request.full_url)
            return FakeResponse(OPENALEX_PAYLOAD)

        with mock.patch("urllib.request.urlopen", _open):
            literature.search_literature(
                self.store,
                "polar",
                source="openalex",
                from_year=2015,
                to_year=2021,
            )
        self.assertIn("from_publication_date%3A2015-01-01", seen_urls[0])
        self.assertIn("to_publication_date%3A2021-12-31", seen_urls[0])

    def test_search_unavailable_on_network_error(self):
        def _open(request, timeout=0):
            raise urllib.error.URLError("no network")

        with mock.patch("urllib.request.urlopen", _open):
            result = literature.search_literature(
                self.store, "polar", source="openalex"
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["results"], [])
        self.assertIn("diagnostic", result)

    def test_search_rejects_unknown_source(self):
        with self.assertRaises(ContractError) as ctx:
            literature.search_literature(self.store, "polar", source="google")
        self.assertEqual(ctx.exception.error_code, "unknown_lit_source")


class TestLitSearchAds(StoreTestCase):
    def test_ads_requires_token_without_blocking_other_sources(self):
        with mock.patch.dict("os.environ", {"ADS_API_TOKEN": ""}, clear=False):
            result = literature.search_literature(
                self.store, "solar cycle precursor", source="ads"
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["results"], [])
        self.assertIn("ADS_API_TOKEN", str(result["diagnostic"]))

    def test_ads_sends_bearer_token_and_parses_trust_metadata(self):
        requests = []

        def _open(request, timeout=0):
            requests.append(request)
            return FakeResponse(ADS_PAYLOAD)

        with (
            mock.patch.dict(
                "os.environ", {"ADS_API_TOKEN": "test-ads-token"}, clear=False
            ),
            mock.patch("urllib.request.urlopen", _open),
        ):
            result = literature.search_literature(
                self.store,
                "polar field precursor",
                source="ads",
                from_year=2024,
                to_year=2026,
                sort="recent",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            requests[0].get_header("Authorization"), "Bearer test-ads-token"
        )
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0].full_url).query)
        self.assertEqual(query["sort"], ["date desc"])
        self.assertIn("year:[2024 TO 2026]", query["q"][0])
        row = result["results"][0]
        self.assertEqual(row["source_id"], "ads:2026ApJ...999A...1S")
        self.assertTrue(row["is_refereed"])
        self.assertFalse(row["is_retracted"])
        self.assertEqual(row["publication_date"], "2026-04-00")
        cached = self.store.get_lit_source(row["source_id"])
        self.assertEqual(cached["is_refereed"], 1)

    def test_provider_diagnostic_redacts_token(self):
        def _open(request, timeout=0):
            raise urllib.error.URLError("leaked-secret-token")

        with (
            mock.patch.dict(
                "os.environ", {"ADS_API_TOKEN": "leaked-secret-token"}, clear=False
            ),
            mock.patch("urllib.request.urlopen", _open),
        ):
            result = literature.search_literature(
                self.store, "polar field", source="ads"
            )
        diagnostic = json.dumps(result["diagnostic"])
        self.assertNotIn("leaked-secret-token", diagnostic)
        self.assertIn("[redacted]", diagnostic)


class TestLitSearchArxiv(StoreTestCase):
    def test_arxiv_search_parses_atom_and_strips_version(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen(ARXIV_PAYLOAD)):
            result = literature.search_literature(
                self.store, "polar precursor", source="arxiv"
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        first = result["results"][0]
        self.assertEqual(first["source_id"], "arxiv:2401.12345")
        self.assertEqual(first["year"], 2024)
        self.assertEqual(first["authors"], ["Carol Wang"])
        self.assertEqual(first["doi"], "10.48550/arXiv.2401.12345")

    def test_arxiv_year_filter_is_applied(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen(ARXIV_PAYLOAD)):
            result = literature.search_literature(
                self.store, "polar precursor", source="arxiv", from_year=2015
            )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["source_id"], "arxiv:2401.12345")

    def test_cached_arxiv_source_refreshes_to_new_version(self):
        original = self.store.upsert_lit_source(
            {
                "source_id": "arxiv:2401.12345",
                "provider": "arxiv",
                "source_version": "1",
                "title": "Polar precursor methods for cycle prediction",
                "authors": ["Carol Wang"],
                "year": 2024,
                "doi": "10.48550/arXiv.2401.12345",
                "url": "https://arxiv.org/abs/2401.12345v1",
                "abstract": "version one",
            }
        )
        self.store.upsert_lit_source(
            {
                "source_id": "arxiv:2401.12345",
                "provider": "arxiv",
                "source_version": "3",
                "title": "Revised polar precursor methods for cycle prediction",
                "authors": ["Carol Wang"],
                "year": 2024,
                "doi": "10.48550/arXiv.2401.12345",
                "url": "https://arxiv.org/abs/2401.12345v3",
                "abstract": "version three corrected abstract",
            }
        )
        row = self.store.get_lit_source("arxiv:2401.12345")
        self.assertEqual(row["source_version"], "3")
        self.assertEqual(row["abstract"], "version three corrected abstract")
        self.assertEqual(row["family_id"], original["family_id"])


class TestLitSearchCrossref(StoreTestCase):
    def test_crossref_search_parses_metadata_and_plain_abstract(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen(CROSSREF_PAYLOAD)):
            result = literature.search_literature(
                self.store, "solar cycle precursor", source="crossref"
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        row = result["results"][0]
        self.assertEqual(row["source_id"], "crossref:10.5555/crossref.1")
        self.assertEqual(row["year"], 2025)
        self.assertEqual(row["authors"], ["Dana Researcher", "Eli Sun"])
        cached = self.store.get_lit_source(row["source_id"])
        self.assertEqual(
            cached["abstract"],
            "The polar field is a precursor of solar cycle amplitude.",
        )

    def test_crossref_title_marks_retracted_work(self):
        payload = json.dumps(
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.5555/retracted",
                            "title": ["RETRACTED: Solar Cycle Forecast"],
                            "author": [{"family": "Example"}],
                            "published": {"date-parts": [[2025]]},
                            "URL": "https://doi.org/10.5555/retracted",
                        }
                    ]
                }
            }
        ).encode()
        with mock.patch("urllib.request.urlopen", fake_urlopen(payload)):
            result = literature.search_literature(
                self.store, "solar cycle forecast", source="crossref"
            )

        self.assertTrue(result["results"][0]["is_retracted"])
        self.assertEqual(
            self.store.get_lit_source("crossref:10.5555/retracted")["is_retracted"],
            1,
        )

    def test_all_search_interleaves_providers_and_tolerates_partial_failure(self):
        def _open(request, timeout=0):
            if "openalex.org" in request.full_url:
                return FakeResponse(OPENALEX_PAYLOAD)
            if "arxiv.org" in request.full_url:
                raise urllib.error.URLError("arxiv temporarily unavailable")
            if "crossref.org" in request.full_url:
                return FakeResponse(CROSSREF_PAYLOAD)
            raise AssertionError(request.full_url)

        with mock.patch("urllib.request.urlopen", _open):
            result = literature.search_literature(
                self.store, "solar cycle precursor", source="all", limit=3
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            {row["source_id"].split(":", 1)[0] for row in result["results"]},
            {"openalex", "crossref"},
        )
        self.assertIn("arxiv", result["provider_diagnostics"])


class TestLiteratureFeeds(StoreTestCase):
    def _catalog_path(self) -> Path:
        path = Path(self.tmp) / "literature_feeds.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "literature-feed-catalog-v1",
                    "feeds": [
                        {
                            "id": "polar_updates",
                            "title_zh": "极区磁场更新",
                            "query": "polar field solar cycle precursor",
                            "providers": ["ads", "crossref"],
                            "required_any_terms": ["precursor"],
                            "lookback_years": 3,
                            "sort": "recent",
                            "limit": 2,
                            "enabled": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_feed_sync_is_partial_audited_and_idempotent(self):
        path = self._catalog_path()
        stale = self.store.upsert_lit_source(
            {
                "source_id": "crossref:10.5555/stale-feed-hit",
                "provider": "crossref",
                "title": "Polar Field Response in the Terrestrial Atmosphere",
                "authors": ["Example Author"],
                "year": 2025,
                "doi": "10.5555/stale-feed-hit",
                "url": "https://doi.org/10.5555/stale-feed-hit",
                "abstract": "A polar field response driven by solar forcing.",
            }
        )
        self.store.touch_lit_feed_family("polar_updates", stale["family_id"])

        def _open(request, timeout=0):
            if "crossref.org" in request.full_url:
                return FakeResponse(CROSSREF_PAYLOAD)
            raise AssertionError(request.full_url)

        with (
            mock.patch.dict("os.environ", {"ADS_API_TOKEN": ""}, clear=False),
            mock.patch("urllib.request.urlopen", _open),
        ):
            first = literature.sync_literature_feed(
                self.store, "polar_updates", feeds_path=path
            )
            second = literature.sync_literature_feed(
                self.store, "polar_updates", feeds_path=path
            )
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["new_source_count"], 1)
        self.assertEqual(first["new_family_count"], 1)
        self.assertEqual(first["pruned_family_count"], 1)
        self.assertIn("ads", first["provider_diagnostics"])
        self.assertEqual(second["new_source_count"], 0)
        self.assertEqual(second["new_family_count"], 0)
        latest = self.store.latest_lit_feed_run("polar_updates")
        self.assertEqual(latest["id"], second["receipt"]["id"])
        self.assertEqual(latest["providers"], ["ads", "crossref"])
        self.assertIn("ads", latest["diagnostics"])
        self.assertIsInstance(latest["diagnostics"]["ads"], str)
        self.assertEqual(
            [
                row["source_id"]
                for row in self.store.list_lit_feed_sources("polar_updates")
            ],
            ["crossref:10.5555/crossref.1"],
        )

    def test_feed_catalog_preserves_operational_batch_size(self):
        path = self._catalog_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["feeds"][0]["limit"] = 25
        path.write_text(json.dumps(payload), encoding="utf-8")

        catalog = literature.load_literature_feeds(path)

        self.assertEqual(catalog["feeds"][0]["limit"], 25)

    def test_feed_relevance_gate_does_not_cache_metadata_false_positive(self):
        irrelevant_payload = json.dumps(
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.5555/irrelevant",
                            "title": [
                                "Hemispheric Asymmetry Across the Menstrual Cycle"
                            ],
                            "author": [{"family": "Editor"}],
                            "published": {"date-parts": [[2026]]},
                            "URL": "https://doi.org/10.5555/irrelevant",
                        }
                    ]
                }
            }
        ).encode()
        with mock.patch("urllib.request.urlopen", fake_urlopen(irrelevant_payload)):
            result = literature.search_literature(
                self.store,
                "solar hemispheric asymmetry coupling activity cycle",
                source="crossref",
                sort="recent",
                minimum_query_term_matches=2,
                required_terms=["solar"],
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 0)
        self.assertEqual(
            result["relevance_gate"]["filtered_out_by_provider"]["crossref"], 1
        )
        self.assertIsNone(self.store.get_lit_source("crossref:10.5555/irrelevant"))

    def test_feed_required_any_gate_rejects_adjacent_solar_domain(self):
        adjacent_payload = json.dumps(
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.5555/adjacent",
                            "title": [
                                "Hemispheric Atmospheric Response to Solar Forcing"
                            ],
                            "author": [{"family": "Editor"}],
                            "published": {"date-parts": [[2026]]},
                            "URL": "https://doi.org/10.5555/adjacent",
                        }
                    ]
                }
            }
        ).encode()
        with mock.patch("urllib.request.urlopen", fake_urlopen(adjacent_payload)):
            result = literature.search_literature(
                self.store,
                "solar hemispheric asymmetry coupling activity cycle",
                source="crossref",
                sort="recent",
                minimum_query_term_matches=2,
                required_terms=["solar"],
                required_any_terms=[
                    "solar hemispheric",
                    "sunspot",
                    "solar dynamo",
                    "solar activity",
                    "active region",
                    "coronal",
                ],
                required_any_title_terms=[
                    "solar hemispheric",
                    "hemispheric asymmetry of sunspot",
                    "hemispheric asymmetry of the solar",
                    "solar active region",
                    "coronal bright",
                    "north-south asymmetry",
                ],
            )
        self.assertEqual(result["count"], 0)
        self.assertIsNone(self.store.get_lit_source("crossref:10.5555/adjacent"))


class TestLiteratureFamilies(StoreTestCase):
    def test_preferred_source_uses_refereed_and_retraction_risk(self):
        preprint = self.store.upsert_lit_source(
            {
                "source_id": "arxiv:2601.00001",
                "provider": "arxiv",
                "title": "Polar precursor comparison",
                "authors": ["A. Solar"],
                "year": 2026,
                "doi": "10.1234/preferred.1",
                "url": "https://arxiv.org/abs/2601.00001",
                "abstract": "preprint",
            }
        )
        journal = self.store.upsert_lit_source(
            {
                "source_id": "ads:2025ApJ...1A",
                "provider": "ads",
                "title": "Polar precursor comparison",
                "authors": ["A. Solar"],
                "year": 2025,
                "doi": "10.1234/preferred.1",
                "url": "https://ui.adsabs.harvard.edu/abs/2025ApJ...1A",
                "abstract": "refereed article",
                "is_refereed": True,
            }
        )
        self.assertEqual(preprint["family_id"], journal["family_id"])
        self.assertEqual(
            self.store.resolve_lit_source(preprint["source_id"])["source_id"],
            journal["source_id"],
        )

        self.store.upsert_lit_source(
            {
                **journal,
                "abstract": "retracted article",
                "is_refereed": True,
                "is_retracted": True,
            }
        )
        self.assertEqual(
            self.store.resolve_lit_source(journal["source_id"])["source_id"],
            preprint["source_id"],
        )

    def test_same_author_title_updates_share_family_and_prefer_latest(self):
        old = self.store.upsert_lit_source(
            {
                "source_id": "openalex:W2005",
                "title": "Dynamo Models of the Solar Cycle",
                "authors": ["Paul Charbonneau"],
                "year": 2005,
                "doi": "10.1000/edition-2005",
                "url": "https://example.org/2005",
                "abstract": "older review",
            }
        )
        new = self.store.upsert_lit_source(
            {
                "source_id": "openalex:W2020",
                "title": "Dynamo models of the solar cycle",
                "authors": ["Paul Charbonneau"],
                "year": 2020,
                "doi": "10.1000/edition-2020",
                "url": "https://example.org/2020",
                "abstract": "updated review",
            }
        )
        old = self.store.get_lit_source(old["source_id"])
        new = self.store.get_lit_source(new["source_id"])
        self.assertEqual(old["family_id"], new["family_id"])
        self.assertEqual(old["canonical_source_id"], "openalex:W2020")
        self.assertEqual(new["is_preferred"], 1)
        self.assertEqual(
            self.store.resolve_lit_source("openalex:W2005")["source_id"],
            "openalex:W2020",
        )

    def test_same_doi_across_providers_shares_family(self):
        openalex = self.store.upsert_lit_source(
            {
                "source_id": "openalex:Wdoi",
                "title": "Journal title",
                "authors": ["A. Author"],
                "year": 2024,
                "doi": "https://doi.org/10.1234/ABC.1",
                "url": "https://example.org/journal",
                "abstract": "journal abstract",
            }
        )
        arxiv = self.store.upsert_lit_source(
            {
                "source_id": "arxiv:2401.00001",
                "provider": "arxiv",
                "source_version": "2",
                "title": "Preprint title changed",
                "authors": ["A. Author"],
                "year": 2023,
                "doi": "10.1234/abc.1",
                "url": "https://arxiv.org/abs/2401.00001v2",
                "abstract": "preprint abstract",
            }
        )
        self.assertEqual(openalex["family_id"], arxiv["family_id"])


class TestLitFetch(StoreTestCase):
    def test_fetch_unknown_source_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            literature.fetch_literature(self.store, "openalex:W000")
        self.assertEqual(ctx.exception.error_code, "lit_source_not_found")

    def test_fetch_writes_text_and_is_idempotent(self):
        cache_openalex_source(self.store)
        target_dir = Path(self.tmp) / "literature"
        first = literature.fetch_literature(
            self.store, "openalex:W1234567", literature_dir=target_dir
        )
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["cached"], False)
        path = Path(first["path"])
        self.assertTrue(path.is_file())
        archive_path = Path(first["archive_path"])
        self.assertTrue(archive_path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn(ABSTRACT, text)
        self.assertEqual(archive_path.read_text(encoding="utf-8"), text)
        self.assertEqual(first["text_length"], len(text))
        second = literature.fetch_literature(
            self.store, "openalex:W1234567", literature_dir=target_dir
        )
        self.assertEqual(second["cached"], True)
        self.assertEqual(second["text_length"], first["text_length"])


class TestLitDistill(StoreTestCase):
    def setUp(self):
        super().setUp()
        cache_openalex_source(self.store)

    def distill(self, content, **overrides):
        params = {
            "entry_type": "concept",
            "title": "Polar field precursor",
            "content": content,
            "research_question": (
                "Does the polar field precursor predict the next solar cycle amplitude?"
            ),
            "focus": "polar field precursor for next solar cycle amplitude",
        }
        params.update(overrides)
        return literature.distill_literature(self.store, "openalex:W1234567", **params)

    def test_distill_happy_path_proposes_candidate(self):
        result = self.distill(
            {
                "definition": evidence(
                    "极区磁场（polar field）强度是下一活动周振幅的可靠前兆",
                    "polar field strength at solar minimum is a robust precursor",
                ),
                "physical_notes": "evidence_gap",
            },
            focus="polar field precursor for next solar cycle amplitude",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["idempotent"], False)
        self.assertEqual(result["quotes_verified"], 1)
        self.assertEqual(
            result["evidence_gaps"], [{"field": "physical_notes", "note": ""}]
        )
        entry = result["entry"]
        self.assertEqual(entry["status"], "candidate")
        self.assertEqual(entry["type"], "concept")
        self.assertEqual(entry["source_type"], "literature")
        self.assertEqual(entry["source_ref"], "10.1029/2019SW002000")
        self.assertNotIn("physical_notes", entry["content"])
        provenance = entry["provenance"]
        self.assertEqual(provenance["lit_source_id"], "openalex:W1234567")
        self.assertEqual(
            provenance["distill_focus"],
            "polar field precursor for next solar cycle amplitude",
        )
        self.assertIn("definition", provenance["evidence_map"])
        row = self.store.get_lit_source("openalex:W1234567")
        self.assertEqual(row["distilled_entry_id"], entry["id"])

    def test_quote_match_is_case_and_whitespace_normalized(self):
        result = self.distill(
            {
                "definition": evidence(
                    "polar field precursor 归一化匹配",
                    "POLAR FIELD STRENGTH\n  AT SOLAR MINIMUM   is a robust precursor",
                )
            }
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["quotes_verified"], 1)

    def test_fabricated_quote_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            self.distill(
                {
                    "definition": evidence(
                        "编造内容",
                        "completely fabricated sentence never present in the source",
                    )
                }
            )
        self.assertEqual(ctx.exception.error_code, "quote_not_grounded")

    def test_quote_over_40_words_rejected(self):
        long_quote = " ".join(["word"] * 41)
        with self.assertRaises(ContractError) as ctx:
            self.distill({"definition": evidence("超长引用", long_quote)})
        self.assertEqual(ctx.exception.error_code, "quote_too_long")

    def test_required_field_cannot_be_evidence_gap(self):
        with self.assertRaises(ContractError) as ctx:
            self.distill({"definition": "evidence_gap"})
        self.assertEqual(ctx.exception.error_code, "required_field_ungrounded")

    def test_repeat_distill_returns_existing_entry(self):
        content = {
            "definition": evidence(
                "polar field precursor 首次蒸馏",
                "polar field strength at solar minimum is a robust precursor",
            )
        }
        first = self.distill(content)
        second = self.distill(content, title="另一个标题")
        self.assertEqual(second["idempotent"], True)
        self.assertEqual(second["entry_id"], first["entry_id"])
        still = self.store.get_lit_source("openalex:W1234567")
        self.assertEqual(still["distilled_entry_id"], first["entry_id"])

    def test_same_source_different_focus_creates_second_candidate(self):
        first = self.distill(
            {
                "definition": evidence(
                    "polar field precursor predicts cycle amplitude",
                    "polar field strength at solar minimum is a robust precursor",
                )
            }
        )
        second = self.distill(
            {
                "definition": evidence(
                    "weak polar fields precede weak cycles",
                    "weak polar fields precede weak cycles",
                )
            },
            research_question="Do weak polar fields precede weak solar cycles?",
            focus="weak polar fields precede weak cycles",
            title="Weak polar fields and weak cycles",
        )
        self.assertFalse(second["idempotent"])
        self.assertNotEqual(first["entry_id"], second["entry_id"])

    def test_focus_normalization_is_idempotent(self):
        content = {
            "definition": evidence(
                "polar field precursor predicts amplitude",
                "polar field strength at solar minimum is a robust precursor",
            )
        }
        first = self.distill(content)
        second = self.distill(
            content,
            focus="  POLAR   FIELD precursor for next solar cycle amplitude  ",
        )
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["entry_id"], first["entry_id"])

    def test_high_confidence_rejected_for_single_abstract(self):
        with self.assertRaises(ContractError) as ctx:
            self.distill(
                {
                    "definition": evidence(
                        "polar field precursor predicts amplitude",
                        "polar field strength at solar minimum is a robust precursor",
                    )
                },
                confidence="high",
            )
        self.assertEqual(ctx.exception.error_code, "confidence_cap_exceeded")

    def test_focus_must_be_bound_to_research_question(self):
        with self.assertRaises(ContractError) as ctx:
            self.distill(
                {
                    "definition": evidence(
                        "polar field precursor predicts amplitude",
                        "polar field strength at solar minimum is a robust precursor",
                    )
                },
                research_question="Is the Waldmeier amplitude-rise relation stable?",
                focus="polar field precursor method comparison",
            )
        self.assertEqual(ctx.exception.error_code, "focus_not_related_to_question")

    def test_background_source_rejected_for_specific_focus(self):
        self.store.upsert_lit_source(
            {
                "source_id": "openalex:W7654321",
                "title": "The Solar Cycle",
                "authors": ["Review Author"],
                "year": 2015,
                "doi": "10.1000/review",
                "url": "https://example.org/review",
                "abstract": "Solar activity indicators include the magnetic field.",
            }
        )
        with self.assertRaises(ContractError) as ctx:
            literature.distill_literature(
                self.store,
                "openalex:W7654321",
                "mechanism",
                "Polar field precursor mechanism",
                {
                    "claim": evidence(
                        "polar field precursor predicts amplitude",
                        "the magnetic field",
                    )
                },
                research_question=(
                    "Does the polar field precursor predict solar cycle amplitude?"
                ),
                focus="polar field precursor and solar cycle amplitude",
            )
        self.assertEqual(ctx.exception.error_code, "source_not_related_to_focus")

    def test_source_title_cannot_substitute_for_abstract_relevance(self):
        self.store.upsert_lit_source(
            {
                "source_id": "openalex:Wtitle-only",
                "title": "Polar field precursor and solar cycle amplitude",
                "authors": ["Review Author"],
                "year": 2015,
                "doi": "10.1000/title-only",
                "url": "https://example.org/title-only",
                "abstract": "This review discusses a magnetic indicator.",
            }
        )
        with self.assertRaises(ContractError) as ctx:
            literature.distill_literature(
                self.store,
                "openalex:Wtitle-only",
                "mechanism",
                "Polar field precursor mechanism",
                {
                    "claim": evidence(
                        "polar field precursor predicts amplitude",
                        "a magnetic indicator",
                    )
                },
                research_question=(
                    "Does the polar field precursor predict solar cycle amplitude?"
                ),
                focus="polar field precursor and solar cycle amplitude",
            )
        self.assertEqual(ctx.exception.error_code, "source_not_related_to_focus")

    def test_candidate_title_cannot_substitute_for_distilled_content_relevance(self):
        with self.assertRaises(ContractError) as ctx:
            self.distill(
                {
                    "definition": evidence(
                        "This is a robust indicator near minimum.",
                        "at solar minimum is a robust precursor",
                    )
                },
                title="Polar field precursor for next solar cycle amplitude",
            )
        self.assertEqual(
            ctx.exception.error_code, "distill_output_not_related_to_focus"
        )

    def test_distill_unknown_source_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            literature.distill_literature(
                self.store,
                "openalex:W000",
                "concept",
                "标题",
                {"definition": "evidence_gap"},
                focus="polar field precursor",
                research_question="Does the polar field precursor work?",
            )
        self.assertEqual(ctx.exception.error_code, "lit_source_not_found")


class TestLiteratureDeltaAndTaskBundle(StoreTestCase):
    def test_source_deltas_are_material_and_idempotent(self):
        record = {
            "source_id": "openalex:Wdelta",
            "provider": "openalex",
            "source_version": "1",
            "title": "Polar field precursor update",
            "authors": ["Ada Solar"],
            "year": 2026,
            "abstract": "Polar field strength predicts the next solar cycle amplitude.",
            "is_retracted": False,
        }
        first = self.store.upsert_lit_source(record)
        self.assertEqual(first["delta_types"], ["new_source"])
        second = self.store.upsert_lit_source(record)
        self.assertEqual(second["delta_types"], [])

        versioned = self.store.upsert_lit_source(
            {
                **record,
                "source_version": "2",
                "abstract": record["abstract"] + " Updated.",
            }
        )
        self.assertEqual(versioned["delta_types"], ["new_version"])
        retracted = self.store.upsert_lit_source(
            {
                **record,
                "source_version": "2",
                "abstract": record["abstract"] + " Updated.",
                "is_retracted": True,
            }
        )
        self.assertEqual(retracted["delta_types"], ["source_retracted"])
        event_types = [
            row["event_type"]
            for row in reversed(self.store.list_lit_delta_events(limit=10))
        ]
        self.assertEqual(
            event_types,
            ["new_source", "new_version", "source_retracted"],
        )

    def test_baseline_is_seeded_once_and_excluded_from_change_count(self):
        cache_openalex_source(self.store)
        first = self.store.seed_literature_baseline()
        second = self.store.seed_literature_baseline()
        self.assertEqual(first, {"inserted": 1, "existing": 0})
        self.assertEqual(second, {"inserted": 0, "existing": 1})
        self.assertEqual(
            len(
                self.store.list_lit_delta_events(
                    event_type="baseline_source", include_baseline=True
                )
            ),
            1,
        )
        self.assertEqual(self.store.count_lit_delta_events(), 1)

    def test_task_bundle_is_bounded_relevant_and_immutable(self):
        cache_openalex_source(self.store)
        self.store.upsert_lit_source(
            {
                "source_id": "arxiv:unrelated",
                "title": "Coronal rain morphology",
                "authors": ["B. Author"],
                "year": 2025,
                "abstract": "Coronal rain traces cooling loops in active regions.",
            }
        )
        first = literature.build_literature_task_bundle(
            self.store,
            "Does the polar field precursor predict solar cycle amplitude?",
            "polar field precursor and solar cycle amplitude",
            limit=3,
            run_id="run-bundle",
        )
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["source_count"], 1)
        self.assertEqual(first["sources"][0]["source_id"], "openalex:W1234567")
        original_abstract = first["sources"][0]["abstract"]

        repeated = literature.build_literature_task_bundle(
            self.store,
            "Does the polar field precursor predict solar cycle amplitude?",
            "polar field precursor and solar cycle amplitude",
            limit=3,
            run_id="run-bundle",
        )
        self.assertEqual(repeated["bundle_id"], first["bundle_id"])

        source = self.store.get_lit_source("openalex:W1234567")
        self.store.upsert_lit_source(
            {
                **source,
                "source_version": "2",
                "abstract": f"{ABSTRACT} Updated evidence.",
            }
        )
        updated = literature.build_literature_task_bundle(
            self.store,
            "Does the polar field precursor predict solar cycle amplitude?",
            "polar field precursor and solar cycle amplitude",
            limit=3,
            run_id="run-bundle",
        )
        self.assertNotEqual(updated["bundle_id"], first["bundle_id"])
        frozen = literature.read_literature_task_bundle(self.store, first["bundle_id"])
        self.assertEqual(frozen["sources"][0]["abstract"], original_abstract)

    def test_task_bundle_can_require_a_compound_focus_anchor(self):
        cache_openalex_source(self.store)
        self.store.upsert_lit_source(
            {
                "source_id": "openalex:scattered-terms",
                "title": "Statistical measurement study of a planetary field",
                "authors": ["B. Author"],
                "year": 2026,
                "abstract": (
                    "A polar atmospheric region is analysed under a changing "
                    "measurement regime. The statistical null concerns a field "
                    "observable, but not the solar precursor."
                ),
            }
        )

        bundle = literature.build_literature_task_bundle(
            self.store,
            "Does the polar field precursor predict solar cycle amplitude?",
            "polar field measurement regime and statistical null",
            ranking_focus="polar field measurement regime statistical null",
            required_anchor_phrases=["polar field"],
            limit=5,
            run_id="run-compound-anchor",
        )

        self.assertEqual(bundle["source_count"], 1)
        self.assertEqual(bundle["sources"][0]["source_id"], "openalex:W1234567")
        self.assertIn("polar field", bundle["sources"][0]["matched_focus_phrases"])


class TestGroundingWarnings(StoreTestCase):
    def test_valid_kb_id_passes(self):
        proposed = service.propose(
            self.store,
            entry_type="concept",
            title="polar field precursor",
            content={"definition": "Polar fields precede cycle amplitude."},
            source_type="expert",
            source_ref="expert:reviewer-a",
            confidence="medium",
        )
        entry_id = proposed["entry"]["id"]
        warnings = service.grounding_warnings(
            self.store,
            [{"id": "cand_a", "evidence_ids": [entry_id], "knowledge_gap": False}],
        )
        self.assertEqual(warnings, [])

    def test_missing_or_unknown_kb_id_warns(self):
        warnings = service.grounding_warnings(
            self.store,
            [
                {"id": "cand_a", "evidence_ids": ["ev_up1"], "knowledge_gap": False},
                {
                    "id": "cand_b",
                    "evidence_ids": ["kb_concept_ghost_999"],
                    "knowledge_gap": False,
                },
            ],
        )
        self.assertEqual([row["id"] for row in warnings], ["cand_a", "cand_b"])
        self.assertEqual(warnings[1]["kb_ids_cited"], ["kb_concept_ghost_999"])

    def test_knowledge_gap_declaration_passes(self):
        warnings = service.grounding_warnings(
            self.store,
            [{"id": "cand_a", "evidence_ids": [], "knowledge_gap": True}],
        )
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
