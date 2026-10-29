"""Turn provider payloads and a PDF text layer into one :class:`PaperRecord`.

Field-by-field, this module answers three questions that the influence model
depends on: *what is in this paper* (title, authors, references, data
statement), *where exactly did we read it* (page, section, character offsets),
and *what is missing and why* (``MissingReason``), so a paper that is
metadata-only is distinguishable from a paper that truly has no data statement.

Nothing is inferred from a paper's absence.  A missing data statement on a
paper we could not read is ``FULL_TEXT_UNAVAILABLE``, not "no open data".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .contracts import (
    CORE_FIELDS,
    AccessDecision,
    AccessRoute,
    AuthorRecord,
    ConfidenceBand,
    Locator,
    MissingReason,
    OpenDataKind,
    OpenDataRecord,
    PaperRecord,
    Provenance,
    ReferenceRecord,
    VenueRecord,
    normalise_doi,
    utc_now,
)
from .fulltext import FulltextResult

__all__ = [
    "CANONICAL_SECTIONS",
    "build_paper",
    "normalise_section_name",
    "open_data_from_text",
    "references_from_text",
    "sections_from_pages",
]

#: Section names the downstream features understand.  Anything else is kept
#: under its own heading so nothing is silently discarded.
CANONICAL_SECTIONS: tuple[str, ...] = (
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusions",
    "acknowledgements",
    "data_availability",
    "software",
    "references",
    "appendix",
)

_SECTION_ALIASES: Mapping[str, str] = {
    "summary": "abstract",
    "abstract": "abstract",
    "introduction": "introduction",
    "motivation": "introduction",
    "methods": "methods",
    "method": "methods",
    "methodology": "methods",
    "observations": "methods",
    "data": "methods",
    "data and methods": "methods",
    "materials and methods": "methods",
    "analysis": "methods",
    "results": "results",
    "results and discussion": "results",
    "discussion": "discussion",
    "discussion and conclusions": "discussion",
    "conclusions": "conclusions",
    "conclusion": "conclusions",
    "summary and conclusions": "conclusions",
    "acknowledgements": "acknowledgements",
    "acknowledgments": "acknowledgements",
    "acknowledgement": "acknowledgements",
    "data availability": "data_availability",
    "data availability statement": "data_availability",
    "availability of data": "data_availability",
    "data and code availability": "data_availability",
    "software": "software",
    "code availability": "software",
    "references": "references",
    "bibliography": "references",
    "appendix": "appendix",
    "appendices": "appendix",
    "supplementary material": "appendix",
}

_HEADING_NUMBER = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+")
_DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+")
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\])}]+")
_REFERENCE_MARKER = re.compile(
    r"^\s*(?:\[(?P<bracket>\d{1,3})\]|(?P<number>\d{1,3})\.)\s+\S"
)
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

#: Repositories worth recognising in a data-availability statement.  The list is
#: deliberately domain-heavy (astronomy) plus the general-purpose archives.
_REPOSITORIES: Mapping[str, str] = {
    "zenodo.org": "Zenodo",
    "figshare.com": "Figshare",
    "datadryad.org": "Dryad",
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "mast.stsci.edu": "MAST",
    "vizier.cds.unistra.fr": "VizieR",
    "cdsarc.cds.unistra.fr": "VizieR",
    "vizier.u-strasbg.fr": "VizieR",
    "archive.eso.org": "ESO Archive",
    "irsa.ipac.caltech.edu": "IRSA",
    "ned.ipac.caltech.edu": "NED",
    "simbad.u-strasbg.fr": "SIMBAD",
    "heasarc.gsfc.nasa.gov": "HEASARC",
    "chandra.harvard.edu": "Chandra Archive",
    "asdc.larc.nasa.gov": "ASDC",
    "open Science framework": "OSF",
    "osf.io": "OSF",
    "doi.org/10.5281": "Zenodo",
}

_OPEN_DATA_PHRASES: tuple[str, ...] = (
    "available at",
    "available from",
    "publicly available",
    "are available",
    "is available",
    "data are available",
    "data used in this work",
    "we use publicly available",
)


@dataclass(slots=True)
class _Section:
    """One located section of the text layer."""

    name: str
    page: int
    char_start: int
    char_end: int
    text: str


def normalise_section_name(raw: str) -> str:
    """Map a heading onto a canonical section name, or ``""`` when unknown."""
    text = " ".join(_HEADING_NUMBER.sub("", str(raw or "")).split()).casefold()
    text = text.strip(" .:—-")
    if not text:
        return ""
    if text in _SECTION_ALIASES:
        return _SECTION_ALIASES[text]
    # Longest alias first, so "data and code availability" wins over "data".
    for alias in sorted(_SECTION_ALIASES, key=len, reverse=True):
        if text == alias or (
            text.startswith(alias + " ") and len(text) <= len(alias) + 24
        ):
            return _SECTION_ALIASES[alias]
    return ""


def sections_from_pages(pages: Sequence[str]) -> dict[str, str]:
    """Split a page-level text layer into canonical sections."""
    located = _locate_sections(pages)
    merged: dict[str, list[str]] = {}
    for section in located:
        merged.setdefault(section.name, []).append(section.text)
    return {name: "\n".join(parts).strip() for name, parts in merged.items()}


def _locate_sections(pages: Sequence[str]) -> tuple[_Section, ...]:
    """Find headings in the text layer and slice the text between them."""
    located: list[_Section] = []
    for page_index, page in enumerate(pages, 1):
        offset = 0
        current_name = ""
        current_start = 0
        buffer: list[str] = []
        for line in page.splitlines(keepends=True):
            heading = _heading(line)
            if heading and buffer:
                located.append(
                    _Section(
                        name=current_name or "body",
                        page=page_index,
                        char_start=current_start,
                        char_end=offset,
                        text="".join(buffer).strip(),
                    )
                )
                buffer = []
            if heading:
                current_name = heading
                current_start = offset
            buffer.append(line)
            offset += len(line)
        if buffer:
            located.append(
                _Section(
                    name=current_name or "body",
                    page=page_index,
                    char_start=current_start,
                    char_end=offset,
                    text="".join(buffer).strip(),
                )
            )
    return tuple(item for item in located if item.text)


def _heading(line: str) -> str:
    """Canonical section name when ``line`` is a heading, else ``""``."""
    text = line.strip()
    if not text or len(text) > 64:
        return ""
    if text.endswith(".") and not _HEADING_NUMBER.match(text):
        return ""
    if sum(1 for char in text if char.isalpha()) < 3:
        return ""
    return normalise_section_name(text)


def references_from_text(pages: Sequence[str]) -> tuple[ReferenceRecord, ...]:
    """Recover reference entries from the text layer.

    Publishers do not give us a reference parser, so this is a *candidate* list:
    numbering is trusted (it is typographic), DOIs are extracted, and the whole
    entry is kept verbatim in ``raw`` so a human can check what we matched.  The
    resolved bibliometric fields come from Crossref/OpenAlex, not from here.
    """
    text = "\n".join(pages)
    start = _references_start(text)
    if start < 0:
        return ()
    block = text[start:]
    entries: list[tuple[int, list[str]]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker = _REFERENCE_MARKER.match(line)
        if marker:
            entries.append(
                (int(marker.group("bracket") or marker.group("number")), [stripped])
            )
        elif entries:
            entries[-1][1].append(stripped)
        elif len(stripped) > 20:
            entries.append((len(entries) + 1, [stripped]))
    out: list[ReferenceRecord] = []
    for index, (number, lines) in enumerate(entries, 1):
        raw = " ".join(lines)
        doi = _first_doi(raw)
        year = _first_year(raw)
        title = _guess_reference_title(raw)
        out.append(
            ReferenceRecord(
                index=number or index,
                raw=raw[:1200],
                doi=doi,
                title=title,
                year=year,
                venue="",
                match_confidence=ConfidenceBand.LOW
                if doi
                else ConfidenceBand.UNVERIFIED,
                provenance=(
                    Provenance(
                        provider="pdf_text",
                        route=AccessRoute.METADATA_ONLY,
                        method="reference_entry_regex",
                        retrieved_at=utc_now(),
                        confidence=ConfidenceBand.LOW,
                        locator=Locator(section="references", char_start=start),
                        note="numbering and DOI are typographic; bibliometrics come from providers",
                    ),
                ),
            )
        )
    return tuple(out)


def _references_start(text: str) -> int:
    """Character offset where the reference list begins, or ``-1``."""
    for match in re.finditer(r"(?im)^[ \t]*(?:\d+\.?[ \t]*)?references[ \t]*$", text):
        return match.end()
    return -1


def _first_doi(text: str) -> str:
    """First DOI-looking token in ``text``."""
    match = _DOI_IN_TEXT.search(text or "")
    return normalise_doi(match.group(0).rstrip(".,;")) if match else ""


def _first_year(text: str) -> int | None:
    """First plausible publication year in ``text``."""
    match = _YEAR.search(text or "")
    return int(match.group(1)) if match else None


def _guess_reference_title(raw: str) -> str:
    """Best-effort title from a reference entry.

    Reference styles differ too much to be parsed reliably, so we take the
    longest quoted or capitalised segment and leave the rest to the provider
    match.  A wrong title here cannot leak into the model: features use the
    resolved work, not this guess.
    """
    quoted = re.findall(r"[\"“”']([^\"“”']{8,200})[\"“”']", raw)
    if quoted:
        return quoted[0].strip()
    parts = [part.strip() for part in re.split(r"\.\s+|,\s+", raw) if part.strip()]
    candidates = [part for part in parts if len(part) > 12 and not part.isupper()]
    return candidates[0] if candidates else ""


def open_data_from_text(pages: Sequence[str]) -> tuple[OpenDataRecord, ...]:
    """Find data/code availability evidence in the text layer.

    Only statements inside a data-availability-like section, or sentences that
    use an availability phrase next to a repository URL, are accepted.  Random
    URLs in the body are not data statements and must not become one.
    """
    sections = _locate_sections(pages)
    statement_sections = [
        section
        for section in sections
        if section.name in {"data_availability", "software", "appendix"}
    ]
    context = "\n".join(section.text for section in statement_sections)
    if not context.strip():
        return ()
    out: list[OpenDataRecord] = []
    for match in _URL_IN_TEXT.finditer(context):
        url = match.group(0).rstrip(".,;)")
        repository, kind = _classify_repository(url)
        if not repository:
            continue
        out.append(
            OpenDataRecord(
                kind=kind,
                url=url,
                repository=repository,
                accession=_accession(url),
                statement=_sentence_around(context, match.start()),
                locator=Locator(section="data_availability", char_start=match.start()),
                confidence=ConfidenceBand.MEDIUM if repository else ConfidenceBand.LOW,
                provenance=(
                    Provenance(
                        provider="pdf_text",
                        route=AccessRoute.METADATA_ONLY,
                        method="data_statement_url",
                        retrieved_at=utc_now(),
                        confidence=ConfidenceBand.MEDIUM,
                        locator=Locator(
                            section="data_availability", char_start=match.start()
                        ),
                    ),
                ),
            )
        )
    return _dedupe_open_data(out)


def _classify_repository(url: str) -> tuple[str, OpenDataKind]:
    """Repository name and data kind for one URL."""
    lowered = url.casefold()
    for host, name in _REPOSITORIES.items():
        if host.casefold() in lowered:
            kind = (
                OpenDataKind.CODE if name in {"GitHub", "GitLab"} else OpenDataKind.DATA
            )
            return name, kind
    return "", OpenDataKind.UNKNOWN


def _accession(url: str) -> str:
    """Trailing identifier of a repository URL, when there is one."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail if any(char.isdigit() for char in tail) else ""


