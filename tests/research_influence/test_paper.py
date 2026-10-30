"""Paper assembly: located sections, honest absences, reproducible fingerprint."""

from __future__ import annotations

from jw.research_influence.contracts import (
    AccessRoute,
    ConfidenceBand,
    MissingReason,
    OpenDataKind,
)
from jw.research_influence.fulltext import FulltextResult
from jw.research_influence.paper import (
    build_paper,
    normalise_section_name,
    open_data_from_text,
    references_from_text,
    sections_from_pages,
)

PAGE_ONE = """The light curve of SN 2005cf
1. Introduction
Supernovae of Type Ia are standardisable candles used to measure distances.
2. Observations and data reduction
We obtained photometry with the 2.5 m telescope over 60 nights.
3. Results
The B-band declined by 1.2 magnitudes in 15 days.
4. Discussion
The decline rate correlates with peak luminosity.
5. Conclusions
SN 2005cf is a normal Type Ia supernova.
6. Acknowledgements
We thank the telescope operators.
7. References
[1] Astro J. 2005, ApJ, 621, 1, doi:10.1086/427468
[2] Other A. 2006, MNRAS, 371, 2
[3] Third B. 2007, AJ, 133, 3
"""

PAGE_TWO = """Data availability
The reduced photometry is available at https://zenodo.org/record/1234567 and the
analysis code is available at https://github.com/example/sn2005cf.
Appendix A
Additional tables are provided in the online supplement.
"""


def test_headings_map_onto_canonical_sections():
    """Numbered headings become canonical section names."""
    assert normalise_section_name("1. Introduction") == "introduction"
    assert normalise_section_name("2. Observations and data reduction") == "methods"
    assert normalise_section_name("Data availability") == "data_availability"
    assert normalise_section_name("Acknowledgments") == "acknowledgements"
    assert normalise_section_name("Random sentence that is not a heading") == ""


def test_sections_are_split_and_text_is_preserved():
    """Section text is kept verbatim, not summarised."""
    sections = sections_from_pages((PAGE_ONE,))
    assert "introduction" in sections
    assert "standardisable candles" in sections["introduction"]
    assert "methods" in sections
    assert "60 nights" in sections["methods"]
    assert "conclusions" in sections


def test_references_are_numbered_and_dois_extracted():
    """Reference parsing trusts typography and keeps raw text."""
    references = references_from_text((PAGE_ONE,))
    assert len(references) == 3
    assert references[0].index == 1
    assert references[0].doi == "10.1086/427468"
    assert references[0].year == 2005
    assert "Astro J. 2005" in references[0].raw
    assert references[0].match_confidence is ConfidenceBand.LOW
    assert references[1].doi == ""


def test_open_data_needs_a_data_statement_section():
    """A URL in the body is not a data statement."""
    body_only = ("1. Introduction\nSee https://zenodo.org/record/1 for context.\n",)
    assert open_data_from_text(body_only) == ()


def test_open_data_finds_repositories_and_kinds():
    """Data and code statements are separated and deduplicated."""
    records = open_data_from_text((PAGE_TWO,))
    urls = {record.url for record in records}
    assert "https://zenodo.org/record/1234567" in urls
    assert "https://github.com/example/sn2005cf" in urls
    kinds = {record.repository: record.kind for record in records}
    assert kinds["Zenodo"] is OpenDataKind.DATA
    assert kinds["GitHub"] is OpenDataKind.CODE
    assert all(record.accession for record in records)
    assert all("available" in record.statement for record in records)


def test_metadata_only_paper_declares_what_is_missing():
    """Without full text the absence is FULL_TEXT_UNAVAILABLE, not 'none'."""
    paper = build_paper(
        paper_id="paper:1",
        doi="10.1093/mnras/stv1947",
        openalex={
            "title": "A title",
            "abstract": "An abstract",
            "year": 2015,
            "authors": [{"name": "Jane Astro"}],
            "venue": {"name": "MNRAS"},
            "cited_by_count": 12,
            "referenced_works": ("W1", "W2"),
            "work_id": "W1",
        },
    )
    assert paper.title == "A title"
    assert paper.has_full_text is False
    assert paper.missing["sections"] is MissingReason.FULL_TEXT_UNAVAILABLE
    assert paper.missing_fields()
    assert "references" not in paper.missing
    assert len(paper.references) == 2
    assert paper.references[0].resolved_work_id == "W1"


