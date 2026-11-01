"""Read a PDF's text layer as geometry, not as a stream of characters.

``pypdf`` gives text with the columns already smeared together; rebuilding a
table from it means guessing where cells end.  PyMuPDF exposes each word's
bounding box, so a column boundary becomes a measurable gap between word boxes
instead of a run of spaces.  Tables found by ``page.find_tables()`` (ruled or
text-strategy) are used first; word geometry is the fallback for borderless
tables.  Both paths still emit :data:`PDF_TEXT_WARNING`: the text layer tells
us where glyphs landed, never that a table was intended.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ExtractedTable
from .table_builder import (
    build_table,
    contains_number,
    is_number,
)

PDF_TEXT_WARNING = "pdf_text_reconstruction"
PDF_LAYOUT_CONFIDENCE = 0.62
PDF_STRUCTURED_CONFIDENCE = 0.72
MIN_COLUMNS = 3
MIN_ROWS = 3

_CAPTION = re.compile(r"^\s*(?:table|tab\.|表)\s*[\dIVX]+", re.IGNORECASE)

__all__ = [
    "PdfLayoutDocument",
    "PdfLayoutTable",
    "PdfLine",
    "PdfPageLayout",
    "PdfWord",
    "load_pdf_library",
    "read_pdf_layout",
    "tables_from_layout_page",
]


@dataclass(slots=True)
class PdfWord:
    """One word and the box it occupies on the page."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass(slots=True)
class PdfLine:
    """A visual line: words sharing a baseline, ordered left to right."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    words: tuple[PdfWord, ...] = ()


@dataclass(slots=True)
class PdfLayoutTable:
    """A table PyMuPDF detected, with the box it came from."""

    rows: tuple[tuple[str, ...], ...]
    bbox: tuple[float, float, float, float]


@dataclass(slots=True)
class PdfPageLayout:
    """One page's words, lines, and detector output."""

    page_number: int
    width: float
    height: float
    lines: tuple[PdfLine, ...] = ()
    tables: tuple[PdfLayoutTable, ...] = ()
    warnings: tuple[str, ...] = ()
    has_images: bool = False

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "page": self.page_number,
            "lines": len(self.lines),
            "characters": len(self.text),
            "tables": len(self.tables),
            "images": self.has_images,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class PdfLayoutDocument:
    """Every page of a PDF plus how the layout was obtained.

    ``engine`` is ``"pymupdf"`` when geometry was available and empty when the
    caller must fall back to the plain-text reader.
    """

    path: str
    pages: tuple[PdfPageLayout, ...] = ()
    warnings: tuple[str, ...] = ()
    engine: str = ""

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "engine": self.engine or "unavailable",
            "page_count": len(self.pages),
            "characters": len(self.text),
            "warnings": list(self.warnings),
        }


def load_pdf_library() -> Any | None:
    """Import PyMuPDF under either its new or legacy module name."""

    try:
        import pymupdf  # type: ignore[import-not-found]

        # PyMuPDF 1.26 prints a one-line layout-analyser advertisement to
        # stdout on the first ``find_tables`` call; keep CLI output clean.
        quiet = getattr(pymupdf, "no_recommend_layout", None)
        if callable(quiet):
            quiet()
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore[import-not-found]

        return fitz
    except ImportError:
        return None


def _words(raw: object) -> tuple[PdfWord, ...]:
    words: list[PdfWord] = []
    for item in raw or ():  # type: ignore[union-attr]
        try:
            x0, y0, x1, y1, text = item[0], item[1], item[2], item[3], item[4]
        except (TypeError, IndexError):
            continue
        text = str(text).strip()
        if not text:
            continue
        words.append(
            PdfWord(
                text=text,
                x0=float(x0),
                y0=float(y0),
                x1=float(x1),
                y1=float(y1),
            )
        )
    return tuple(words)


