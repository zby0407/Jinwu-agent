"""PyMuPDF geometry is the primary PDF reader; these tests pin its contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from jw.scientific_data.contracts import ConfidenceBand
from jw.scientific_data.documents import read_pdf_tables, read_pdf_text
from jw.scientific_data.pdf_layout import (
    PDF_LAYOUT_CONFIDENCE,
    PDF_TEXT_WARNING,
    load_pdf_library,
    read_pdf_layout,
    tables_from_layout_page,
)

TABLE_LINES = [
    "Table 2: Photometry of SN 2011fe",
    "object      time     filter  magnitude",
    "SN 2011fe   55800.0  B       15.30",
    "SN 2011fe   55802.0  B       15.00",
    "SN 2011fe   55804.0  B       14.90",
]


def _write_pdf(path: Path, lines: list[str]) -> Path:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if load_pdf_library() is None:
        pytest.skip("pymupdf is not installed")
    figure = plt.figure(figsize=(8.27, 11.69))
    position = 0.9
    for line in lines:
        figure.text(0.05, position, line, fontsize=9, family="monospace")
        position -= 0.03
    figure.savefig(path)
    plt.close(figure)
    return path


def _write_scrambled_pdf(path: Path) -> Path:
    """A page whose content stream draws columns out of visual order."""

    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if load_pdf_library() is None:
        pytest.skip("pymupdf is not installed")
    figure = plt.figure(figsize=(8.27, 11.69))
    figure.text(
        0.08,
        0.95,
        "object        time       filter  magnitude",
        fontsize=9,
        family="monospace",
    )
    rows = [
        ("SN 2011fe", "55800.0", "B", "15.30"),
        ("SN 2011fe", "55802.0", "B", "15.00"),
        ("SN 2011fe", "55804.0", "B", "14.90"),
    ]
    y = 0.90
    for _object, _time, _filter, magnitude in rows:
        figure.text(0.70, y, magnitude, fontsize=9, family="monospace")
        y -= 0.03
    y = 0.90
    for object_name, time, band, _magnitude in rows:
        figure.text(0.08, y, object_name, fontsize=9, family="monospace")
        figure.text(0.30, y, time, fontsize=9, family="monospace")
        figure.text(0.50, y, band, fontsize=9, family="monospace")
        y -= 0.03
    figure.savefig(path)
    plt.close(figure)
    return path


def _write_ruled_pdf(path: Path) -> Path:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if load_pdf_library() is None:
        pytest.skip("pymupdf is not installed")
    figure, axes = plt.subplots(figsize=(8, 3))
    axes.axis("off")
    table = axes.table(
        cellText=[
            ["object", "time", "filter", "magnitude"],
            ["SN 2011fe", "55800.0", "B", "15.30"],
            ["SN 2011fe", "55802.0", "B", "15.00"],
            ["SN 2011fe", "55804.0", "B", "14.90"],
        ],
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    figure.savefig(path)
    plt.close(figure)
    return path


def test_text_layer_is_read_with_geometry(tmp_path):
    pdf = _write_pdf(tmp_path / "paper.pdf", TABLE_LINES)
    document = read_pdf_layout(pdf)
    assert document.engine == "pymupdf"
    assert len(document.pages) == 1
    assert "object time filter magnitude" in document.pages[0].text


def test_borderless_table_is_recovered_from_word_gaps(tmp_path):
    pdf = _write_pdf(tmp_path / "paper.pdf", TABLE_LINES)
    document = read_pdf_layout(pdf)
    tables, rejected = tables_from_layout_page(
        document.pages[0], source_id="src:pdf:1", path=str(pdf)
    )

    assert rejected == ()
    assert len(tables) == 1
    table = tables[0]
    assert table.columns == ("object", "time", "filter", "magnitude")
    assert table.row_count == 3
    assert table.caption.startswith("Table 2")
    assert PDF_TEXT_WARNING in table.parse_warnings
    assert table.confidence is ConfidenceBand.MEDIUM
    locator = table.provenance.locator
    assert locator["columns_from"] == "word_geometry"
    assert locator["detector"] == "word_geometry"
    assert locator["page"] == 1
    assert table.provenance.confidence_score == PDF_LAYOUT_CONFIDENCE
    assert [row["magnitude"] for row in table.rows] == ["15.30", "15.00", "14.90"]


def test_scrambled_text_order_is_rebuilt_from_geometry(tmp_path):
    pdf = _write_scrambled_pdf(tmp_path / "scrambled.pdf")
    tables = read_pdf_tables(pdf, source_id="src:pdf:scrambled")

    # pypdf reads the magnitude column as its own block before the labels;
    # visual-line clustering puts the cells back in their rows.
    assert len(tables) == 1
    table = tables[0]
    assert table.columns == ("object", "time", "filter", "magnitude")
    assert table.row_count == 3
    assert table.provenance.locator["columns_from"] == "word_geometry"
    assert [row["magnitude"] for row in table.rows] == ["15.30", "15.00", "14.90"]


def test_ruled_table_is_read_by_the_structural_detector(tmp_path):
    pdf = _write_ruled_pdf(tmp_path / "ruled.pdf")
    tables = read_pdf_tables(pdf, source_id="src:pdf:ruled")

    # The plain whitespace reader sees five tokens per line ("SN" / "2011fe")
    # and loses the header; the ruled grid keeps all four columns.
    assert len(tables) == 1
    table = tables[0]
    assert table.columns == ("object", "time", "filter", "magnitude")
    assert table.row_count == 3
    assert table.provenance.locator["columns_from"] == "find_tables"
    assert [row["magnitude"] for row in table.rows] == ["15.30", "15.00", "14.90"]


def test_numeric_first_row_becomes_synthetic_columns(tmp_path):
    pdf = _write_pdf(
        tmp_path / "paper.pdf",
        [
            "15.10  0.05  14.80",
            "15.20  0.05  14.70",
            "15.30  0.05  14.60",
        ],
    )
    tables, _rejected = tables_from_layout_page(
        read_pdf_layout(pdf).pages[0], source_id="src:pdf:1", path=str(pdf)
    )
    assert tables[0].columns == ("column_1", "column_2", "column_3")
    assert "no_header_row" in tables[0].parse_warnings
    # The first row is data, not a header, so it must be kept.
    assert tables[0].row_count == 3


def test_prose_is_not_turned_into_a_table(tmp_path):
    pdf = _write_pdf(
        tmp_path / "paper.pdf",
        [
            "1 Introduction",
            "We present optical photometry of SN 2005cf obtained at Xinglong",
            "between April and July using the 0.8 m telescope and a V filter.",
        ],
    )
    tables, _rejected = tables_from_layout_page(
        read_pdf_layout(pdf).pages[0], source_id="src:pdf:1", path=str(pdf)
    )
    assert tables == ()


def test_text_reader_uses_the_geometry_engine(tmp_path):
    pdf = _write_pdf(tmp_path / "paper.pdf", TABLE_LINES)
    document = read_pdf_text(pdf)
    assert document.engine == "pymupdf"
    assert document.pages
    assert "SN 2011fe" in document.text


def test_a_broken_pdf_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot really a pdf")
    document = read_pdf_layout(broken)
    assert document.pages == ()
    assert any(
        warning.startswith(("unreadable_pdf", "pymupdf_not_installed"))
        for warning in document.warnings
    )