def test_full_text_paper_carries_sections_and_open_data():
    """A readable paper gains sections and data statements with locators."""
    fulltext = FulltextResult(
        path="/tmp/x.pdf", is_pdf=True, pages=(PAGE_ONE, PAGE_TWO), doi="10.1/x"
    )
    paper = build_paper(
        paper_id="paper:2",
        doi="10.1/x",
        openalex={
            "title": "The light curve of SN 2005cf",
            "abstract": "Abstract text",
            "year": 2005,
            "authors": [{"name": "Jane Astro", "institution": "IoA", "country": "GB"}],
            "venue": {"name": "MNRAS", "publisher": "OUP"},
            "topics": [{"name": "Supernovae"}],
        },
        fulltext=fulltext,
        access=None,
    )
    assert paper.has_full_text
    assert "methods" in paper.sections
    assert paper.open_data
    assert paper.keywords == ("Supernovae",)
    assert paper.authors[0].country == "GB"
    assert paper.fulltext_pages == 2
    assert paper.coverage() == 1.0


def test_crossref_reference_deposit_beats_text_parsing():
    """Deposited references are authoritative; text parsing is the fallback."""
    paper = build_paper(
        paper_id="paper:3",
        doi="10.1/x",
        openalex={"title": "T"},
        crossref={
            "title": "T",
            "references": [
                {
                    "index": 1,
                    "doi": "10.1088/0004-637X/1/1/1",
                    "raw": "Ref",
                    "year": 2001,
                }
            ],
        },
        fulltext=FulltextResult(path="/tmp/x.pdf", is_pdf=True, pages=(PAGE_ONE,)),
    )
    assert len(paper.references) == 1
    assert paper.references[0].match_confidence is ConfidenceBand.HIGH
    assert "references_from_crossref_deposit" in paper.notes


def test_access_route_is_carried_on_the_record():
    """The route travels with the paper so the model can flag degradation."""
    from jw.research_influence.access import decide_access
    from jw.research_influence.access import AccessCandidate

    decision = decide_access(
        doi="10.1/x",
        candidates=[
            AccessCandidate(
                url="https://arxiv.org/pdf/1",
                route=AccessRoute.PREPRINT,
                provider="arxiv",
            )
        ],
        has_abstract=True,
    )
    paper = build_paper(
        paper_id="paper:4", doi="10.1/x", access=decision, openalex={"title": "T"}
    )
    assert paper.access_route is AccessRoute.PREPRINT
    assert paper.access is not None
    assert "preprint_is_not_the_version_of_record" in paper.access.note


def test_fingerprint_round_trips_through_json():
    """A record rebuilt from JSON hashes identically."""
    paper = build_paper(
        paper_id="paper:5",
        doi="10.1093/MNRAS/STV1947",
        openalex={
            "title": "T",
            "abstract": "A",
            "year": 2015,
            "authors": [{"name": "Jane Astro"}],
            "venue": {"name": "MNRAS"},
            "topics": [{"name": "Supernovae"}],
        },
        fulltext=FulltextResult(
            path="/tmp/x.pdf", is_pdf=True, pages=(PAGE_ONE, PAGE_TWO)
        ),
    )
    rebuilt = type(paper).from_dict(paper.to_dict())
    assert rebuilt.doi == "10.1093/mnras/stv1947"
    assert rebuilt.fingerprint() == paper.fingerprint()


