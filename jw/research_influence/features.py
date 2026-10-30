"""Features available at publication time, plus a target on a fixed horizon.

Three choices keep the model honest:

* nothing that only exists *after* the horizon is used as a feature, so
  ``cited_by_count`` never leaks into its own prediction;
* a feature that cannot be computed is ``NaN`` with a recorded reason, never a
  zero — ``HistGradientBoosting`` handles ``NaN`` natively, so no imputation
  hides the absence;
* every feature declares which bias route could capture it, which is what the
  audit later looks for.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .cohort import HORIZON_YEARS, citations_within, is_right_censored
from .contracts import MissingReason, PaperRecord, VenueRecord

__all__ = [
    "FEATURES",
    "FEATURE_SCHEMA",
    "VENUE_TIERS",
    "FeatureMatrix",
    "FeatureRow",
    "FeatureSpec",
    "TargetSet",
    "feature_matrix",
    "feature_names",
    "feature_row",
    "targets",
    "venue_tier",
]

FEATURE_SCHEMA = "jw.research-influence.features/v1"

#: Journal tiers.  This is a *contested* proxy: it is the kind of signal that
#: makes venue prestige enter a prediction, so the audit treats it as a bias
#: route rather than as a neutral measurement.
VENUE_TIERS: Mapping[str, int] = {
    "nature": 4,
    "science": 4,
    "nature astronomy": 4,
    "nature physics": 4,
    "physical review letters": 3,
    "apj": 3,
    "apjl": 3,
    "astrophysical journal": 3,
    "astrophysical journal letters": 3,
    "mnras": 3,
    "monthly notices of the royal astronomical society": 3,
    "a&a": 2,
    "astronomy & astrophysics": 2,
    "astronomy and astrophysics": 2,
    "aj": 2,
    "astronomical journal": 2,
    "physical review d": 2,
    "pasp": 1,
    "publications of the astronomical society of the pacific": 1,
    "jkaa": 1,
    "jkas": 1,
    "journal of the korean astronomical society": 1,
    "research in astronomy and astrophysics": 1,
}

_PREPRINT_SERVERS = frozenset(
    {
        "arxiv",
        "biorxiv",
        "medrxiv",
        "ssrn",
        "research square",
        "preprints.org",
        "hal",
    }
)

_NUMBER = re.compile(r"\d")
_WORD = re.compile(r"[A-Za-z\u00c0-\u024f]+")
_SENTENCE = re.compile(r"[.!?]+\s")


@dataclass(slots=True, frozen=True)
class FeatureSpec:
    """One model input, with the reason it can be absent."""

    name: str
    kind: str
    requires_full_text: bool
    description: str
    bias_route: str = ""
    absent_reason: MissingReason = MissingReason.NOT_REPORTED


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("year", "numeric", False, "Publication year", "time"),
    FeatureSpec(
        "age_years",
        "numeric",
        False,
        "Years between publication and the reference year",
        "time_censoring",
    ),
    FeatureSpec("author_count", "numeric", False, "Number of listed authors", "author"),
    FeatureSpec(
        "institution_count",
        "numeric",
        False,
        "Distinct institutions among authors",
        "affiliation",
    ),
    FeatureSpec(
        "country_count",
        "numeric",
        False,
        "Distinct countries among authors",
        "geography",
    ),
    FeatureSpec(
        "international_collaboration",
        "boolean",
        False,
        "More than one country on the author list",
        "geography",
    ),
    FeatureSpec(
        "first_author_has_institution",
        "boolean",
        False,
        "Whether the first author's institution is known",
        "affiliation",
    ),
    FeatureSpec(
        "corresponding_known",
        "boolean",
        False,
        "Whether a corresponding author is marked",
        "affiliation",
    ),
    FeatureSpec(
        "venue_open_access",
        "boolean",
        False,
        "Whether the venue is flagged open access",
        "open_access",
    ),
    FeatureSpec(
        "venue_tier",
        "numeric",
        False,
        "Venue tier from a documented journal table",
        "venue_prestige",
        MissingReason.NOT_INDEXED,
    ),
    FeatureSpec(
        "venue_has_issn",
        "boolean",
        False,
        "Whether the venue carries an ISSN",
        "venue_prestige",
    ),
    FeatureSpec(
        "is_preprint_server",
        "boolean",
        False,
        "Whether the venue is a preprint server",
        "open_access",
    ),
    FeatureSpec(
        "abstract_length", "numeric", False, "Abstract length in characters", "text"
    ),
    FeatureSpec(
        "abstract_sentence_count",
        "numeric",
        False,
        "Sentence count in the abstract",
        "text",
    ),
    FeatureSpec(
        "abstract_has_numeric",
        "boolean",
        False,
        "Whether the abstract reports numbers",
        "text",
    ),
    FeatureSpec("title_length", "numeric", False, "Title length in characters", "text"),
    FeatureSpec("keyword_count", "numeric", False, "Number of topic keywords", "topic"),
    FeatureSpec(
        "reference_count",
        "numeric",
        False,
        "Number of references",
        "reference",
        MissingReason.NOT_INDEXED,
    ),
    FeatureSpec(
        "references_with_doi_share",
        "numeric",
        False,
        "Share of references that carry a DOI",
        "reference",
        MissingReason.NOT_INDEXED,
    ),
    FeatureSpec(
        "references_with_year_share",
        "numeric",
        False,
        "Share of references with a parsable year",
        "reference",
        MissingReason.NOT_INDEXED,
    ),
    FeatureSpec(
        "page_count",
        "numeric",
        False,
        "Page count of the full text",
        "open_access",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "section_count",
        "numeric",
        True,
        "Number of recognised sections",
        "open_access",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "methods_length",
        "numeric",
        True,
        "Methods section length in characters",
        "text",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "methods_numeric_density",
        "numeric",
        True,
        "Digits per character in the methods section",
        "text",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "body_word_count",
        "numeric",
        True,
        "Words of body text available",
        "text",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "has_data_statement",
        "boolean",
        True,
        "Whether the paper makes a data availability statement",
        "open_data",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "has_code_statement",
        "boolean",
        True,
        "Whether the paper links analysis code",
        "open_data",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
    FeatureSpec(
        "data_record_count",
        "numeric",
        True,
        "Number of open data or code records found",
        "open_data",
        MissingReason.FULL_TEXT_UNAVAILABLE,
    ),
)

_BY_NAME: Mapping[str, FeatureSpec] = {spec.name: spec for spec in FEATURES}


def feature_names(*, include_text_level: bool = True) -> tuple[str, ...]:
    """Model column order, optionally without the full-text-only features."""
    return tuple(
        spec.name
        for spec in FEATURES
        if include_text_level or not spec.requires_full_text
    )


def venue_tier(venue: VenueRecord) -> int | None:
    """Look the venue up in the documented tier table."""
    name = (venue.name or "").strip().lower()
    if not name:
        return None
    if name in VENUE_TIERS:
        return VENUE_TIERS[name]
    # "The Astrophysical Journal" and "ApJ" must agree; also accept a leading
    # article or a trailing volume marker.
    cleaned = re.sub(r"^(the|a|an)\s+", "", name)
    return VENUE_TIERS.get(cleaned)


def _is_preprint_server(venue: VenueRecord) -> bool:
    name = (venue.name or "").strip().lower()
    if name in _PREPRINT_SERVERS:
        return True
    return (venue.venue_type or "").strip().lower() in {"repository", "preprint"}


@dataclass(slots=True)
class FeatureRow:
    """One paper's feature values, with the reason for every absence."""

    paper_id: str
    values: Mapping[str, float] = field(default_factory=dict)
    missing: Mapping[str, MissingReason] = field(default_factory=dict)
    has_full_text: bool = False

    def get(self, name: str, default: float = math.nan) -> float:
        """Value for one column, ``NaN`` when it could not be computed."""
        return float(self.values.get(name, default))

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "paper_id": self.paper_id,
            "values": {key: _finite(value) for key, value in self.values.items()},
            "missing": {key: value.value for key, value in self.missing.items()},
            "has_full_text": self.has_full_text,
        }


