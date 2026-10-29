"""A cohort is a frozen set of papers plus the record of how it was chosen.

Two rules make the rest of the pipeline auditable:

* the snapshot is hashed, so a model trained on it can be re-derived exactly;
* ``source`` says whether the cohort is real or synthetic.  A synthetic cohort
  exercises the machinery; it is never evidence about real astronomy, and the
  report repeats that in every table caption.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .contracts import (
    AccessRoute,
    AuthorRecord,
    OpenDataKind,
    OpenDataRecord,
    PaperRecord,
    ReferenceRecord,
    VenueRecord,
    content_hash,
    normalise_doi,
)

__all__ = [
    "COHORT_SCHEMA",
    "HORIZON_YEARS",
    "CohortSnapshot",
    "build_snapshot",
    "citations_within",
    "is_right_censored",
    "openalex_snapshot",
    "select_cohort",
    "synthetic_cohort",
]

COHORT_SCHEMA = "jw.research-influence.cohort/v1"

#: Citation window used as the prediction target.  A three-year window keeps the
#: target available for recent papers and stops the model from scoring long,
#: slowly accumulating citation histories against short ones.
HORIZON_YEARS = 3


@dataclass(slots=True)
class CohortSnapshot:
    """Frozen evidence set plus the filters that produced it."""

    cohort_id: str
    papers: tuple[PaperRecord, ...] = ()
    query: str = ""
    source: str = "unknown"
    created_at: str = ""
    reference_year: int = 0
    filters: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        if not self.reference_year:
            self.reference_year = datetime.now(UTC).year

    @property
    def is_synthetic(self) -> bool:
        """Whether the cohort was generated rather than observed."""
        return self.source.startswith("synthetic")

    def __len__(self) -> int:
        return len(self.papers)

    def fingerprint(self) -> str:
        """Hash over the papers and the selection record."""
        return content_hash(
            {
                "schema": COHORT_SCHEMA,
                "cohort_id": self.cohort_id,
                "query": self.query,
                "source": self.source,
                "reference_year": self.reference_year,
                "filters": dict(self.filters),
                "papers": [paper.fingerprint() for paper in self.papers],
            }
        )

    def summary(self) -> dict[str, Any]:
        """Counts that belong in every report header."""
        with_year = sum(1 for paper in self.papers if paper.year)
        with_abstract = sum(1 for paper in self.papers if paper.abstract)
        with_full_text = sum(1 for paper in self.papers if paper.has_full_text)
        with_references = sum(1 for paper in self.papers if paper.references)
        with_data = sum(1 for paper in self.papers if paper.open_data)
        years = sorted(paper.year for paper in self.papers if paper.year)
        return {
            "papers": len(self.papers),
            "source": self.source,
            "synthetic": self.is_synthetic,
            "query": self.query,
            "reference_year": self.reference_year,
            "year_min": years[0] if years else None,
            "year_max": years[-1] if years else None,
            "with_year": with_year,
            "with_abstract": with_abstract,
            "with_full_text": with_full_text,
            "with_references": with_references,
            "with_open_data": with_data,
            "fingerprint": self.fingerprint(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "schema": COHORT_SCHEMA,
            "cohort_id": self.cohort_id,
            "query": self.query,
            "source": self.source,
            "created_at": self.created_at,
            "reference_year": self.reference_year,
            "filters": dict(self.filters),
            "notes": list(self.notes),
            "papers": [paper.to_dict() for paper in self.papers],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CohortSnapshot:
        """Rebuild a snapshot from :meth:`to_dict` output."""
        created = str(raw.get("created_at") or "")
        reference_year = raw.get("reference_year")
        return cls(
            cohort_id=str(raw.get("cohort_id") or ""),
            papers=tuple(
                PaperRecord.from_dict(item) for item in raw.get("papers") or ()
            ),
            query=str(raw.get("query") or ""),
            source=str(raw.get("source") or "unknown"),
            created_at=created,
            reference_year=int(reference_year)
            if isinstance(reference_year, int)
            else 0,
            filters=dict(raw.get("filters") or {}),
            notes=tuple(str(item) for item in raw.get("notes") or ()),
        )

    def write(self, path: str | Path) -> str:
        """Write the snapshot as UTF-8 JSON and return the path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return str(target)

    @classmethod
    def load(cls, path: str | Path) -> CohortSnapshot:
        """Read a snapshot written by :meth:`write`."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)


def build_snapshot(
    papers: Iterable[PaperRecord],
    *,
    cohort_id: str,
    query: str = "",
    source: str = "unknown",
    reference_year: int = 0,
    filters: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> CohortSnapshot:
    """Freeze an evidence set.

    Papers are sorted by identifier so that the same papers in a different order
    hash identically — a snapshot is a set, not a sequence.
    """
    ordered = tuple(sorted(papers, key=lambda paper: paper.paper_id))
    return CohortSnapshot(
        cohort_id=cohort_id,
        papers=ordered,
        query=query,
        source=source,
        reference_year=reference_year,
        filters=dict(filters or {}),
        notes=tuple(str(note) for note in notes),
    )


def select_cohort(
    papers: Iterable[PaperRecord],
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    require_doi: bool = False,
    require_abstract: bool = False,
) -> tuple[PaperRecord, ...]:
    """Apply the documented inclusion filters.

    A paper without a year cannot be placed on a citation horizon, and a paper
    without an abstract has no text-level feature; neither is dropped silently —
    the caller decides and the filters are recorded on the snapshot.
    """
    kept: list[PaperRecord] = []
    for paper in papers:
        if min_year is not None and (paper.year or 0) < min_year:
            continue
        if max_year is not None and (paper.year or 0) > max_year:
            continue
        if require_doi and not paper.doi:
            continue
        if require_abstract and not paper.abstract:
            continue
        kept.append(paper)
    return tuple(kept)


def citations_within(paper: PaperRecord, *, horizon: int = HORIZON_YEARS) -> int | None:
    """Citations accrued inside the first ``horizon`` years after publication.

    Returns ``None`` when the per-year history is unavailable, rather than
    substituting the lifetime count — that substitution is exactly the leakage
    the horizon exists to prevent.
    """
    if not paper.year or not paper.counts_by_year:
        return None
    first = paper.year
    last = paper.year + horizon - 1
    return sum(count for year, count in paper.counts_by_year if first <= year <= last)


def is_right_censored(
    paper: PaperRecord,
    *,
    reference_year: int,
    horizon: int = HORIZON_YEARS,
) -> bool:
    """Whether the observation window has not closed yet for this paper."""
    if not paper.year:
        return True
    return paper.year + horizon > reference_year


# --- synthetic cohorts -------------------------------------------------------

#: Venue table for the synthetic generator: (name, publisher, tier, open access).
_SYNTHETIC_VENUES: tuple[tuple[str, str, int, bool], ...] = (
    ("Nature Astronomy", "Springer Nature", 4, False),
    ("Nature", "Springer Nature", 4, False),
    ("ApJ", "American Astronomical Society", 3, True),
    ("MNRAS", "Oxford University Press", 3, False),
    ("A&A", "EDP Sciences", 2, True),
    ("AJ", "American Astronomical Society", 2, True),
    ("ApJL", "American Astronomical Society", 3, True),
    ("PASP", "IOP Publishing", 1, False),
    ("JKAS", "Korean Astronomical Society", 1, True),
)

_SYNTHETIC_TOPICS: tuple[tuple[str, float], ...] = (
    ("supernovae", 0.9),
    ("exoplanets", 1.0),
    ("cosmology", 0.85),
    ("galaxy evolution", 0.7),
    ("gravitational waves", 0.95),
    ("stellar astrophysics", 0.45),
    ("instrumentation", 0.3),
)

_SYNTHETIC_COUNTRIES: tuple[str, ...] = (
    "US",
    "GB",
    "DE",
    "FR",
    "IT",
    "NL",
    "CA",
    "CN",
    "JP",
    "IN",
    "BR",
    "CL",
    "ZA",
)

#: Countries that receive a designed citation bonus in the synthetic cohort.
#: The generator plants this effect on purpose so the audit has a known answer.
_SYNTHETIC_ADVANTAGED = frozenset({"US", "GB", "DE", "FR", "NL", "CA"})

_SYNTHETIC_INSTITUTIONS: Mapping[str, tuple[str, ...]] = {
    "US": ("Harvard CfA", "Caltech", "UC Berkeley", "STScI"),
    "GB": ("University of Cambridge", "University of Oxford", "UCL"),
    "DE": ("MPA Garching", "MPE Garching", "ESO"),
    "FR": ("IAP Paris", "CEA Saclay"),
    "IT": ("INAF Trieste", "INAF Bologna"),
    "NL": ("Leiden Observatory", "ASTRON"),
    "CA": ("University of Toronto", "CITA"),
    "CN": ("NAOC", "SHAO"),
    "JP": ("University of Tokyo", "NAOJ"),
    "IN": ("IUCAA", "TIFR"),
    "BR": ("Universidade de Sao Paulo",),
    "CL": ("Universidad de Chile",),
    "ZA": ("SAAO", "University of Cape Town"),
}

_SYNTHETIC_SURVEYS: tuple[str, ...] = (
    "the Zwicky Transient Facility",
    "the Dark Energy Survey",
    "Gaia DR3",
    "the Transiting Exoplanet Survey Satellite",
    "the Very Large Telescope",
)

_METHODS_TEXT = (
    "2. Methods\n"
    "We selected {sample} targets from {survey} and reduced the data with "
    "standard pipelines. Photometry was measured in {bands} bands with a "
    "typical uncertainty of {error} mag. We discarded {rejected} epochs that "
    "failed the quality cuts and binned the remainder into {bins} bins.\n"
)

_DATA_TEXT = (
    "Data availability\n"
    "The reduced data products are available at {url}. "
    "Analysis code is available at https://github.com/example/{slug}.\n"
)


def _synthetic_title(rng: random.Random, topic: str) -> str:
    pattern = rng.choice(
        (
            "A survey of {topic} in {survey}",
            "{topic} from the {survey}: first results",
            "Constraining {topic} with {survey}",
            "The {topic} sample of {survey}",
        )
    )
    survey = rng.choice(_SYNTHETIC_SURVEYS).replace("the ", "")
    return pattern.format(topic=topic, survey=survey)


def _synthetic_abstract(rng: random.Random, topic: str, *, quality: float) -> str:
    """Abstract whose length tracks quality, as a real abstract often does."""
    sentences = 3 + max(0, min(4, round(1.0 + 0.9 * quality)))
    body = [
        f"We present a study of {topic} using {rng.randint(12, 240)} objects.",
        "Our sample spans a wide range in redshift and stellar mass.",
        "We find a correlation between the observables that is significant at "
        f"the {rng.randint(2, 6)} sigma level.",
        "We discuss its implications for models of the population.",
        "Systematic checks on the selection function are presented in the appendix.",
        "The measurement is consistent with a simple power-law description.",
        "The results are made publicly available with the reduced data products.",
    ]
    return " ".join(body[:sentences])


def _synthetic_authors(
    rng: random.Random, *, privileged: bool, quality: float
) -> tuple[tuple[AuthorRecord, ...], str]:
    """Author list whose size tracks the paper's latent quality.

    A well-resourced study has more authors and more institutions; that is the
    observable proxy that lets a model learn anything about quality at all.
    """
    size = max(1, round(rng.lognormvariate(1.05 + 0.30 * quality, 0.45)))
    size = min(size, 40)
    if privileged:
        first_pool = ("US", "GB", "DE", "FR", "NL", "CA")
        rest_pool = ("US", "GB", "DE", "FR", "NL", "CA", "IT", "JP", "CL", "ZA")
        rest_weights = (26, 13, 11, 9, 7, 6, 8, 8, 6, 6)
    else:
        first_pool = ("CN", "IN", "BR", "ZA", "CL", "JP")
        rest_pool = ("CN", "IN", "BR", "ZA", "CL", "JP", "IT", "US", "GB")
        rest_weights = (24, 20, 12, 8, 8, 10, 8, 6, 4)
    first_country = rng.choice(first_pool)
    collaborators = rng.choices((1, 2, 3, 4), weights=(30, 34, 24, 12))[0]
    countries = [first_country]
    while len(countries) < collaborators:
        candidate = rng.choices(rest_pool, weights=rest_weights)[0]
        if candidate not in countries:
            countries.append(candidate)
    authors: list[AuthorRecord] = []
    for index in range(size):
        if index == 0:
            country = first_country
        else:
            country = (
                rng.choices(rest_pool, weights=rest_weights)[0]
                if rng.random() < 0.7
                else countries[index % len(countries)]
            )
        institution = (
            rng.choice(_SYNTHETIC_INSTITUTIONS[country]) if rng.random() < 0.88 else ""
        )
        authors.append(
            AuthorRecord(
                name=f"Synthetic Author {index + 1}",
                given="Synthetic",
                family=f"Author{index + 1}",
                position=index + 1,
                orcid="",
                institution=institution,
                country=country,
                is_corresponding=index == 0,
            )
        )
    return tuple(authors), first_country


def synthetic_cohort(
    *,
    count: int = 200,
    seed: int = 11,
    reference_year: int = 0,
    cohort_id: str = "cohort:synthetic",
) -> CohortSnapshot:
    """Generate a cohort that contains known effects and known gaps.

    The generative model is explicit so the audit can be scored against truth:

    * a latent ``quality`` drives venue tier, author count, reference count,
      abstract length and the chance of a data statement — that is why a model
      can predict anything at all;
    * citation accrual grows with ``quality``, with topic heat, with venue tier
      and with ``advantage``, a binary indicator for six wealthy countries that
      is **planted on purpose** so the audit has a known answer;
    * full-text availability depends on venue openness and publication year, so
      text-level features are missing-not-at-random.

    It is nevertheless synthetic.  Every table that quotes it says so.
    """
    rng = random.Random(seed)
    reference = reference_year or datetime.now(UTC).year
    papers: list[PaperRecord] = []
    for index in range(count):
        topic, heat = rng.choices(
            _SYNTHETIC_TOPICS, weights=[item[1] for item in _SYNTHETIC_TOPICS]
        )[0]
        quality = rng.gauss(0.0, 1.0)
        tier, venue_name, publisher, is_oa = _synthetic_venue(rng, quality=quality)
        year = reference - rng.choice((0, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 15))
        privileged = rng.random() < 0.55
        authors, first_country = _synthetic_authors(
            rng, privileged=privileged, quality=quality
        )
        advantage = 0.35 if privileged else 0.0

        log_mean = 0.6 + 1.15 * quality + 0.45 * heat + 0.30 * (tier / 4.0) + advantage
        horizon_total = max(0, round(3.0 * rng.lognormvariate(log_mean, 0.35)))
        counts_by_year = _spread_horizon(rng, year=year, total=horizon_total)
        years_elapsed = max(0, reference - year - 3)
        lifetime = horizon_total + round(
            horizon_total * 0.45 * rng.random() * min(1.0, years_elapsed / 6.0)
        )

        route = AccessRoute.METADATA_ONLY
        sections: dict[str, str] = {}
        open_data: tuple[OpenDataRecord, ...] = ()
        open_full = rng.random() < (
            0.20 + 0.45 * float(is_oa) + 0.20 * float(year >= 2015)
        )
        if open_full:
            route = AccessRoute.OPEN_ACCESS_GOLD if is_oa else AccessRoute.PREPRINT
            sections = {
                "methods": _synthetic_methods(rng, quality=quality),
                "conclusions": "5. Conclusions\nWe summarise the results.",
            }
            if rng.random() < _sigmoid(0.9 * quality - 0.4):
                sections["data_availability"] = _synthetic_data_text(rng, index)
                open_data = _synthetic_open_data(rng, index)

        reference_count = max(
            3,
            round(rng.gauss(34.0, 7.0) * float(2.718281828459045 ** (0.22 * quality))),
        )
        references = tuple(
            ReferenceRecord(
                index=position + 1,
                raw=f"Synthetic ref {position + 1}",
                doi=f"10.9999/synthetic.{index}.{position + 1}"
                if rng.random() < 0.7
                else "",
                year=year - rng.randint(1, 12),
            )
            for position in range(reference_count)
        )
        keywords = (
            topic,
            *(
                other
                for other, _ in rng.sample(_SYNTHETIC_TOPICS, rng.randint(0, 3))
                if other != topic
            ),
        )

        papers.append(
            PaperRecord(
                paper_id=f"paper:synthetic-{index:04d}",
                title=_synthetic_title(rng, topic),
                abstract=_synthetic_abstract(rng, topic, quality=quality),
                year=year,
                doi=f"10.9999/synthetic.{index:04d}",
                language="en" if rng.random() < 0.93 else "zh",
                venue=VenueRecord(
                    name=venue_name,
                    venue_type="journal",
                    publisher=publisher,
                    issn=f"0000-{index:04d}" if rng.random() < 0.9 else "",
                    is_open_access=is_oa,
                ),
                authors=authors,
                references=references,
                open_data=open_data,
                sections=sections,
                keywords=keywords,
                cited_by_count=lifetime,
                reference_count=len(references),
                counts_by_year=counts_by_year,
                access_route=route,
                work_id=f"W{index:09d}",
                notes=("synthetic", f"first_author_country:{first_country}"),
            )
        )
    return build_snapshot(
        papers,
        cohort_id=cohort_id,
        query="synthetic astrophysics cohort",
        source="synthetic:designed-v2",
        reference_year=reference,
        filters={"generator": "synthetic_cohort", "seed": seed, "requested": count},
        notes=(
            "Generated to exercise the pipeline; not evidence about real astronomy.",
            "Planted effect: +0.35 log-citations when the first author is in one of "
            "six privileged countries.",
            "Quality is latent and only partly observable, through venue tier, "
            "author count, reference count and data statements.",
            "Full text is missing-not-at-random (depends on venue openness and year).",
        ),
    )


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + 2.718281828459045 ** (-max(-30.0, min(30.0, value))))


def _synthetic_venue(
    rng: random.Random, *, quality: float
) -> tuple[int, str, str, bool]:
    """Pick a venue whose tier rises with the paper's latent quality."""
    if quality > 1.0:
        weights = (3.0, 4.0, 2.0, 1.0)
    elif quality < -0.5:
        weights = (1.0, 3.0, 3.0, 3.0)
    else:
        weights = (1.5, 3.0, 3.0, 2.5)
    wanted = rng.choices((4, 3, 2, 1), weights=weights)[0]
    options = [venue for venue in _SYNTHETIC_VENUES if venue[2] == wanted]
    if not options:
        options = list(_SYNTHETIC_VENUES)
        wanted = options[0][2]
    name, publisher, _tier, is_oa = rng.choice(options)
    return wanted, name, publisher, is_oa