def _lines_from_words(words: tuple[PdfWord, ...]) -> tuple[PdfLine, ...]:
    """Group words into visual lines by vertical overlap."""

    if not words:
        return ()
    heights = [word.y1 - word.y0 for word in words if word.y1 > word.y0]
    tolerance = max(2.0, (statistics.median(heights) if heights else 9.0) * 0.55)
    ordered = sorted(words, key=lambda word: ((word.y0 + word.y1) / 2.0, word.x0))
    groups: list[list[PdfWord]] = []
    current: list[PdfWord] = []
    centre = 0.0
    for word in ordered:
        word_centre = (word.y0 + word.y1) / 2.0
        if current and abs(word_centre - centre) > tolerance:
            groups.append(current)
            current = []
        current.append(word)
        centre = sum((item.y0 + item.y1) / 2.0 for item in current) / len(current)
    if current:
        groups.append(current)

    lines: list[PdfLine] = []
    for group in groups:
        ordered_group = tuple(sorted(group, key=lambda word: word.x0))
        lines.append(
            PdfLine(
                text=" ".join(word.text for word in ordered_group),
                x0=min(word.x0 for word in ordered_group),
                y0=min(word.y0 for word in ordered_group),
                x1=max(word.x1 for word in ordered_group),
                y1=max(word.y1 for word in ordered_group),
                words=ordered_group,
            )
        )
    return tuple(sorted(lines, key=lambda line: (line.y0, line.x0)))


def _clean_matrix(rows: object) -> tuple[tuple[str, ...], ...]:
    """Normalise a detector's cells, dropping empty rows and columns."""

    matrix: list[list[str]] = []
    for row in rows or ():  # type: ignore[union-attr]
        cells = ["" if cell is None else str(cell).strip() for cell in row]
        if any(cells):
            matrix.append(cells)
    if len(matrix) < 2:
        return ()
    width = max(len(row) for row in matrix)
    matrix = [row + [""] * (width - len(row)) for row in matrix]
    keep = [index for index in range(width) if any(row[index] for row in matrix)]
    if len(keep) < 2:
        return ()
    trimmed = tuple(tuple(row[index] for index in keep) for row in matrix)
    return trimmed if len(trimmed) >= 2 else ()


def _read_page(
    page: Any, page_number: int, *, detect_tables: bool = True
) -> PdfPageLayout:
    warnings: list[str] = []
    words = _words(page.get_text("words"))
    lines = _lines_from_words(words)
    detected: list[PdfLayoutTable] = []
    if detect_tables:
        try:
            finder = page.find_tables()
            for table in getattr(finder, "tables", ()) or ():
                rows = _clean_matrix(table.extract())
                if not rows:
                    continue
                bbox = tuple(float(value) for value in (table.bbox or (0, 0, 0, 0)))
                detected.append(PdfLayoutTable(rows=rows, bbox=bbox))  # type: ignore[arg-type]
        except Exception as exc:  # find_tables is an optional convenience
            warnings.append(f"find_tables_failed:{exc}")
    if not words:
        warnings.append("no_text_layer")
    try:
        has_images = bool(page.get_images(full=False))
    except Exception:
        has_images = False
    rect = page.rect
    return PdfPageLayout(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        lines=lines,
        tables=tuple(detected),
        warnings=tuple(warnings),
        has_images=has_images,
    )


def read_pdf_layout(
    path: str | Path, *, detect_tables: bool = True
) -> PdfLayoutDocument:
    """Read word geometry and detector tables.  Never raises for bad input."""

    target = Path(path)
    fitz = load_pdf_library()
    if fitz is None:
        return PdfLayoutDocument(str(target), warnings=("pymupdf_not_installed",))
    try:
        document = fitz.open(str(target))
    except Exception as exc:  # broken or non-PDF bytes must not crash a run
        return PdfLayoutDocument(str(target), warnings=(f"unreadable_pdf:{exc}",))
    try:
        if getattr(document, "needs_pass", False):
            return PdfLayoutDocument(str(target), warnings=("encrypted_pdf",))
        pages = tuple(
            _read_page(page, index, detect_tables=detect_tables)
            for index, page in enumerate(document, start=1)
        )
    except Exception as exc:
        return PdfLayoutDocument(str(target), warnings=(f"unreadable_pdf:{exc}",))
    finally:
        document.close()
    warnings: list[str] = []
    for page in pages:
        warnings.extend(page.warnings)
    return PdfLayoutDocument(
        str(target),
        pages=pages,
        warnings=tuple(dict.fromkeys(warnings)),
        engine="pymupdf",
    )


def _char_width(lines: tuple[PdfLine, ...]) -> float:
    widths = [
        word.width / max(len(word.text), 1)
        for line in lines
        for word in line.words
        if word.width > 0
    ]
    return statistics.median(widths) if widths else 5.0