def _finite(value: float) -> float | None:
    """JSON has no NaN literal; absence is written as null."""
    return None if value is None or math.isnan(value) else float(value)


def feature_row(paper: PaperRecord, *, reference_year: int) -> FeatureRow:
    """Compute every feature for one paper.

    Absent values are recorded with a typed reason taken from the feature
    specification, so "we could not read the full text" never looks like "the
    paper reported zero".
    """
    values: dict[str, float] = {}
    missing: dict[str, MissingReason] = {}

    def put(
        name: str, value: float | None, reason: MissingReason | None = None
    ) -> None:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            spec = _BY_NAME[name]
            missing[name] = reason or spec.absent_reason
            values[name] = math.nan
            return
        values[name] = float(value)

    institutions = {
        author.institution for author in paper.authors if author.institution
    }
    countries = {author.country for author in paper.authors if author.country}
    references = paper.references
    with_doi = sum(1 for entry in references if entry.doi)
    with_year = sum(1 for entry in references if entry.year)

    put("year", paper.year)
    put(
        "age_years",
        (reference_year - paper.year) if paper.year else None,
        MissingReason.NOT_REPORTED,
    )
    put("author_count", paper.author_count if paper.authors else None)
    put("institution_count", len(institutions) if paper.authors else None)
    put("country_count", len(countries) if paper.authors else None)
    put(
        "international_collaboration",
        float(len(countries) > 1) if countries else None,
    )
    put(
        "first_author_has_institution",
        float(bool(paper.authors and paper.authors[0].institution))
        if paper.authors
        else None,
    )
    put(
        "corresponding_known",
        float(any(author.is_corresponding for author in paper.authors))
        if paper.authors
        else None,
    )
    put(
        "venue_open_access",
        float(bool(paper.venue.is_open_access)) if paper.venue.name else None,
    )
    put("venue_tier", venue_tier(paper.venue))
    put("venue_has_issn", float(bool(paper.venue.issn)) if paper.venue.name else None)
    put("is_preprint_server", float(_is_preprint_server(paper.venue)))

    abstract = paper.abstract or ""
    put("abstract_length", len(abstract) if abstract else None)
    put(
        "abstract_sentence_count",
        len(_SENTENCE.findall(abstract)) + 1 if abstract else None,
    )
    put(
        "abstract_has_numeric",
        float(bool(_NUMBER.search(abstract))) if abstract else None,
    )
    put("title_length", len(paper.title) if paper.title else None)
    put("keyword_count", len(paper.keywords) if paper.keywords else 0)

    put("reference_count", len(references) if references else None)
    put(
        "references_with_doi_share",
        (with_doi / len(references)) if references else None,
    )
    put(
        "references_with_year_share",
        (with_year / len(references)) if references else None,
    )
    put(
        "page_count",
        float(paper.fulltext_pages) if paper.fulltext_pages else None,
        MissingReason.FULL_TEXT_UNAVAILABLE,
    )

    sections = paper.sections or {}
    methods = sections.get("methods", "") or ""
    body = "\n".join(str(value) for value in sections.values())
    data_records = paper.open_data
    put(
        "section_count",
        len([name for name, text in sections.items() if text]) if sections else None,
    )
    put("methods_length", len(methods) if methods else None)
    put(
        "methods_numeric_density",
        (len(_NUMBER.findall(methods)) / len(methods)) if methods else None,
    )
    put("body_word_count", len(_WORD.findall(body)) if body else None)
    put(
        "has_data_statement",
        float(bool(sections.get("data_availability"))) if sections else None,
    )
    put(
        "has_code_statement",
        float(any(record.repository.lower() == "github" for record in data_records))
        if sections
        else None,
    )
    put("data_record_count", float(len(data_records)) if sections else None)

    return FeatureRow(
        paper_id=paper.paper_id,
        values=values,
        missing=missing,
        has_full_text=paper.has_full_text,
    )


