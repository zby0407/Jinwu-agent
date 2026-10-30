"""Tests for the HTML and Markdown table readers."""

from __future__ import annotations

from pathlib import Path

from jw.scientific_data.html_tables import (
    collect_html_tables,
    read_html_file,
    read_html_tables,
)
from jw.scientific_data.markdown_tables import (
    collect_markdown_tables,
    read_markdown_file,
    read_markdown_tables,
)

PHOTOMETRY_HTML = """
<html><body>
<h2>Table 3. Photometry of SN 2011fe</h2>
<table>
  <caption>B-band photometry</caption>
  <tr><th>MJD</th><th>B</th><th>e_B</th></tr>
  <tr><td>54900.5</td><td>10.02</td><td>0.03</td></tr>
  <tr><td>54901.5</td><td> 9.98 </td><td>0.02</td></tr>
</table>
</body></html>
"""

SPAN_HTML = """
<table>
  <tr><th rowspan="2">ID</th><th colspan="2">Flux</th></tr>
  <tr><th>value</th><th>err</th></tr>
  <tr><td>A1</td><td>1.5</td><td>0.1</td></tr>
</table>
"""


class TestHtmlTables:
    def test_caption_heading_and_rows(self):
        tables = read_html_tables(PHOTOMETRY_HTML, source_id="src")
        assert len(tables) == 1
        table = tables[0]
        assert table.columns == ("MJD", "B", "e_B")
        assert table.caption == "B-band photometry"
        assert table.provenance.locator["heading"] == "Table 3. Photometry of SN 2011fe"
        assert table.rows[1]["B"] == "9.98"  # surrounding whitespace collapsed
        assert table.header_row == 0
        assert table.format == "html"
        assert table.provenance.locator["line_start"] > 0

    def test_rowspan_and_colspan_do_not_shift_columns(self):
        (table,) = read_html_tables(SPAN_HTML, source_id="src")
        assert table.columns == ("ID", "value", "err")
        assert table.rows[0]["ID"] == "A1"
        assert table.rows[0]["value"] == "1.5"
        assert table.rows[0]["err"] == "0.1"

    def test_tables_inside_scripts_are_ignored(self):
        html = (
            '<script>var t = "<table><tr><td>1</td></tr></table>";</script>'
            + PHOTOMETRY_HTML
        )
        assert len(collect_html_tables(html)) == 1

    def test_multiple_tables_are_indexed(self):
        html = PHOTOMETRY_HTML + PHOTOMETRY_HTML
        tables = read_html_tables(html, source_id="src")
        assert [table.table_id for table in tables] == ["html_000", "html_001"]

    def test_headerless_table_gets_generated_columns(self):
        (table,) = read_html_tables(
            "<table><tr><td>1</td><td>2</td></tr></table>", source_id="s"
        )
        assert table.columns == ("column_1", "column_2")
        assert "no_header_row" in table.parse_warnings

    def test_empty_table_is_not_an_error(self):
        (table,) = read_html_tables("<table></table>", source_id="s")
        assert table.rows == ()
        assert "empty_table" in table.parse_warnings

    def test_read_html_file_with_gbk_bytes(self, tmp_path: Path):
        path = tmp_path / "page.html"
        path.write_bytes(
            "<table><tr><th>星等</th></tr><tr><td>10.1</td></tr></table>".encode(
                "gb18030"
            )
        )
        (table,) = read_html_file(path, source_id="s")
        assert table.columns == ("星等",)
        assert table.rows[0]["星等"] == "10.1"


class TestMarkdownTables:
    MD = """# Photometry

Table 2: B-band light curve of SN 2011fe

| MJD | B (mag) | e_B |
|----:|--------:|----:|
| 54900.5 | 10.02 | 0.03 |
| 54901.5 | 9.98 | 0.02 |
"""

    def test_caption_alignment_and_rows(self):
        (table,) = read_markdown_tables(self.MD, source_id="src")
        assert table.columns == ("MJD", "B (mag)", "e_B")
        assert table.caption == "B-band light curve of SN 2011fe"
        assert table.provenance.locator["alignments"] == ["right", "right", "right"]
        assert table.provenance.locator["heading"] == "Photometry"
        assert table.rows[0]["MJD"] == "54900.5"
        assert table.format == "markdown"

    def test_fenced_code_block_is_not_a_table(self):
        md = self.MD + "\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n"
        assert len(collect_markdown_tables(md)) == 1

    def test_escaped_pipe_and_ragged_row(self):
        md = "| id | note |\n|----|------|\n| 1 | a \\| b |\n| 2 |\n"
        (table,) = read_markdown_tables(md, source_id="s")
        assert table.rows[0]["note"] == "a | b"
        assert "ragged_rows" in table.parse_warnings

    def test_header_only_table_is_flagged(self):
        (table,) = read_markdown_tables("| a | b |\n|---|---|\n", source_id="s")
        assert table.rows == ()
        assert "header_only_table" in table.parse_warnings

    def test_read_markdown_file(self, tmp_path: Path):
        path = tmp_path / "supp.md"
        path.write_text(self.MD, encoding="utf-8")
        (table,) = read_markdown_file(path, source_id="s")
        assert len(table.rows) == 2
        assert table.provenance.locator["path"].endswith("supp.md")
