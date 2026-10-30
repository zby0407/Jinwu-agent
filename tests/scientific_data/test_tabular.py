"""Tests for jw.scientific_data.tabular."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from jw.scientific_data.tabular import (
    decode_bytes,
    flatten_nested,
    read_json_records,
    read_table,
    read_text_file,
    sniff_table,
)

SN_CSV = (
    "# SN 2011fe light curve\n"
    "# B band, photometry only\n"
    "MJD,B (mag),e_B (mag),Observer\n"
    "54900.5,10.02,0.03,KAIT\n"
    "54901.5,9.98,0.02,KAIT\n"
)


def _write(directory: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = directory / name
    path.write_text(text, encoding=encoding)
    return path


class TestEncoding:
    def test_bom_is_detected_and_stripped(self):
        text, encoding, warnings = decode_bytes("\ufeffa,b\n1,2\n".encode("utf-8"))
        assert encoding == "utf-8-sig"
        assert text.startswith("a,b")
        assert "byte_order_mark:utf-8-sig" in warnings

    def test_gb18030_is_preferred_over_low_byte_fallbacks(self):
        text, encoding, warnings = decode_bytes(
            "星等;时间\n10.1;54900\n".encode("gb18030")
        )
        assert encoding == "gb18030"
        assert text.startswith("星等")
        assert "encoding_suspect:latin-1" not in warnings

    def test_high_byte_fallback_is_flagged(self):
        # 0xE9 alone is invalid utf-8 and invalid gb18030 -> cp1252 wins
        _text, encoding, warnings = decode_bytes(b"caf\xe9,1\n")
        assert encoding in {"cp1252", "latin-1"}
        assert f"encoding_suspect:{encoding}" in warnings

    def test_gzip_is_transparent_and_reported(self, tmp_path: Path):
        path = tmp_path / "flux.csv.gz"
        path.write_bytes(gzip.compress(b"x,y\n1,2\n"))
        text, _encoding, _warnings = read_text_file(path)
        assert text == "x,y\n1,2\n"
        (table,) = read_table(path, source_id="s1")
        assert table.columns == ("x", "y")
        assert table.format == "csv"


class TestSniff:
    def test_delimiter_columns_and_caption(self, tmp_path: Path):
        path = _write(tmp_path, "sn.csv", SN_CSV)
        profile = sniff_table(path)
        assert profile.delimiter == ","
        assert profile.columns == ("MJD", "B (mag)", "e_B (mag)", "Observer")
        assert profile.header_row == 2
        assert profile.row_count == 2
        assert profile.extra["caption"].startswith("SN 2011fe light curve")
        assert profile.numeric_share == pytest.approx(0.75)
        assert profile.confidence_score > 0.8

    def test_semicolon_and_tab_delimiters(self, tmp_path: Path):
        semi = _write(tmp_path, "semi.csv", "a;b;c\n1;2;3\n4;5;6\n")
        assert sniff_table(semi).delimiter == ";"
        tabbed = _write(tmp_path, "tab.tsv", "a\tb\tc\n1\t2\t3\n")
        assert sniff_table(tabbed).delimiter == "\t"

    def test_comma_inside_quoted_field_does_not_win_over_semicolon(
        self, tmp_path: Path
    ):
        path = _write(tmp_path, "mixed.csv", 'a;b\n"x, y";2\n"p, q";3\n')
        assert sniff_table(path).delimiter == ";"

    def test_single_column_file_is_flagged(self, tmp_path: Path):
        path = _write(tmp_path, "one.csv", "value\n1\n2\n")
        profile = sniff_table(path)
        assert profile.field_count < 2
        assert "single_column_table" in profile.notes


class TestReadTable:
    def test_values_stay_raw(self, tmp_path: Path):
        path = _write(
            tmp_path,
            "raw.csv",
            'id,flux,note\n1,1,234.5,"1,200"\n2,1.2e3,-9.533\n',
        )
        (table,) = read_table(path, source_id="src")
        assert table.rows[0]["flux"] == "1,234.5" or table.rows[0]["flux"] == "1"
        # numbers are never coerced here; the alignment stage decides
        assert isinstance(table.rows[1]["flux"], str)
        assert table.rows[1]["flux"] == "1.2e3"

    def test_provenance_points_at_source_lines(self, tmp_path: Path):
        path = _write(tmp_path, "sn.csv", SN_CSV)
        (table,) = read_table(path, source_id="src-sn")
        assert table.provenance.source_id == "src-sn"
        assert table.provenance.locator["header_row"] == 3
        assert table.provenance.locator["line_start"] == 4
        assert table.provenance.locator["encoding"] == "utf-8"
        assert table.provenance.confidence_score > 0.5
        assert table.caption.startswith("SN 2011fe")
        assert table.rows[0]["_row_index"] == 4

    def test_ragged_and_overflowing_rows_are_reported(self, tmp_path: Path):
        path = _write(tmp_path, "ragged.csv", "a,b,c\n1,2,3\n4,5\n6,7,8,9\n")
        (table,) = read_table(path, source_id="src")
        assert table.parse_warnings.count("ragged_rows:1") == 1
        assert "extra_columns:1" in table.parse_warnings
        assert table.rows[1]["c"] == ""
        assert table.rows[2]["extra_1"] == "9"

    def test_duplicate_columns_are_suffixed(self, tmp_path: Path):
        path = _write(tmp_path, "dup.csv", "mag,mag,mag\n1,2,3\n")
        (table,) = read_table(path, source_id="src")
        assert table.columns == ("mag", "mag#2", "mag#3")
        assert "duplicate_column:mag" in table.parse_warnings

    def test_missing_header_yields_generated_names(self, tmp_path: Path):
        path = _write(tmp_path, "nohead.csv", "1,2,3\n4,5,6\n")
        (table,) = read_table(path, source_id="src")
        assert table.columns == ("column_1", "column_2", "column_3")
        assert table.header_row == -1
        assert "no_header_row" in table.parse_warnings

    def test_quoted_field_spanning_lines(self, tmp_path: Path):
        path = _write(
            tmp_path, "multi.csv", 'id,note,val\n1,"line one\nline two",5\n2,x,6\n'
        )
        (table,) = read_table(path, source_id="src")
        assert len(table.rows) == 2
        assert table.rows[0]["note"] == "line one\nline two"
        assert table.rows[1]["val"] == "6"

    def test_truncated_final_line_is_flagged(self, tmp_path: Path):
        path = _write(tmp_path, "cut.csv", 'a,b\n1,2\n3,"unterminated\n')
        (table,) = read_table(path, source_id="src")
        assert "possible_truncation" in table.parse_warnings

    def test_empty_file_is_not_an_error(self, tmp_path: Path):
        path = _write(tmp_path, "empty.csv", "")
        (table,) = read_table(path, source_id="src")
        assert table.rows == ()
        assert "empty_file" in table.parse_warnings

    def test_header_only_table(self, tmp_path: Path):
        path = _write(tmp_path, "header.csv", "only,header\n")
        (table,) = read_table(path, source_id="src")
        assert table.columns == ("only", "header")
        assert table.rows == ()

    def test_row_limit(self, tmp_path: Path):
        path = _write(
            tmp_path, "many.csv", "a,b\n" + "".join(f"{i},{i}\n" for i in range(10))
        )
        (table,) = read_table(path, source_id="src", max_rows=3)
        assert len(table.rows) == 3
        assert "row_limit:3" in table.parse_warnings

    def test_forced_delimiter_and_encoding(self, tmp_path: Path):
        path = tmp_path / "cn.csv"
        path.write_bytes("星等|时间\n10.1|54900\n".encode("gb18030"))
        (table,) = read_table(path, source_id="src", encoding="gb18030", delimiter="|")
        assert table.columns == ("星等", "时间")
        assert table.rows[0]["星等"] == "10.1"


class TestJson:
    def test_records_path_and_nested_flattening(self):
        payload = {
            "results": [
                {
                    "rows": [
                        {"id": 1, "meta": {"band": "B"}, "tags": ["a", "b"]},
                        {"id": 2, "meta": {"band": "V"}, "tags": []},
                    ]
                }
            ]
        }
        table = read_json_records(
            payload, source_id="api", records_path="results[0].rows"
        )
        assert "meta_band" in table.columns
        assert table.rows[0]["meta_band"] == "B"
        assert table.rows[0]["tags"] == "a; b"
        assert table.rows[1]["tags"] == ""
        assert table.format == "json"

    def test_bad_records_path_warns_instead_of_raising(self):
        table = read_json_records(
            {"a": 1}, source_id="api", records_path="missing.path"
        )
        assert table.rows == ()
        assert any(w.startswith("records_path_not_found") for w in table.parse_warnings)

    def test_json_file_and_jsonl_file(self, tmp_path: Path):
        path = _write(tmp_path, "r.json", json.dumps({"data": [{"x": 1}, {"x": 2}]}))
        (table,) = read_table(
            path,
            source_id="s",
        )
        assert "data" in table.columns
        lines = _write(tmp_path, "r.jsonl", '{"x": 1}\n{"x": 2}\n')
        (table,) = read_table(lines, source_id="s")
        assert len(table.rows) == 2

    def test_flatten_nested_keeps_scalar_types(self):
        flat = flatten_nested({"a": 1, "b": {"c": 2.5}, "d": [1, 2]})
        assert flat == {"a": 1, "b_c": 2.5, "d": "1; 2"}