@dataclass(slots=True)
class FeatureMatrix:
    """A model-ready matrix whose holes are visible rather than filled."""

    names: tuple[str, ...]
    paper_ids: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    missing_counts: Mapping[str, int]
    full_text_level: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.rows)

    def column(self, name: str) -> tuple[float, ...]:
        """One column of the matrix."""
        index = self.names.index(name)
        return tuple(row[index] for row in self.rows)

    def missing_share(self, name: str) -> float:
        """Share of rows where this feature is absent."""
        if not self.rows:
            return 0.0
        return self.missing_counts.get(name, 0) / len(self.rows)

    def absent_rows(self) -> dict[str, int]:
        """Alias used by the report table."""
        return dict(self.missing_counts)

    def text_level_missing_share(self) -> float:
        """Share of text-level cells that are absent."""
        if not self.rows or not self.full_text_level:
            return 0.0
        total = len(self.rows) * len(self.full_text_level)
        absent = sum(self.missing_counts.get(name, 0) for name in self.full_text_level)
        return absent / total

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON summary; the rows themselves stay in memory."""
        return {
            "schema": FEATURE_SCHEMA,
            "columns": list(self.names),
            "records": len(self.rows),
            "missing_counts": dict(self.missing_counts),
            "text_level_columns": list(self.full_text_level),
            "text_level_missing_share": self.text_level_missing_share(),
        }


def feature_matrix(
    papers: Sequence[PaperRecord],
    *,
    reference_year: int,
    include_text_level: bool = True,
) -> FeatureMatrix:
    """Build the matrix, one row per paper, in a stable column order."""
    names = feature_names(include_text_level=include_text_level)
    full_text_level = tuple(
        spec.name for spec in FEATURES if spec.requires_full_text and spec.name in names
    )
    rows: list[tuple[float, ...]] = []
    counts: dict[str, int] = dict.fromkeys(names, 0)
    paper_ids: list[str] = []
    for paper in papers:
        row = feature_row(paper, reference_year=reference_year)
        paper_ids.append(row.paper_id)
        rows.append(tuple(row.get(name) for name in names))
        for name in row.missing:
            if name in counts:
                counts[name] += 1
    return FeatureMatrix(
        names=names,
        paper_ids=tuple(paper_ids),
        rows=tuple(rows),
        missing_counts=counts,
        full_text_level=full_text_level,
    )


@dataclass(slots=True)
class TargetSet:
    """Targets on a fixed horizon, with censoring visible."""

    values: tuple[float, ...]
    raw: tuple[int, ...]
    available: tuple[bool, ...]
    censored: tuple[bool, ...]
    horizon: int
    reference_year: int
    missing_reason: MissingReason | None = None

    def __len__(self) -> int:
        return len(self.values)

    @property
    def usable(self) -> tuple[int, ...]:
        """Row indices that carry a label."""
        return tuple(index for index, flag in enumerate(self.available) if flag)

    def summary(self) -> dict[str, Any]:
        """Counts for the report header."""
        return {
            "horizon_years": self.horizon,
            "reference_year": self.reference_year,
            "papers": len(self.values),
            "labelled": len(self.usable),
            "censored": sum(1 for flag in self.censored if flag),
            "missing_reason": self.missing_reason.value
            if self.missing_reason
            else None,
        }


def targets(
    papers: Iterable[PaperRecord],
    *,
    reference_year: int,
    horizon: int = HORIZON_YEARS,
) -> TargetSet:
    """Citations inside ``horizon`` years, ``log1p``-scaled for the model.

    Right-censored papers keep their (partial) count in ``raw`` for reporting but
    are marked unavailable for training: fitting on a three-year window that has
    not elapsed yet teaches the model that recent papers are unpopular.
    """
    values: list[float] = []
    raw: list[int] = []
    available: list[bool] = []
    censored: list[bool] = []
    for paper in papers:
        count = citations_within(paper, horizon=horizon)
        right_censored = is_right_censored(
            paper, reference_year=reference_year, horizon=horizon
        )
        known = count is not None
        raw.append(int(count or 0))
        values.append(math.log1p(count) if known else math.nan)
        censored.append(right_censored)
        available.append(known and not right_censored)
    return TargetSet(
        values=tuple(values),
        raw=tuple(raw),
        available=tuple(available),
        censored=tuple(censored),
        horizon=horizon,
        reference_year=reference_year,
        missing_reason=MissingReason.NOT_INDEXED,
    )