def _sentence_around(text: str, position: int, *, width: int = 160) -> str:
    """The sentence containing ``position``, clipped to ``width`` characters."""
    start = max(0, position - width)
    end = min(len(text), position + width)
    window = text[start:end].replace("\n", " ")
    return " ".join(window.split())


def _dedupe_open_data(records: Iterable[OpenDataRecord]) -> tuple[OpenDataRecord, ...]:
    """Keep the first mention of each URL."""
    seen: set[str] = set()
    out: list[OpenDataRecord] = []
    for record in records:
        key = record.url.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return tuple(out)


def build_paper(
    *,
    paper_id: str,
    doi: str = "",
    openalex: Mapping[str, object] | None = None,
    crossref: Mapping[str, object] | None = None,
    europepmc: Mapping[str, object] | None = None,
    fulltext: FulltextResult | None = None,
    access: AccessDecision | None = None,
    notes: Sequence[str] = (),
) -> PaperRecord:
    """Assemble a :class:`PaperRecord` from whatever the ladder obtained.

    Provider precedence is explicit and stable: OpenAlex wins for bibliometrics
    (it is the indexed authority for citations), Crossref fills gaps (notably
    reference lists and JATS abstracts), the PDF text layer supplies sections
    and the data statement.  Every field keeps the provider that answered it.
    """
    oa = dict(openalex or {})
    cr = dict(crossref or {})
    ep = dict(europepmc or {})
    route = access.route if access is not None else AccessRoute.METADATA_ONLY
    provenance: dict[str, tuple[Provenance, ...]] = {}
    missing: dict[str, MissingReason] = {}
    note_list = list(notes)

    title = _first_text(oa.get("title"), cr.get("title"), ep.get("title"))
    provenance["title"] = _prov(
        ("openalex", oa.get("title")),
        ("crossref", cr.get("title")),
        ("europepmc", ep.get("title")),
    )
    if not title:
        missing["title"] = MissingReason.NOT_FOUND

    abstract = _first_text(oa.get("abstract"), cr.get("abstract"), "")
    if not abstract:
        missing["abstract"] = (
            MissingReason.NOT_REPORTED if any((oa, cr)) else MissingReason.NOT_INDEXED
        )
    provenance["abstract"] = _prov(
        ("openalex", oa.get("abstract")), ("crossref", cr.get("abstract"))
    )

    year = _first_int(oa.get("year"), cr.get("year"), ep.get("year"))
    if year is None:
        missing["year"] = MissingReason.NOT_REPORTED

    authors = _authors(oa, cr)
    if not authors:
        missing["authors"] = MissingReason.NOT_REPORTED

    venue = _venue(oa, cr)
    if not venue.name:
        missing["venue"] = MissingReason.NOT_REPORTED

    references, reference_note = _references(oa, cr, fulltext)
    if reference_note:
        note_list.append(reference_note)
    if not references:
        missing["references"] = (
            MissingReason.NOT_REPORTED
            if route.has_full_text or oa.get("referenced_works")
            else MissingReason.FULL_TEXT_UNAVAILABLE
        )
    reference_count = _first_int(
        oa.get("reference_count"),
        cr.get("reference_count"),
        len(oa.get("referenced_works") or ()) or None,
    )

    sections: dict[str, str] = {}
    open_data: tuple[OpenDataRecord, ...] = ()
    pages = fulltext.pages if fulltext is not None else ()
    if pages:
        sections = sections_from_pages(pages)
        open_data = open_data_from_text(pages)
        if not open_data:
            note_list.append("data_availability_not_state_in_text")
    else:
        missing["sections"] = MissingReason.FULL_TEXT_UNAVAILABLE

    keywords = _keywords(oa)
    paper = PaperRecord(
        paper_id=paper_id,
        title=title,
        abstract=abstract,
        year=year,
        doi=normalise_doi(doi or str(oa.get("doi") or "") or str(cr.get("doi") or "")),
        language=str(oa.get("language") or cr.get("language") or ""),
        venue=venue,
        authors=authors,
        references=references,
        open_data=open_data,
        sections=sections,
        keywords=keywords,
        cited_by_count=_first_int(oa.get("cited_by_count"), cr.get("cited_by_count")),
        reference_count=reference_count,
        access_route=route,
        access=access,
        fulltext_path=fulltext.path if fulltext is not None else "",
        fulltext_pages=fulltext.page_count if fulltext is not None else 0,
        work_id=str(oa.get("work_id") or ""),
        source_paths=(fulltext.path,) if fulltext is not None and fulltext.path else (),
        missing=missing,
        provenance=provenance,
        notes=tuple(note_list),
    )
    paper.missing = _finalise_missing(paper, missing)
    return paper