def _spread_horizon(
    rng: random.Random, *, year: int, total: int
) -> tuple[tuple[int, int], ...]:
    if total <= 0:
        return ()
    first = max(1, round(total * 0.45))
    second = max(0, round(total * 0.33))
    third = max(0, total - first - second)
    return ((year, first), (year + 1, second), (year + 2, third))


def _synthetic_methods(rng: random.Random, *, quality: float) -> str:
    return _METHODS_TEXT.format(
        sample=rng.randint(20, 400),
        survey=rng.choice(_SYNTHETIC_SURVEYS),
        bands=rng.choice((2, 3, 5, 7)),
        error=round(rng.uniform(0.01, 0.2), 3),
        rejected=rng.randint(0, 40),
        bins=rng.choice((5, 10, 20)),
    ) + (
        "We also performed an extensive set of systematic checks, including "
        f"{rng.randint(3, 12)} independent validation samples, and we quantify "
        "the impact of each on the final measurement.\n"
        if quality > 0.4
        else ""
    )


def _synthetic_data_text(rng: random.Random, index: int) -> str:
    repository = rng.choice(("zenodo", "figshare", "dryad", "mast"))
    accession = rng.randint(100000, 999999)
    url = {
        "zenodo": f"https://zenodo.org/record/{accession}",
        "figshare": f"https://figshare.com/articles/{accession}",
        "dryad": f"https://datadryad.org/stash/dataset/doi%3A10.5061%2Fdryad.{accession}",
        "mast": f"https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?id={accession}",
    }[repository]
    return _DATA_TEXT.format(url=url, slug=f"synthetic-{index:04d}")


