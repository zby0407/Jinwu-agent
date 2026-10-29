"""The upload path: what the text layer offers, and what the index adds."""

from __future__ import annotations

import json

import pytest

from jw.research_influence.contracts import (
    AccessRoute,
    ConfidenceBand,
    PaperRecord,
    VenueRecord,
)
from jw.research_influence.enrich import (
    apply_metadata,
    enrich_paper,
    lookup_work,
    title_similarity,
)
from jw.research_influence.metadata import (
    apply_pdf_metadata,
    extract_pdf_metadata,
    looks_like_author_line,
    title_from_pages,
)
from jw.research_influence.providers.base import ProviderPolicy
from jw.research_influence.providers.openalex import parse_work
from tests.research_influence.conftest import FakeTransport

PAGE_ONE = """arXiv:2401.00123v2  [math.PR]  2 Apr 2024
A QUICK PROBABILITY-ORIENTED INTRODUCTION TO OPERATOR
SPLITTING METHODS
M.B. VOVCHANSKYI
Abstract. The survey is devoted to operator splitting methods.
"""

PAGE_SENTENCE_CASE = """Energy-momentum Tensor for a Field and Particle in Interaction
Roderick I. Sutherland
Centre for Time, University of Sydney, NSW 2006 Australia
rod.sutherland@sydney.edu.au
Abstract
This paper focuses on the basic system of a field and a particle in interaction.
"""


def _work(**overrides):
    """A raw OpenAlex work, the shape ``parse_work`` actually receives."""
    payload = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.1093/mnras/stv1947",
        "title": "A quick probability-oriented introduction to operator splitting methods",
        "publication_year": 2024,
        "abstract_inverted_index": {
            "The": [0],
            "survey": [1],
            "is": [2],
            "devoted": [3],
        },
        "authorships": [
            {
                "author_position": "first",
                "author": {"display_name": "M.B. Vovchanskyi", "orcid": ""},
                "institutions": [
                    {"display_name": "Kyiv University", "country_code": "UA"}
                ],
                "is_corresponding": True,
            }
        ],
        "primary_location": {
            "source": {
                "display_name": "Monthly Notices of the Royal Astronomical Society",
                "host_organization_name": "Oxford University Press",
                "type": "journal",
            }
        },
        "open_access": {"is_oa": False, "oa_status": "closed"},
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
        "cited_by_count": 42,
        "counts_by_year": [
            {"year": 2024, "cited_by_count": 12},
            {"year": 2025, "cited_by_count": 30},
        ],
        "topics": [
            {"display_name": "Probability theory"},
            {"display_name": "Numerical analysis"},
        ],
    }
    payload.update(overrides)
    return payload


def _paper(**overrides) -> PaperRecord:
    base = {
        "paper_id": "paper:local:abc",
        "title": "",
        "access_route": AccessRoute.USER_SUPPLIED,
        "missing": {
            "title": "not_found",
            "authors": "not_reported",
            "venue": "not_reported",
        },
    }
    base.update(overrides)
    return PaperRecord(**base)


# --- text layer --------------------------------------------------------------


def test_the_stamp_above_the_title_is_skipped_not_fatal():
    """An arXiv stamp on line one must not hide the title below it."""
    assert title_from_pages([PAGE_ONE]) == (
        "A QUICK PROBABILITY-ORIENTED INTRODUCTION TO OPERATOR",
        "SPLITTING METHODS",
    )


def test_a_sentence_case_title_is_read_without_its_author_and_affiliation():
    metadata = extract_pdf_metadata([PAGE_SENTENCE_CASE])
    assert (
        metadata.title
        == "Energy-momentum Tensor for a Field and Particle in Interaction"
    )
    assert metadata.authors == ("Roderick I. Sutherland",)
    # The affiliation looks exactly like a two-part name list; it must not be
    # accepted as authors.
    assert all("University" not in name for name in metadata.authors)
    assert metadata.confidence in {ConfidenceBand.LOW, ConfidenceBand.MEDIUM}


def test_an_all_caps_title_continuation_is_not_read_as_an_author():
    metadata = extract_pdf_metadata([PAGE_ONE])
    assert metadata.authors == ("M.B. VOVCHANSKYI",)
    assert "SPLITTING METHODS" not in metadata.authors


def test_the_arxiv_identifier_and_the_stamp_year_are_recovered():
    metadata = extract_pdf_metadata([PAGE_ONE])
    assert metadata.arxiv_id == "2401.00123"
    assert metadata.arxiv_doi == "10.48550/arXiv.2401.00123"
    assert metadata.year == 2024


def test_a_year_without_a_date_marker_is_not_guessed():
    """A bare four-digit number is a magnitude as often as a publication year."""
    metadata = extract_pdf_metadata(
        ["A survey of things\nWe use 2020 samples in total.\n"]
    )
    assert metadata.year is None
    assert "year" not in metadata.evidence


def test_page_starts_that_are_only_labels_produce_no_title():
    metadata = extract_pdf_metadata(["Abstract\nWe measure the thing.\n"])
    assert metadata.title == ""


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Roderick I. Sutherland", True),
        ("M.B. VOVCHANSKYI", True),
        ("JOHN SMITH, MARY JONES", True),
        ("SPLITTING METHODS", False),
        ("Centre for Time, University of Sydney", False),
        ("rod.sutherland@sydney.edu.au", False),
        ("Abstract", False),
        ("1. Introduction", False),
    ],
)
def test_author_lines_are_told_apart_from_affiliations_and_titles(line, expected):
    assert looks_like_author_line(line) is expected