def _finalise_missing(
    paper: PaperRecord, missing: dict[str, MissingReason]
) -> dict[str, MissingReason]:
    """Add a typed absence for every core field that ended up empty."""
    out = dict(missing)
    for name in CORE_FIELDS:
        if name in out:
            continue
        value = getattr(paper, name, None)
        if isinstance(value, (tuple, list, Mapping)):
            if not value:
                out[name] = MissingReason.NOT_REPORTED
        elif value in (None, ""):
            out[name] = MissingReason.NOT_REPORTED
    return out


def _prov(*pairs: tuple[str, object]) -> tuple[Provenance, ...]:
    """Provenance entries for the providers that actually answered a field."""
    entries: list[Provenance] = []
    for provider, value in pairs:
        if value in (None, "", 0, ()):
            continue
        entries.append(
            Provenance(
                provider=provider,
                route=AccessRoute.METADATA_ONLY,
                method="metadata_field",
                retrieved_at=utc_now(),
                confidence=ConfidenceBand.HIGH,
            )
        )
    return tuple(entries)


def _first_text(*values: object) -> str:
    """First non-empty string among ``values``."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_int(*values: object) -> int | None:
    """First integer-like value among ``values``."""
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _authors(
    openalex: Mapping[str, object], crossref: Mapping[str, object]
) -> tuple[AuthorRecord, ...]:
    """Author list, preferring the provider with affiliations attached."""
    source = openalex.get("authors") or crossref.get("authors") or ()
    out: list[AuthorRecord] = []
    for position, item in enumerate(source, 1):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append(
            AuthorRecord(
                name=name,
                given=str(item.get("given") or ""),
                family=str(item.get("family") or ""),
                position=int(item.get("position") or position),
                orcid=str(item.get("orcid") or ""),
                institution=str(item.get("institution") or ""),
                country=str(item.get("country") or ""),
                is_corresponding=bool(item.get("is_corresponding")),
                provenance=(
                    Provenance(
                        provider="openalex" if openalex.get("authors") else "crossref",
                        route=AccessRoute.METADATA_ONLY,
                        method="authorship_list",
                        retrieved_at=utc_now(),
                        confidence=ConfidenceBand.HIGH,
                    ),
                ),
            )
        )
    return tuple(out)


def _venue(
    openalex: Mapping[str, object], crossref: Mapping[str, object]
) -> VenueRecord:
    """Journal record, preferring OpenAlex's source object."""
    for payload, provider in ((openalex, "openalex"), (crossref, "crossref")):
        raw = payload.get("venue")
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        issn = raw.get("issn") or ()
        return VenueRecord(
            name=name,
            venue_type=str(raw.get("venue_type") or ""),
            publisher=str(raw.get("publisher") or ""),
            issn=tuple(str(item) for item in issn),
            is_open_access=bool(raw.get("is_open_access")),
            provenance=(
                Provenance(
                    provider=provider,
                    route=AccessRoute.METADATA_ONLY,
                    method="journal_object",
                    retrieved_at=utc_now(),
                    confidence=ConfidenceBand.HIGH,
                ),
            ),
        )
    return VenueRecord()


