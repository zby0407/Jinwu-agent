"""Features: nothing leaks, and every hole carries a reason."""

from __future__ import annotations

import math

from jw.research_influence.cohort import HORIZON_YEARS
from jw.research_influence.contracts import (
    AuthorRecord,
    MissingReason,
    OpenDataKind,
    OpenDataRecord,
    PaperRecord,
    ReferenceRecord,
    VenueRecord,
)
from jw.research_influence.features import (
    FEATURES,
    feature_matrix,
    feature_names,
    feature_row,
    targets,
    venue_tier,
)

FULL_PAPER = PaperRecord(
    paper_id="paper:full",
    title="A survey of supernovae",
    abstract="We present 42 supernovae. We find a 3 sigma trend.",
    year=2015,
    doi="10.1/full",
    venue=VenueRecord(name="MNRAS", publisher="OUP", issn="0035-8711"),
    authors=(
        AuthorRecord(name="A", institution="IoA", country="GB", is_corresponding=True),
        AuthorRecord(name="B", institution="MIT", country="US"),
    ),
    references=(
        ReferenceRecord(index=1, raw="x", doi="10.1/x", year=2001),
        ReferenceRecord(index=2, raw="y", doi="", year=None),
    ),
    open_data=(
        OpenDataRecord(kind=OpenDataKind.DATA, url="https://zenodo.org/record/1", repository="Zenodo"),
        OpenDataRecord(kind=OpenDataKind.CODE, url="https://github.com/x/y", repository="GitHub"),
    ),
    sections={
        "methods": "2. Methods\nWe used 5 bands and 12 targets.",
        "data_availability": "Data availability\nAt https://zenodo.org/record/1",
    },
    keywords=("supernovae",),
    cited_by_count=30,
    counts_by_year=((2015, 5), (2016, 5), (2017, 5)),
    fulltext_pages=9,
)

METADATA_ONLY = PaperRecord(
    paper_id="paper:meta",
    title="A title",
    abstract="",
    year=2018,
    doi="10.1/meta",
    cited_by_count=3,
    counts_by_year=((2018, 1), (2019, 1), (2020, 1)),
)


def test_every_feature_is_declared_once():
    """The specification is the single source of column truth."""
    names = [spec.name for spec in FEATURES]
    assert len(names) == len(set(names))
    assert feature_names(include_text_level=False) == tuple(
        spec.name for spec in FEATURES if not spec.requires_full_text
    )
    assert set(feature_names()) > set(feature_names(include_text_level=False))


def test_citation_count_is_never_a_feature():
    """Total citations are the outcome; using them would be leakage."""
    assert "cited_by_count" not in feature_names()
    assert not any("citation" in spec.name for spec in FEATURES)


def test_full_paper_features_are_all_present():
    """With full text and metadata nothing should be missing."""
    row = feature_row(FULL_PAPER, reference_year=2024)
    assert not row.missing
    assert row.values["author_count"] == 2
    assert row.values["country_count"] == 2
    assert row.values["international_collaboration"] == 1.0
    assert row.values["venue_tier"] == 3
    assert row.values["references_with_doi_share"] == 0.5
    assert row.values["has_data_statement"] == 1.0
    assert row.values["has_code_statement"] == 1.0
    assert row.values["data_record_count"] == 2
    assert row.values["page_count"] == 9
    assert row.values["methods_numeric_density"] > 0


def test_metadata_only_paper_declares_full_text_absence():
    """Absence is typed: this paper is not 'no data', it is 'not readable'."""
    row = feature_row(METADATA_ONLY, reference_year=2024)
    assert row.missing["section_count"] is MissingReason.FULL_TEXT_UNAVAILABLE
    assert row.missing["has_data_statement"] is MissingReason.FULL_TEXT_UNAVAILABLE
    assert math.isnan(row.values["has_data_statement"])
    assert row.values["author_count"] == 0 or math.isnan(row.values["author_count"])
    assert row.has_full_text is False


def test_absent_values_are_nan_not_zero():
    """A missing number must not look like a measured zero."""
    row = feature_row(METADATA_ONLY, reference_year=2024)
    assert math.isnan(row.get("methods_length"))
    assert row.get("methods_length") != 0.0


def test_venue_tier_accepts_definite_articles():
    """'The Astrophysical Journal' and 'ApJ' must agree."""
    assert venue_tier(VenueRecord(name="ApJ")) == 3
    assert venue_tier(VenueRecord(name="The Astrophysical Journal")) == 3
    assert venue_tier(VenueRecord(name="Some Unknown Letters")) is None
    assert venue_tier(VenueRecord(name="")) is None


def test_matrix_records_missing_counts_per_column():
    """The matrix reports how many rows each column lost."""
    matrix = feature_matrix([FULL_PAPER, METADATA_ONLY], reference_year=2024)
    assert len(matrix) == 2
    assert matrix.missing_counts["section_count"] == 1
    assert matrix.missing_counts["author_count"] >= 1
    assert matrix.missing_share("section_count") == 0.5
    assert matrix.text_level_missing_share() > 0
    assert matrix.to_dict()["records"] == 2


def test_matrix_is_rectangular_and_finite_or_nan():
    """No infinities: every cell is a number or a declared absence."""
    matrix = feature_matrix([FULL_PAPER, METADATA_ONLY], reference_year=2024)
    for row in matrix.rows:
        assert len(row) == len(matrix.names)
        assert all(not math.isinf(value) for value in row)


def test_targets_exclude_censored_papers():
    """A window that has not closed is not a label."""
    recent = PaperRecord(
        paper_id="paper:recent",
        year=2023,
        counts_by_year=((2023, 1), (2024, 2)),
    )
    old = PaperRecord(
        paper_id="paper:old",
        year=2015,
        counts_by_year=((2015, 4), (2016, 4), (2017, 4), (2018, 90)),
    )
    result = targets([recent, old], reference_year=2024)
    assert result.horizon == HORIZON_YEARS
    assert result.available == (False, True)
    assert result.censored == (True, False)
    assert result.raw == (3, 12)
    assert math.isclose(result.values[1], math.log1p(12))
    assert result.usable == (1,)
    assert result.summary()["labelled"] == 1


def test_targets_of_unlabelled_paper_are_nan():
    """Without a per-year history the label is unknown, not zero."""
    paper = PaperRecord(paper_id="paper:x", year=2010, cited_by_count=100)
    result = targets([paper], reference_year=2024)
    assert result.available == (False,)
    assert math.isnan(result.values[0])