def _synthetic_open_data(rng: random.Random, index: int) -> tuple[OpenDataRecord, ...]:
    text = _synthetic_data_text(rng, index)
    url = text.split("available at ")[1].split(".")[0]
    records = [
        OpenDataRecord(
            kind=OpenDataKind.DATA,
            url=url,
            repository=_repository_of(url),
            accession=url.rstrip("/").split("/")[-1],
            statement="The reduced data products are available at " + url + ".",
        ),
        OpenDataRecord(
            kind=OpenDataKind.CODE,
            url=f"https://github.com/example/synthetic-{index:04d}",
            repository="GitHub",
            accession=f"synthetic-{index:04d}",
            statement="Analysis code is available.",
        ),
    ]
    return tuple(records)


def _repository_of(url: str) -> str:
    for name in ("Zenodo", "Figshare", "Dryad", "MAST"):
        if name.lower() in url.lower():
            return name
    return "other"


# --- snapshots from stored provider payloads ---------------------------------


def openalex_snapshot(
    payloads: Iterable[Mapping[str, Any]],
    *,
    cohort_id: str,
    query: str = "",
    reference_year: int = 0,
    filters: Mapping[str, Any] | None = None,
) -> CohortSnapshot:
    """Build a cohort from stored OpenAlex ``/works`` payloads.

    Kept separate from the network layer on purpose: a snapshot is taken once,
    committed to disk, and re-used, so a model run never depends on a live API.
    """
    from .paper import build_paper

    papers = [
        build_paper(
            paper_id=f"paper:{_identifier(payload)}",
            doi=str(payload.get("doi") or ""),
            openalex=dict(payload.get("parsed") or payload),
        )
        for payload in payloads
    ]
    return build_snapshot(
        papers,
        cohort_id=cohort_id,
        query=query,
        source="openalex:works",
        reference_year=reference_year,
        filters=dict(filters or {}),
    )


def _identifier(payload: Mapping[str, Any]) -> str:
    doi = normalise_doi(str(payload.get("doi") or ""))
    if doi:
        return doi.replace("/", "_")
    work_id = str(payload.get("id") or payload.get("work_id") or "")
    return work_id.rsplit("/", 1)[-1] or "unknown"
