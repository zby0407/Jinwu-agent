"""Best-effort metadata recovery from an uploaded PDF's text layer.

The competition scenario is "a user uploads a paper, the application works out
what it is".  A text layer is a weak source: it has the title on line one, an
author block whose line breaks are not meaningful, an affiliation that looks
exactly like an author when it is a single institution name, and no structural
markup at all.  So this module is deliberately narrow about what it claims:

* every field it returns is a *candidate* with a confidence band and a note
  saying which rule fired;
* an author block that cannot be split is returned as one string with
  ``LOW`` confidence rather than being silently chopped into fake surnames;
* a missing journal is ``None`` and the caller records ``NOT_REPORTED`` — a
  preprint genuinely has no journal, and the model must see that difference.

Downstream, :mod:`jw.research_influence.enrich` replaces these guesses with
indexed metadata whenever a provider can identify the paper, and keeps the
guess only as a fallback with lower provenance.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .contracts import ConfidenceBand

__all__ = [
    "PdfMetadata",
    "apply_pdf_metadata",
    "extract_pdf_metadata",
    "looks_like_author_line",
    "title_from_pages",
]

#: Lines printed above the title by publishers and preprint servers.  They are
#: skipped, not treated as "this page has no title": the title is what follows.
_STAMPS: tuple[str, ...] = (
    "arxiv:",
    "doi:",
    "preprint",
    "draft version",
    "submitted to",
    "in press",
    "accepted for publication",
    "received:",
    "published:",
)

#: Lines that mean the title is *behind* us.  Guessing past them would put body
#: text in the title column.
_STOP_LEADS: tuple[str, ...] = (
    "abstract",
    "summary",
    "keywords",
    "key words",
    "introduction",
)

_AFFILIATION_TOKENS: tuple[str, ...] = (
    "university",
    "universit",
    "institute",
    "instituto",
    "institut",
    "department",
    "departamento",
    "centre",
    "center",
    "laboratory",
    "observatory",
    "college",
    "school of",
    "faculty",
    "academy",
    "research council",
    "inc.",
    "ltd",
    "gmbh",
    "cnrs",
    "esrf",
)

_JOURNAL_TOKENS: tuple[str, ...] = (
    "astrophysical journal",
    "astronomical journal",
    "monthly notices",
    "nature",
    "science",
    "physical review",
    "journal of",
    "proceedings of",
    "annals of",
    "astronomy & astrophysics",
    "astronomy and astrophysics",
    "publications of the astronomical society",
    "communications in",
    "letters",
    "review of",
    "bulletin of",
)

#: Initials, optional middle initials, surname.  Kept loose on purpose: the
#: cost of accepting an affiliation as a name is a washed-out author list, and
#: the cost of rejecting a real name is a missing author, so the line is only
#: accepted when it does *not* look like an affiliation and is short.
_NAME_PART = re.compile(r"^[A-Z][A-Za-z'\u00c0-\u024f-]*(?:\s+[A-Z]\.?)*$")
_INITIALS = re.compile(r"^(?:[A-Z]\.\s*)+$")
_MULTI_NAME_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&)\s*")
_SUPERSCRIPTS = re.compile(r"[\d\u00b9\u00b2\u00b3\u2070-\u2079*†‡§¶]+$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;)\]}>\"']+")
_ARXIV_STAMP = re.compile(r"arXiv:\s*(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_ARXIV_BARE = re.compile(r"^arXiv:\s*(?P<id>[\w./-]+)$", re.IGNORECASE)
_YEAR = re.compile(r"\b(19[3-9]\d|20\d\d)\b")
#: Years only count when a date marker says so.  A bare four-digit number on
#: page one is a magnitude, a sample size or a citation year far more often
#: than it is the publication year.
_YEAR_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\u00a9\s*(?:19[3-9]\d|20\d\d)"),
    re.compile(r"copyright\s*(?:19[3-9]\d|20\d\d)", re.IGNORECASE),
    re.compile(
        r"\b(?:received|accepted|published|revised|submitted)[^\n]{0,40}?(19[3-9]\d|20\d\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:19[3-9]\d|20\d\d)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b"
    ),
    re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(?:19[3-9]\d|20\d\d)\b"
    ),
)
_STAMP_DATE = re.compile(
    r"arXiv:\s*\S+\s*(?:\[[^\]]*\])?\s*\d{1,2}\s+[A-Z][a-z]{2}\s+(?P<year>19[3-9]\d|20\d\d)"
)
_MAX_TITLE_LINES = 3
_MAX_TITLE_LENGTH = 300


@dataclass(slots=True)
class PdfMetadata:
    """Candidates recovered from a text layer, each with a confidence band."""

    title: str = ""
    title_lines: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    confidence: ConfidenceBand = ConfidenceBand.UNVERIFIED
    notes: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def usable_doi(self) -> str:
        """The DOI when one was found in the text."""
        return self.doi

    @property
    def arxiv_doi(self) -> str:
        """The DOI a preprint carries by convention, when only an arXiv id is known."""
        return f"10.48550/arXiv.{self.arxiv_id}" if self.arxiv_id else ""

    def to_dict(self) -> dict[str, object]:
        """Plain mapping for manifests."""
        return {
            "title": self.title,
            "title_lines": list(self.title_lines),
            "authors": list(self.authors),
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "confidence": self.confidence.value,
            "notes": list(self.notes),
            "evidence": dict(self.evidence),
        }


def _clean(line: str) -> str:
    return " ".join(line.split())


def _is_stamp(text: str) -> bool:
    lowered = text.casefold()
    return any(lowered.startswith(token) for token in _STAMPS)


def _is_stop(text: str) -> bool:
    lowered = text.casefold().rstrip(".:")
    return lowered in _STOP_LEADS or lowered.startswith(
        tuple(f"{x}." for x in _STOP_LEADS)
    )


def _looks_like_affiliation(text: str) -> bool:
    lowered = text.casefold()
    if _EMAIL.search(text):
        return True
    return any(token in lowered for token in _AFFILIATION_TOKENS)


def _title_shaped(text: str) -> bool:
    """Whether a line could be part of the title (not a name, not a sentence)."""
    if not 3 <= len(text) <= _MAX_TITLE_LENGTH:
        return False
    if text.endswith((".", ":", ";")):
        return False
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    upper = sum(1 for char in letters if char.isupper())
    words = text.split()
    return len(words) >= 2 and (upper / len(letters) >= 0.6 or words[0][0].isupper())


def _looks_like_caps_title(text: str) -> bool:
    """Whether an all-caps line is a wrapped title rather than a name list.

    "SPLITTING METHODS" and "JOHN SMITH, MARY JONES" have the same character
    class.  A wrapped title has no initials and no list separator, which is the
    only cheap difference that does not need a name dictionary.
    """
    words = text.split()
    if len(words) < 2:
        return False
    if "," in text or ";" in text or " and " in text.casefold():
        return False
    if any(_INITIALS.match(word) for word in words):
        return False
    return all(word.isupper() and len(word) >= 3 for word in words)


def looks_like_author_line(text: str) -> bool:
    """Whether a line reads as one or more personal names.

    Affiliations are rejected first: "Centre for Time, University of Sydney"
    splits into two comma-separated pieces that both look like names, which is
    exactly the kind of quiet corruption this guard exists to prevent.
    """
    cleaned = _clean(text)
    if not cleaned or len(cleaned) > 200:
        return False
    if _looks_like_affiliation(cleaned):
        return False
    if _is_stop(cleaned) or _is_stamp(cleaned):
        return False
    if _looks_like_caps_title(cleaned):
        return False
    parts = [part for part in _MULTI_NAME_SPLIT.split(cleaned) if part.strip()]
    if not parts:
        return False
    names = 0
    for part in parts:
        token = _SUPERSCRIPTS.sub("", part).strip().rstrip(",")
        if not token:
            continue
        words = token.split()
        if not 1 < len(words) <= 4:
            return False
        if _INITIALS.match(token):
            continue
        if not all(
            _NAME_PART.match(word.rstrip(".")) or _INITIALS.match(word)
            for word in words
        ):
            return False
        names += 1
    return names >= 1


def _split_authors(text: str) -> tuple[str, ...]:
    cleaned = _clean(text)
    out: list[str] = []
    for part in _MULTI_NAME_SPLIT.split(cleaned):
        token = _SUPERSCRIPTS.sub("", part).strip().rstrip(",")
        token = " ".join(token.split())
        if token and token not in out:
            out.append(token)
    return tuple(out)


def title_from_pages(
    pages: Sequence[str], *, max_lines: int = _MAX_TITLE_LINES
) -> tuple[str, ...]:
    """Title candidates from the top of page one.

    Stamps printed above the title are skipped; a lead-in such as ``Abstract``
    stops the search, because the title is not below it.  A title that wrapped
    over two or three lines is joined, which is why the return value is a tuple.
    """
    if not pages:
        return ()
    collected: list[str] = []
    seen_content = False
    for raw in pages[0].splitlines():
        line = _clean(raw)
        if not line:
            continue
        if _is_stamp(line):
            continue
        if _is_stop(line):
            return tuple(collected)
        if looks_like_author_line(line) and collected:
            return tuple(collected)
        if not _title_shaped(line):
            if collected:
                return tuple(collected)
            seen_content = True
            continue
        collected.append(line)
        seen_content = True
        if len(collected) >= max_lines:
            return tuple(collected)
        if not seen_content:
            return tuple(collected)
    return tuple(collected)


def _authors_from_page_one(
    pages: Sequence[str], title_lines: tuple[str, ...]
) -> tuple[str, ...]:
    """Names printed under the title, stopping at the abstract or affiliation."""
    if not pages:
        return ()
    authors: list[str] = []
    started = False
    for raw in pages[0].splitlines():
        line = _clean(raw)
        if not line:
            continue
        if title_lines and line in title_lines:
            started = True
            continue
        if not started:
            if _title_shaped(line):
                started = True
            continue
        if _is_stop(line) or _is_stamp(line) or line.casefold().startswith("abstract"):
            break
        if _looks_like_affiliation(line):
            if authors:
                break
            continue
        if looks_like_author_line(line):
            for name in _split_authors(line):
                if name not in authors:
                    authors.append(name)
            if len(authors) >= 25:
                break
            continue
        if authors:
            break
    return tuple(authors)


def _venue_from_page_one(pages: Sequence[str]) -> str:
    """A journal name printed on page one, when it is one we recognise."""
    if not pages:
        return ""
    for raw in pages[0].splitlines()[:40]:
        line = _clean(raw)
        if len(line) < 6 or len(line) > 160:
            continue
        lowered = line.casefold()
        if _looks_like_affiliation(line) or _is_stop(line):
            continue
        if any(token in lowered for token in _JOURNAL_TOKENS):
            return line
    return ""


def _year_from_page_one(pages: Sequence[str]) -> int | None:
    if not pages:
        return None
    head = pages[0][:2000]
    stamped = _STAMP_DATE.search(head)
    if stamped:
        return int(stamped.group("year"))
    for marker in _YEAR_MARKERS:
        found = marker.search(head)
        if found:
            year = _YEAR.search(found.group(0))
            if year:
                return int(year.group(1))
    return None


def extract_pdf_metadata(pages: Sequence[str]) -> PdfMetadata:
    """Everything the text layer will give up about one paper."""
    notes: list[str] = []
    evidence: dict[str, str] = {}
    if not pages:
        return PdfMetadata(
            confidence=ConfidenceBand.UNVERIFIED,
            notes=("pdf_text_layer_empty",),
        )

    head = "\n".join(pages[:2])
    title_lines = title_from_pages(pages)
    if not title_lines:
        notes.append("title_not_found_on_page_one")

    authors = _authors_from_page_one(pages, title_lines)
    if not authors:
        notes.append("authors_not_found_on_page_one")

    arxiv_match = _ARXIV_STAMP.search(head) or _ARXIV_BARE.search(
        head.strip().split("\n")[0]
    )
    arxiv_id = arxiv_match.group("id") if arxiv_match else ""
    bare = (
        _ARXIV_BARE.match(_clean(pages[0].splitlines()[0]))
        if pages[0].strip()
        else None
    )
    if not arxiv_id and bare:
        arxiv_id = bare.group("id")
    if arxiv_id:
        evidence["arxiv_id"] = "page_1_stamp"

    doi = ""
    doi_match = _DOI.search(head)
    if doi_match:
        doi = doi_match.group(0).rstrip(".")
        evidence["doi"] = "page_1_text"

    year = _year_from_page_one(pages)
    if year is not None:
        evidence["year"] = "page_1_text"

    venue = _venue_from_page_one(pages)
    if venue:
        evidence["venue"] = "page_1_text"
        notes.append("venue_matched_by_name_only")

    title = " ".join(title_lines)
    if len(title_lines) > 1:
        notes.append(f"title_joined_from_{len(title_lines)}_lines")

    score = 0
    score += 2 if title else 0
    score += 2 if authors else 0
    score += 1 if year else 0
    score += 1 if (doi or arxiv_id) else 0
    score += 1 if venue else 0
    confidence = (
        ConfidenceBand.MEDIUM
        if score >= 5
        else ConfidenceBand.LOW
        if score >= 3
        else ConfidenceBand.UNVERIFIED
    )
    return PdfMetadata(
        title=title,
        title_lines=title_lines,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        confidence=confidence,
        notes=tuple(notes),
        evidence=evidence,
    )


def apply_pdf_metadata(paper: PaperRecord, metadata: PdfMetadata) -> PaperRecord:  # noqa: F821
    """Fill a paper's empty fields from what the text layer offered.

    Everything filled here is ``LOW`` confidence and says so, because a name
    block and a wrapped title line are guesses compared with an index entry.
    """
    from dataclasses import replace

    from .contracts import AuthorRecord, Provenance, VenueRecord, utc_now

    def _prov(field_name: str) -> Provenance:
        return Provenance(
            provider="pdf_text_layer",
            route="user_supplied",
            method=f"pdf_metadata:{field_name}",
            url="",
            retrieved_at=utc_now(),
            sha256="",
            license="user_supplied",
            confidence=metadata.confidence,
            locator=None,
            note="read_from_text_layer",
        )

    changes: dict[str, object] = {}
    missing = dict(paper.missing)
    provenance = {key: tuple(value) for key, value in paper.provenance.items()}
    filled: list[str] = []
    if not paper.title and metadata.title:
        changes["title"] = metadata.title
        provenance["title"] = (_prov("title"),)
        missing.pop("title", None)
        filled.append("title")
    if not paper.authors and metadata.authors:
        changes["authors"] = tuple(
            AuthorRecord(name=name, position=index + 1, provenance=(_prov("authors"),))
            for index, name in enumerate(metadata.authors)
        )
        provenance["authors"] = (_prov("authors"),)
        missing.pop("authors", None)
        filled.append("authors")
    if paper.year is None and metadata.year is not None:
        changes["year"] = metadata.year
        provenance["year"] = (_prov("year"),)
        missing.pop("year", None)
        filled.append("year")
    if not paper.doi and metadata.doi:
        changes["doi"] = metadata.doi
        provenance["doi"] = (_prov("doi"),)
        missing.pop("doi", None)
        filled.append("doi")
    if not paper.venue.name and metadata.venue:
        changes["venue"] = VenueRecord(
            name=metadata.venue, provenance=(_prov("venue"),)
        )
        provenance["venue"] = (_prov("venue"),)
        missing.pop("venue", None)
        filled.append("venue")
    import json

    notes = (
        *paper.notes,
        f"pdf_metadata:{json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True)}",
    )
    if filled:
        notes = (*notes, f"metadata_from_pdf_text_layer:{','.join(filled)}")
    changes.update({"provenance": provenance, "missing": missing, "notes": notes})
    return replace(paper, **changes)