def _column_gap(lines: tuple[PdfLine, ...]) -> float:
    """Smallest inter-column gap that clearly exceeds a single space.

    A single space is about one character wide, so ``1.5 * char_width`` is a
    safe upper bound for it.  The median positive gap can be much larger when
    a page contains only numeric columns (every gap is then a column gap), so
    the threshold takes the *smaller* of the two estimates instead of letting
    that page inflate it.
    """

    gaps = [
        next_word.x0 - word.x1
        for line in lines
        for word, next_word in zip(line.words, line.words[1:], strict=False)
        if next_word.x0 - word.x1 > 0
    ]
    by_gaps = statistics.median(gaps) * 1.5 if gaps else 0.0
    by_characters = _char_width(lines) * 1.5
    estimates = [value for value in (by_gaps, by_characters) if value > 0]
    return max(3.5, min(estimates) if estimates else 3.5)


def _cells_from_words(words: tuple[PdfWord, ...], gap: float) -> list[str]:
    """Split one line at gaps wider than ``gap``; keep single-spaced words."""
    if not words:
        return []
    cells: list[str] = []
    current: list[str] = [words[0].text]
    previous = words[0]
    for word in words[1:]:
        if word.x0 - previous.x1 > gap:
            cells.append(" ".join(current))
            current = [word.text]
        else:
            current.append(word.text)
        previous = word
    cells.append(" ".join(current))
    return [cell for cell in cells if cell.strip()]


def _blocks_from_lines(
    lines: tuple[PdfLine, ...],
    gap: float,
    *,
    min_columns: int,
    min_rows: int,
) -> tuple[list[tuple[int, int, list[list[str]]]], list[dict[str, object]]]:
    """Find consecutive same-width numeric blocks, as the text reader does."""

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
            if numeric_rows * 2 >= len(current) - 1:
                blocks.append((start, start + len(current) - 1, list(current)))
        current = []
        width = 0

    for index, line in enumerate(lines):
        cells = _cells_from_words(line.words, gap)
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
    return blocks, rejected


def _caption_above(
    lines: tuple[PdfLine, ...], y0: float, *, distance: float = 80.0
) -> str:
    candidates = [
        line for line in lines if line.y1 <= y0 + 1.0 and y0 - line.y1 <= distance
    ]
    for line in reversed(candidates):
        if _CAPTION.match(line.text.strip()):
            return line.text.strip()
    return ""


def tables_from_layout_page(
    page: PdfPageLayout,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    path: str = "",
    min_columns: int = MIN_COLUMNS,
    min_rows: int = MIN_ROWS,
) -> tuple[tuple[ExtractedTable, ...], tuple[dict[str, object], ...]]:
    """Extract tables from one page, preferring the structural detector."""

    tables: list[ExtractedTable] = []
    if page.tables:
        for position, detected in enumerate(page.tables):
            if len(detected.rows) < min_rows or len(detected.rows[0]) < min_columns:
                continue
            matrix = [list(row) for row in detected.rows]
            lines = ["\t".join(row) for row in matrix]
            tables.append(
                build_table(
                    matrix,
                    source_id=source_id,
                    table_id=f"{table_id_prefix}:p{page.page_number}:t{position}",
                    table_index=position,
                    page=page.page_number,
                    path=path,
                    lines=lines,
                    first=0,
                    last=len(matrix) - 1,
                    columns_from="find_tables",
                    confidence_score=PDF_STRUCTURED_CONFIDENCE,
                    warning=PDF_TEXT_WARNING,
                    extractor="jw.scientific_data.pdf_layout.tables_from_layout_page",
                    caption=_caption_above(page.lines, detected.bbox[1]),
                    extra_locator={
                        "detector": "find_tables",
                        "bbox": list(detected.bbox),
                    },
                )
            )
        if tables:
            return tuple(tables), ()

    gap = _column_gap(page.lines)
    blocks, rejected = _blocks_from_lines(
        page.lines, gap, min_columns=min_columns, min_rows=min_rows
    )
    line_text = [line.text for line in page.lines]
    for position, (first, last, rows) in enumerate(blocks):
        tables.append(
            build_table(
                rows,
                source_id=source_id,
                table_id=f"{table_id_prefix}:p{page.page_number}:t{position}",
                table_index=position,
                page=page.page_number,
                path=path,
                lines=line_text,
                first=first,
                last=last,
                columns_from="word_geometry",
                confidence_score=PDF_LAYOUT_CONFIDENCE,
                warning=PDF_TEXT_WARNING,
                extractor="jw.scientific_data.pdf_layout.tables_from_layout_page",
                extra_locator={
                    "detector": "word_geometry",
                    "column_gap": round(gap, 2),
                },
            )
        )
    return tuple(tables), tuple(rejected)