def test_pdf_metadata_fills_only_empty_fields_with_a_low_band():
    paper = _paper(title="Kept title")
    metadata = extract_pdf_metadata([PAGE_ONE])
    filled = apply_pdf_metadata(paper, metadata)
    assert filled.title == "Kept title"  # the text the user can see wins
    assert filled.authors and filled.authors[0].name == "M.B. VOVCHANSKYI"
    assert filled.year == 2024
    entry = filled.provenance_for("year")[0]
    assert entry.provider == "pdf_text_layer"
    assert entry.confidence in {ConfidenceBand.LOW, ConfidenceBand.MEDIUM}
    assert any(note.startswith("pdf_metadata:") for note in filled.notes)


def test_the_recovered_metadata_is_stored_so_enrichment_can_reuse_it():
    paper = apply_pdf_metadata(_paper(), extract_pdf_metadata([PAGE_ONE]))
    payload = next(note for note in paper.notes if note.startswith("pdf_metadata:"))
    stored = json.loads(payload.split(":", 1)[1])
    assert stored["arxiv_id"] == "2401.00123"
    assert stored["authors"] == ["M.B. VOVCHANSKYI"]


# --- enrichment --------------------------------------------------------------


def test_title_similarity_ignores_case_and_punctuation():
    assert (
        title_similarity("The MUSE view of Abell 370", "the muse view of abell 370.")
        > 0.99
    )
    assert title_similarity("A survey of galaxies", "Star formation in dwarfs") < 0.5


def test_a_doi_lookup_fills_the_bibliographic_fields():
    fake_transport = FakeTransport()
    fake_transport.add("stv1947", _work())
    paper = _paper(doi="10.1093/mnras/stv1947")
    result = enrich_paper(paper, transport=fake_transport, policy=ProviderPolicy())
    assert result.status == "enriched_doi"
    assert result.matched
    assert set(result.filled) >= {"authors", "venue", "references", "cited_by_count"}
    assert result.paper.venue.name.startswith("Monthly Notices")
    assert result.paper.cited_by_count == 42
    assert result.paper.counts_by_year == ((2024, 12), (2025, 30))
    assert result.paper.provenance_for("venue")[0].provider == "openalex"


def test_a_title_match_requires_a_high_similarity():
    fake_transport = FakeTransport()
    fake_transport.add(
        "works?",
        {"results": [_work(title="Operator splitting methods for stochastic PDEs")]},
    )
    result = enrich_paper(
        _paper(
            title="A quick probability-oriented introduction to operator splitting methods"
        ),
        transport=fake_transport,
        policy=ProviderPolicy(),
    )
    assert result.status == "refused_similarity"
    assert result.paper.venue.name == ""


def test_the_year_must_agree_for_a_title_match():
    fake_transport = FakeTransport()
    fake_transport.add(
        "works?",
        {
            "results": [
                _work(
                    title="A quick probability-oriented introduction to operator splitting methods",
                    publication_year=2014,
                )
            ]
        },
    )
    result = enrich_paper(
        _paper(
            title="A quick probability-oriented introduction to operator splitting methods",
            year=2024,
        ),
        transport=fake_transport,
        policy=ProviderPolicy(),
    )
    assert result.status == "refused_similarity"
    assert any(note.startswith("year_mismatch_skipped") for note in result.notes)


def test_enrichment_never_overwrites_what_the_pdf_said():
    paper = _paper(
        title="A quick probability-oriented introduction to operator splitting methods",
        abstract="Our own abstract text.",
        venue=VenueRecord(name="Some Workshop Proceedings"),
        year=2024,
    )
    filled, names = apply_metadata(paper, parse_work(_work()), method="enriched_title")
    assert filled.title == paper.title
    assert filled.abstract == "Our own abstract text."
    assert filled.venue.name == "Some Workshop Proceedings"
    assert "title" not in names and "abstract" not in names and "venue" not in names
    assert "authors" in names


def test_a_failed_lookup_leaves_the_paper_untouched():
    fake_transport = FakeTransport()
    fake_transport.add("works?", _work(), status=503)
    paper = _paper(title="Something nobody indexed")
    result = enrich_paper(paper, transport=fake_transport, policy=ProviderPolicy())
    assert not result.matched
    assert result.paper is paper
    assert "paper_left_unchanged" in result.notes


def test_the_arxiv_doi_is_tried_when_the_pdf_carries_only_an_identifier():
    fake_transport = FakeTransport()
    fake_transport.add("arxiv.2401.00123", _work(doi="10.48550/arXiv.2401.00123"))
    paper = apply_pdf_metadata(_paper(), extract_pdf_metadata([PAGE_ONE]))
    work, status, similarity, candidates, _notes = lookup_work(
        paper=paper, transport=fake_transport, policy=ProviderPolicy()
    )
    assert status == "enriched_arxiv_doi"
    assert work is not None
    assert similarity == 1.0
    assert candidates == 1