def test_summarise_counts_access_routes():
    """The run summary reports how much degradation happened."""
    from jw.research_influence.contracts import summarise

    paper = build_paper(paper_id="paper:6", doi="10.1/x", openalex={"title": "T"})
    summary = summarise([paper])
    assert summary["papers"] == 1
    assert summary["metadata_only"] == 1
    assert summary["with_full_text"] == 0
    assert "routes" in summary


def test_a_pdf_title_line_is_used_and_flagged_as_weak_evidence():
    """A supplied PDF supplies its own title, marked LOW with a page locator."""
    paper = build_paper(
        paper_id="paper:local:abc",
        fulltext=FulltextResult(
            path="/tmp/x.pdf", is_pdf=True, pages=(PAGE_ONE, PAGE_TWO)
        ),
    )
    assert paper.title == "The light curve of SN 2005cf"
    entries = paper.provenance["title"]
    assert entries and entries[0].provider == "pdf_text"
    assert entries[0].confidence == ConfidenceBand.LOW
    assert entries[0].locator is not None and entries[0].locator.page == 1
    assert "title_from_pdf_text_layer" in paper.notes
    # An index record would be stronger evidence, so the flag stays visible.
    assert "title" not in paper.missing


def test_a_heading_first_page_does_not_become_the_title():
    """When page one starts with a label, no title is invented."""
    paper = build_paper(
        paper_id="paper:local:def",
        fulltext=FulltextResult(
            path="/tmp/y.pdf",
            is_pdf=True,
            pages=("Abstract\nWe report a survey of nearby galaxies.\n",),
        ),
    )
    assert paper.title == ""
    assert paper.missing["title"] == MissingReason.NOT_FOUND


def test_kerning_splits_are_flagged_not_rewritten():
    """A word split by the extractor is reported, because it cannot be verified."""
    from jw.research_influence.paper import detect_kerning_splits

    notes = detect_kerning_splits(("Photometric typing of T ype Ia supernovae",))
    assert notes and notes[0].startswith("pdf_text_kerning_suspect:T ype")

    paper = build_paper(
        paper_id="paper:local:ghi",
        fulltext=FulltextResult(
            path="/tmp/z.pdf",
            is_pdf=True,
            pages=("T ype Ia supernovae in the nearby universe\n",),
        ),
    )
    # The characters are kept verbatim: guessing "Type" would be an invention.
    assert paper.title == "T ype Ia supernovae in the nearby universe"
    assert any(note.startswith("pdf_text_kerning_suspect") for note in paper.notes)


def test_the_per_year_history_reaches_the_record(tmp_path):
    """Without counts_by_year every horizon label disappears, silently."""
    from jw.research_influence.cohort import citations_within
    from jw.research_influence.paper import build_paper

    payload = {
        "work_id": "W1",
        "doi": "10.1/x",
        "title": "T",
        "year": 2021,
        "cited_by_count": 40,
        "counts_by_year": (
            {"year": 2024, "cited_by_count": 3},
            {"year": 2021, "cited_by_count": 5},
            {"year": 2022, "cited_by_count": 4},
        ),
    }
    paper = build_paper(paper_id="paper:W1", doi="10.1/x", openalex=payload)
    assert paper.counts_by_year == ((2021, 5), (2022, 4), (2024, 3))
    assert citations_within(paper, horizon=3) == 9  # lifetime count would be 40
    assert citations_within(paper, horizon=1) == 5


def test_local_file_urls_are_collapsed_to_their_name_on_windows():
    r"""A Windows path has no forward slash after the scheme.

    Two independent runs stage the same PDF under different temporary
    directories, so if the whole path survives the scrub their fingerprints
    disagree even though the paper is identical.
    """
    from jw.research_influence.contracts import _scrub_local_path

    first = r"file://C:\Users\runner\AppData\Local\Temp\a\store\benchmark.pdf"
    second = r"file://C:\Users\runner\AppData\Local\Temp\b\store\benchmark.pdf"

    assert _scrub_local_path(first) == "file://<local>/benchmark.pdf"
    assert _scrub_local_path(second) == _scrub_local_path(first)
