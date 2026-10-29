"""PDF tables are reconstructed guesses, so the code must say so out loud."""

from __future__ import annotations

from pathlib import Path

import pytest

from jw.scientific_data.contracts import ConfidenceBand
from jw.scientific_data.documents import (
    PDF_TEXT_WARNING,
    read_pdf_tables,
    read_pdf_text,
    tables_from_text,
)
from jw.scientific_data.workflow import (
    WorkflowOptions,
    contract_from_request,
    run_integration,
)

PROSE = """\
1 Introduction
We present optical photometry of SN 2005cf obtained at the Xinglong station
between 2005 April and 2005 July using the 0.8 m telescope and a Johnson V
filter. The data were reduced with standard IRAF routines.
"""

TABLE_PAGE = """\
2 Observations

Table 1: Photometry of SN 2005cf
name        mjd      band   mag
SN 2005cf   53520.0  V      15.10
SN 2005cf   53522.0  V      14.80
SN 2005cf   53524.0  V      14.60

The light curve peaks near MJD 53520 with V = 14.6 mag.
"""


def test_prose_is_not_mistaken_for_a_table():
    assert tables_from_text(PROSE, source_id="src:pdf:1") == ()


def test_one_whitespace_block_becomes_one_low_confidence_table():
    tables = tables_from_text(TABLE_PAGE, source_id="src:pdf:1", path="paper.pdf")
    assert len(tables) == 1
    table = tables[0]
    assert table.columns == ("name", "mjd", "band", "mag")
    assert table.row_count == 3
    assert table.caption.startswith("Table 1")
    assert PDF_TEXT_WARNING in table.parse_warnings
    assert table.confidence is ConfidenceBand.LOW
    assert table.provenance is not None
    assert table.provenance.locator["page"] == 0
    assert table.provenance.locator["columns_from"] == "whitespace_runs"


def test_wrapped_paragraph_breaks_the_block():
    text = (
        "name        mjd      band   mag\n"
        "SN 2005cf   53520.0  V      15.10\n"
        "SN 2005cf   53522.0  V      14.80\n"
        "SN 2005cf   53524.0  V      14.60\n"
        "a line of prose that is definitely not a table row at all, no\n"
        "name        mjd      band   mag\n"
        "SN 2011fe   55800.0  B      15.30\n"
        "SN 2011fe   55802.0  B      15.00\n"
        "SN 2011fe   55804.0  B      14.90\n"
    )
    tables = tables_from_text(text, source_id="src:pdf:1")
    assert len(tables) == 2


def test_duplicate_header_names_are_disambiguated():
    text = (
        "mag      err      mag      err\n"
        "15.10    0.05     14.80    0.04\n"
        "15.20    0.05     14.70    0.04\n"
        "15.30    0.05     14.60    0.04\n"
    )
    tables = tables_from_text(text, source_id="src:pdf:1")
    assert tables[0].columns == ("mag", "err", "mag#2", "err#2")


def test_rows_keep_the_page_line_number():
    tables = tables_from_text(TABLE_PAGE, source_id="src:pdf:1")
    rows = list(tables[0].rows)
    assert rows[0]["_row_index"] == 5
    assert rows[-1]["_row_index"] == 7


def test_missing_text_layer_is_reported(tmp_path):
    empty = tmp_path / "blank.pdf"
    empty.write_bytes(b"%PDF-1.4\n")
    document = read_pdf_text(empty)
    assert document.warnings


def _write_pdf(path: Path, lines: list[str]) -> Path:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(8.27, 11.69))
    position = 0.9
    for line in lines:
        figure.text(0.05, position, line, fontsize=9, family="monospace")
        position -= 0.03
    figure.savefig(path)
    plt.close(figure)
    return path


def test_real_pdf_yields_the_published_table(tmp_path):
    pdf = _write_pdf(
        tmp_path / "paper.pdf",
        [
            "Table 2: Photometry of SN 2011fe",
            "object      time     filter  magnitude",
            "SN 2011fe   55800.0  B       15.30",
            "SN 2011fe   55802.0  B       15.00",
            "SN 2011fe   55804.0  B       14.90",
        ],
    )
    tables = read_pdf_tables(pdf, source_id="src:pdf:real")
    assert len(tables) == 1
    table = tables[0]
    assert table.columns == ("object", "time", "filter", "magnitude")
    assert table.row_count == 3
    values = [row["magnitude"] for row in table.rows]
    assert values == ["15.30", "15.00", "14.90"]


def test_a_pdf_takes_part_in_a_full_run(tmp_path):
    pdf = _write_pdf(
        tmp_path / "paper.pdf",
        [
            "Table 2: Photometry of SN 2011fe",
            "object      time     filter  magnitude",
            "SN 2011fe   55800.0  B       15.30",
            "SN 2011fe   55802.0  B       15.00",
            "SN 2011fe   55804.0  B       14.90",
        ],
    )
    contract = contract_from_request(
        {
            "research_goal": "Assemble a B band light curve from a paper table",
            "domain": "snia_light_curve",
            "observables": ["apparent_magnitude", "time", "object", "band"],
            "entity_class": "supernova",
        }
    )
    result = run_integration(
        workspace=tmp_path / "run",
        contract=contract,
        inputs=(str(pdf),),
        options=WorkflowOptions(write_package_output=True),
        run_id="pdf-run",
    )
    assert result.ledger.tables
    table = next(iter(result.ledger.tables.values()))
    assert PDF_TEXT_WARNING in table.parse_warnings
    magnitudes = {
        record.observable: record.value
        for record in result.records
        if record.observable == "apparent_magnitude"
    }
    assert magnitudes  # the mapping layer accepted the reconstructed columns
