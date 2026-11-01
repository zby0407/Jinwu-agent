"""Recover tables from PDF text, with the limits of that recovery made loud.

A PDF has no table structure to read: the text layer only tells us where glyphs
landed. Reconstructing columns is therefore a *guess*, and these tables are
marked as such — low confidence, a ``pdf_text_reconstruction`` warning, and a
provenance locator naming the page and the strategy used. The alternative
(producing a confident-looking table from a guess) is worse than saying
nothing.

Two readers share this module.  PyMuPDF reads word geometry, so a column break
is a measurable gap rather than a run of spaces; pypdf remains the fallback when
PyMuPDF is unavailable.  A page with almost no text layer is a scan or an
image-only table: only then, and only when the caller allows network access, is
it sent to the configured cloud OCR adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .cloud_figures import CloudFigureConfig, extract_cloud_figures
from .cloud_tables import CloudTableConfig, Opener, extract_cloud_tables
from .contracts import ExtractedTable
from .domain import slug
from .pdf_layout import (
    MIN_COLUMNS,
    MIN_ROWS,
    PdfPageLayout,
    read_pdf_layout,
    tables_from_layout_page,
)
from .table_builder import build_table, contains_number, is_number

_TWO_SPACES = re.compile(r"\s{2,}")

PDF_TEXT_WARNING = "pdf_text_reconstruction"
PDF_COLUMN_CONFIDENCE = 0.45
CLOUD_TEXT_FLOOR = 200

__all__ = [
    "CLOUD_TEXT_FLOOR",
    "PDF_TEXT_WARNING",
    "PdfText",
    "pdf_summary",
    "read_pdf_report",
    "read_pdf_tables",
    "read_pdf_text",
    "tables_from_text",
    "tables_from_text_report",
]


@dataclass(slots=True)
class PdfText:
    """The text layer of a PDF, page by page, plus how it was obtained."""

    path: str
    pages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    engine: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "engine": self.engine or "pypdf",
            "page_count": len(self.pages),
            "characters": len(self.text),
            "warnings": list(self.warnings),
        }


def read_pdf_text(path: str | Path) -> PdfText:
    """Extract the text layer, preferring word-aware PyMuPDF output."""

    target = Path(path)
    layout = read_pdf_layout(target, detect_tables=False)
    if layout.pages:
        pages = tuple(page.text for page in layout.pages)
        warnings = list(layout.warnings)
        if not any(page.strip() for page in pages):
            warnings.append("no_text_layer")
        return PdfText(
            str(target),
            pages=pages,
            warnings=tuple(dict.fromkeys(warnings)),
            engine=layout.engine,
        )

    try:
        from pypdf import PdfReader
    except ImportError:
        return PdfText(
            str(target),
            warnings=tuple(dict.fromkeys((*layout.warnings, "pypdf_not_installed"))),
            engine="",
        )
    try:
        reader = PdfReader(str(target))
        pages = tuple((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # an encrypted or broken PDF must not crash a run
        return PdfText(
            str(target),
            warnings=tuple(dict.fromkeys((*layout.warnings, f"unreadable_pdf:{exc}"))),
            engine="",
        )
    warnings: list[str] = list(layout.warnings)
    if not any(page.strip() for page in pages):
        warnings.append("no_text_layer")
    return PdfText(
        str(target),
        pages=pages,
        warnings=tuple(dict.fromkeys(warnings)),
        engine="pypdf",
    )


def _cells(line: str) -> list[str]:
    cells = [cell.strip() for cell in _TWO_SPACES.split(line.strip()) if cell.strip()]
    if len(cells) >= MIN_COLUMNS:
        return cells
    # A text layer often collapses a table to single spaces.  Fall back to
    # single-space tokens only when the line has enough of them; the numeric
    # row check below still decides whether a block is a table.
    tokens = line.split()
    if len(tokens) >= MIN_COLUMNS:
        return tokens
    return cells


def tables_from_text(
    text: str,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    path: str = "",
    page: int = 0,
    min_columns: int = MIN_COLUMNS,
    min_rows: int = MIN_ROWS,
) -> tuple[ExtractedTable, ...]:
    """Find whitespace-aligned table blocks in already-extracted text."""
    tables, _rejected = tables_from_text_report(
        text,
        source_id=source_id,
        table_id_prefix=table_id_prefix,
        path=path,
        page=page,
        min_columns=min_columns,
        min_rows=min_rows,
    )
    return tables


def tables_from_text_report(
    text: str,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    path: str = "",
    page: int = 0,
    min_columns: int = MIN_COLUMNS,
    min_rows: int = MIN_ROWS,
) -> tuple[tuple[ExtractedTable, ...], tuple[dict[str, object], ...]]:
    """Extract tables and report blocks rejected for inconsistent columns."""
    lines = text.splitlines()
    blocks: list[tuple[int, int, list[list[str]]]] = []
    rejected: list[dict[str, object]] = []
    current: list[list[str]] = []
    width = 0
    start = 0

    def flush() -> None:
        nonlocal current, width
        if len(current) >= min_rows and width >= min_columns:
            numeric_rows = sum(
                1 for row in current[1:] if any(contains_number(cell) for cell in row)
            )
            # Without numeric cells this is prose, not a table.
            if numeric_rows * 2 >= len(current) - 1:
                blocks.append((start, start + len(current) - 1, list(current)))
        current = []
        width = 0

    for index, line in enumerate(lines):
        cells = _cells(line)
        if len(cells) < min_columns:
            flush()
            continue
        if current and len(cells) != width:
            if (
                width >= min_columns
                and not all(is_number(cell) for cell in current[0])
                and any(contains_number(cell) for cell in cells)
            ):
                rejected.append(
                    {
                        "line_start": start + 1,
                        "line_end": index + 1,
                        "expected_columns": width,
                        "found_columns": len(cells),
                        "reason": "inconsistent_columns",
                    }
                )
            flush()
        if not current:
            start = index
            width = len(cells)
        current.append(cells)
    flush()

    tables = [
        build_table(
            rows,
            source_id=source_id,
            table_id=f"{table_id_prefix}:p{page}:t{position}",
            table_index=position,
            page=page,
            path=path,
            lines=lines,
            first=first,
            last=last,
            columns_from="whitespace_runs",
            confidence_score=PDF_COLUMN_CONFIDENCE,
            warning=PDF_TEXT_WARNING,
            extractor="jw.scientific_data.documents.tables_from_text",
        )
        for position, (first, last, rows) in enumerate(blocks)
    ]
    return tuple(tables), tuple(rejected)


def read_pdf_tables(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    max_tables: int = 20,
    allow_network: bool = False,
    cloud_config: CloudTableConfig | None = None,
    cloud_opener: Opener | None = None,
    figures: bool = False,
    figure_config: CloudFigureConfig | None = None,
    figure_opener: Opener | None = None,
) -> tuple[ExtractedTable, ...]:
    """Extract tables from every page of a PDF (local geometry, cloud fallback)."""
    tables, _rejected, _warnings = read_pdf_report(
        path,
        source_id=source_id,
        table_id_prefix=table_id_prefix,
        max_tables=max_tables,
        allow_network=allow_network,
        cloud_config=cloud_config,
        cloud_opener=cloud_opener,
        figures=figures,
        figure_config=figure_config,
        figure_opener=figure_opener,
    )
    return tables


def _wants_cloud(page: PdfPageLayout) -> bool:
    """Whether a page is plausibly a scan rather than ordinary text.

    A page without any text layer is the obvious case.  A page with a short
    caption above an embedded image is the other: papers often ship a table as
    a picture, and the caption is the only text on the page.  Prose pages, even
    short ones, are never sent out.
    """

    text = page.text.strip()
    if not text:
        return True
    return page.has_images and len(text) < CLOUD_TEXT_FLOOR


def read_pdf_report(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    max_tables: int = 20,
    allow_network: bool = False,
    cloud_config: CloudTableConfig | None = None,
    cloud_opener: Opener | None = None,
    figures: bool = False,
    figure_config: CloudFigureConfig | None = None,
    figure_opener: Opener | None = None,
) -> tuple[tuple[ExtractedTable, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
    """Extract tables plus the rejected blocks and document-level warnings.

    ``allow_network`` only gates the escalation step.  Local geometry and any
    cached cloud response work regardless of it, so an offline run still replays
    exactly the tables a networked run produced.

    ``figures`` opts into chart reading through the cloud figure adapter for
    pages that carry an image, a real text layer and no local table.  Pages
    without a usable text layer stay on the table-OCR path: a scan is not a
    chart, and sending it to a chart prompt would only produce invented points.
    """

    target = str(path)
    layout = read_pdf_layout(target)
    tables: list[ExtractedTable] = []
    rejected: list[dict[str, object]] = []
    warnings: list[str] = list(layout.warnings)
    cloud_pages: list[int] = []
    figure_pages: list[int] = []

    if layout.pages:
        if not any(page.text.strip() for page in layout.pages):
            warnings.append("no_text_layer")
        for page in layout.pages:
            produced, page_rejected = tables_from_layout_page(
                page,
                source_id=source_id,
                table_id_prefix=table_id_prefix,
                path=target,
            )
            for item in page_rejected:
                rejected.append({**item, "page": page.page_number})
            tables.extend(produced)
            if len(tables) >= max_tables:
                break
            if not produced and _wants_cloud(page):
                cloud_pages.append(page.page_number)
            elif figures and not produced and page.has_images and page.text.strip():
                figure_pages.append(page.page_number)
    else:
        document = read_pdf_text(target)
        warnings.extend(document.warnings)
        for page_number, page_text in enumerate(document.pages, start=1):
            if not page_text.strip():
                continue
            produced, page_rejected = tables_from_text_report(
                page_text,
                source_id=source_id,
                table_id_prefix=table_id_prefix,
                path=target,
                page=page_number,
            )
            for item in page_rejected:
                rejected.append({**item, "page": page_number})
            tables.extend(produced)
            if len(tables) >= max_tables:
                break

    if cloud_pages and len(tables) < max_tables:
        cloud_tables, _cloud_rejected, cloud_warnings = extract_cloud_tables(
            target,
            source_id=source_id,
            table_id_prefix=table_id_prefix,
            pages=cloud_pages,
            config=cloud_config,
            allow_network=allow_network,
            opener=cloud_opener,
            max_tables=max_tables - len(tables),
        )
        tables.extend(cloud_tables)
        warnings.extend(cloud_warnings)

    if figures and figure_pages and len(tables) < max_tables:
        figure_tables, _figure_rejected, figure_warnings = extract_cloud_figures(
            target,
            source_id=source_id,
            table_id_prefix=table_id_prefix,
            pages=figure_pages,
            config=figure_config,
            allow_network=allow_network,
            opener=figure_opener,
            max_tables=max_tables - len(tables),
        )
        tables.extend(figure_tables)
        warnings.extend(figure_warnings)

    return tuple(tables), tuple(rejected), tuple(dict.fromkeys(warnings))


def pdf_summary(path: str | Path, *, source_id: str = "") -> dict[str, object]:
    """What a PDF contributed, for the run report."""

    document = read_pdf_text(path)
    tables = read_pdf_tables(
        path, source_id=source_id or f"pdf:{slug(Path(path).stem)}"
    )
    return {
        **document.to_dict(),
        "tables": len(tables),
        "source_id": source_id,
        "warning": (
            "Tables here are reconstructed from text-layer layout; column "
            "boundaries are a reading, not a fact. Image-only pages are read "
            "through the cloud adapter only when the caller allows network "
            "access and credentials are configured."
        ),
    }