def _references(
    openalex: Mapping[str, object],
    crossref: Mapping[str, object],
    fulltext: FulltextResult | None,
) -> tuple[tuple[ReferenceRecord, ...], str]:
    """Reference list plus a note explaining which source produced it."""
    crossref_refs = crossref.get("references") or ()
    if crossref_refs:
        out: list[ReferenceRecord] = []
        for item in crossref_refs:
            if not isinstance(item, Mapping):
                continue
            out.append(
                ReferenceRecord(
                    index=int(item.get("index") or len(out) + 1),
                    raw=str(item.get("raw") or ""),
                    doi=normalise_doi(str(item.get("doi") or "")),
                    title=str(item.get("title") or ""),
                    year=_first_int(item.get("year")),
                    venue=str(item.get("venue") or ""),
                    match_confidence=ConfidenceBand.HIGH
                    if item.get("doi")
                    else ConfidenceBand.MEDIUM,
                    provenance=(
                        Provenance(
                            provider="crossref",
                            route=AccessRoute.METADATA_ONLY,
                            method="deposited_reference_list",
                            retrieved_at=utc_now(),
                            confidence=ConfidenceBand.HIGH,
                        ),
                    ),
                )
            )
        return tuple(out), "references_from_crossref_deposit"
    referenced = openalex.get("referenced_works") or ()
    if referenced:
        out = []
        for index, work in enumerate(referenced, 1):
            if not isinstance(work, str):
                continue
            out.append(
                ReferenceRecord(
                    index=index,
                    raw=work,
                    resolved_work_id=work,
                    match_confidence=ConfidenceBand.MEDIUM,
                    provenance=(
                        Provenance(
                            provider="openalex",
                            route=AccessRoute.METADATA_ONLY,
                            method="referenced_works_ids",
                            retrieved_at=utc_now(),
                            confidence=ConfidenceBand.MEDIUM,
                            note="identifier only: no title or year for this reference",
                        ),
                    ),
                )
            )
        return tuple(out), "references_from_openalex_ids"
    if fulltext is not None and fulltext.pages:
        parsed = references_from_text(fulltext.pages)
        if parsed:
            return parsed, "references_parsed_from_pdf_text"
    return (), ""


def _keywords(openalex: Mapping[str, object]) -> tuple[str, ...]:
    """Topic names from OpenAlex, most specific first."""
    topics = openalex.get("topics") or ()
    out = []
    for item in topics:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
    return tuple(out)
