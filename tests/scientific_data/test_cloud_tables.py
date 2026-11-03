"""The cloud adapter is an explicit, cached, failure-tolerant escalation."""

from __future__ import annotations

import json
import urllib.error
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from jw.scientific_data.cloud_tables import (
    CLOUD_OCR_WARNING,
    CloudTableConfig,
    CloudTableError,
    extract_cloud_tables,
    resolve_cloud_config,
)
from jw.scientific_data.contracts import ConfidenceBand
from jw.scientific_data.documents import read_pdf_report
from jw.scientific_data.pdf_layout import load_pdf_library

GRID = [
    ("name", "mjd", "band", "mag"),
    ("SN 2005cf", "53520.0", "V", "15.10"),
    ("SN 2005cf", "53522.0", "V", "14.80"),
    ("SN 2005cf", "53524.0", "V", "14.60"),
]


def _blank_pdf(path: Path) -> Path:
    if load_pdf_library() is None:
        pytest.skip("pymupdf is not installed")
    fitz = load_pdf_library()
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()
    return path


def _baidu_payload() -> dict[str, object]:
    body = [
        {
            "row_start": row,
            "row_end": row,
            "col_start": column,
            "col_end": column,
            "words": text,
        }
        for row, values in enumerate(GRID)
        for column, text in enumerate(values)
    ]
    return {
        "log_id": 1,
        "table_num": 1,
        "tables_result": [
            {"header": [{"words": "Table 4: Photometry"}], "body": body, "footer": []}
        ],
    }


def _config(tmp_path: Path, **overrides: object) -> CloudTableConfig:
    values: dict[str, object] = {
        "provider": "baidu",
        "client_id": f"id-{uuid.uuid4()}",
        "client_secret": "secret",
        "cache_dir": str(tmp_path / "cache"),
    }
    values.update(overrides)
    return CloudTableConfig(**values)  # type: ignore[arg-type]


def _baidu_opener(calls: list[str] | None = None):
    def opener(request, timeout):
        if calls is not None:
            calls.append(request.full_url)
        if "oauth" in request.full_url:
            return json.dumps(
                {"access_token": "token-abc", "expires_in": 2592000}
            ).encode()
        return json.dumps(_baidu_payload()).encode()

    return opener


def test_environment_beats_saved_settings_and_provider_is_inferred():
    config = SimpleNamespace(
        pdf_cloud_provider="",
        baidu_ocr_api_key="config-id",
        baidu_ocr_secret_key="config-secret",
        pdf_cloud_endpoint="",
        pdf_cloud_api_key="",
    )
    resolved = resolve_cloud_config(
        config=config,
        environ={
            "BAIDU_OCR_API_KEY": "env-id",
            "BAIDU_OCR_SECRET_KEY": "env-secret",
            "JW_PDF_CLOUD_CACHE_DIR": "/tmp/pdf-cache",
        },
    )
    assert resolved.provider == "baidu"
    assert resolved.client_id == "env-id"
    assert resolved.client_secret == "env-secret"
    assert resolved.cache_dir == "/tmp/pdf-cache"
    assert resolved.enabled is True


def test_unconfigured_adapter_refuses_instead_of_raising(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    tables, rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=CloudTableConfig(),
        allow_network=True,
    )
    assert tables == ()
    assert rejected == ()
    assert warnings == ("cloud_ocr_not_configured",)


def test_baidu_round_trip_then_offline_cache_replay(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    config = _config(tmp_path)
    calls: list[str] = []
    tables, rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=True,
        opener=_baidu_opener(calls),
    )

    assert rejected == ()
    assert warnings == ()
    assert len(tables) == 1
    table = tables[0]
    assert table.table_id == "pdf:p1:c0"
    assert table.columns == ("name", "mjd", "band", "mag")
    assert table.row_count == 3
    assert table.format == "pdf_cloud_ocr"
    assert table.confidence is ConfidenceBand.LOW
    assert CLOUD_OCR_WARNING in table.parse_warnings
    assert table.provenance.locator["provider"] == "baidu"
    assert table.provenance.locator["cache_hit"] is False
    assert any("oauth" in url for url in calls)

    def forbidden(request, timeout):
        raise AssertionError("a cached run must not touch the network")

    replay, _rejected, replay_warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=False,
        opener=forbidden,
    )
    assert replay_warnings == ()
    assert list(replay[0].rows) == list(table.rows)
    assert replay[0].provenance.locator["cache_hit"] is True


def test_empty_response_is_cached_for_offline_replay(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    config = _config(tmp_path)

    def empty(request, timeout):
        if "oauth" in request.full_url:
            return json.dumps(
                {"access_token": "token-abc", "expires_in": 2592000}
            ).encode()
        return json.dumps({"log_id": 1, "table_num": 0, "tables_result": []}).encode()

    tables, _rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=True,
        opener=empty,
    )
    assert tables == ()
    assert warnings == ("cloud_ocr_empty",)

    def forbidden(request, timeout):
        raise AssertionError("an empty cached result must not touch the network")

    replay, _rejected, replay_warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=False,
        opener=forbidden,
    )
    assert replay == ()
    assert replay_warnings == ("cloud_ocr_empty",)


def test_generic_endpoint_accepts_row_matrices(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    config = CloudTableConfig(
        provider="generic",
        endpoint="https://tables.example/v1/read",
        api_key="key",
        cache_dir=str(tmp_path / "cache"),
    )

    def opener(request, timeout):
        assert request.headers["Authorization"] == "Bearer key"
        return json.dumps({"tables": [{"rows": [list(row) for row in GRID]}]}).encode()

    tables, _rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=True,
        opener=opener,
    )
    assert warnings == ()
    assert tables[0].columns == ("name", "mjd", "band", "mag")
    assert tables[0].provenance.locator["provider"] == "generic"


def test_network_is_refused_before_any_call(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    config = _config(tmp_path)

    def forbidden(request, timeout):
        raise AssertionError("allow_network=False must not call out")

    tables, _rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=config,
        allow_network=False,
        opener=forbidden,
    )
    assert tables == ()
    assert warnings == ("cloud_ocr_network_disabled",)


def test_a_transport_failure_becomes_a_warning(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")

    def broken(request, timeout):
        raise urllib.error.URLError("no route to host")

    tables, _rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=_config(tmp_path),
        allow_network=True,
        opener=broken,
    )
    assert tables == ()
    assert warnings == ("cloud_ocr_failed:transport_error",)


def test_error_payload_is_not_mistaken_for_a_table(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")

    def limited(request, timeout):
        if "oauth" in request.full_url:
            return json.dumps(
                {"access_token": "token-abc", "expires_in": 2592000}
            ).encode()
        return json.dumps(
            {"error_code": 17, "error_msg": "daily request limit reached"}
        ).encode()

    tables, _rejected, warnings = extract_cloud_tables(
        pdf,
        source_id="src:pdf:1",
        config=_config(tmp_path),
        allow_network=True,
        opener=limited,
    )
    assert tables == ()
    assert warnings == ("cloud_ocr_failed:17",)


def test_read_pdf_report_escalates_an_image_only_page(tmp_path):
    pdf = _blank_pdf(tmp_path / "scan.pdf")
    tables, rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
        cloud_config=_config(tmp_path),
        cloud_opener=_baidu_opener(),
    )

    assert rejected == ()
    assert len(tables) == 1
    assert tables[0].table_id == "pdf:p1:c0"
    assert tables[0].format == "pdf_cloud_ocr"
    assert "pdf_text_reconstruction" in tables[0].parse_warnings
    assert CLOUD_OCR_WARNING in tables[0].parse_warnings
    assert "no_text_layer" in warnings


def test_non_default_error_code_is_preserved():
    error = CloudTableError("quota_exceeded", "daily limit")
    assert error.code == "quota_exceeded"
    assert "daily limit" in str(error)


def test_short_text_without_an_image_is_not_escalated(tmp_path):
    fitz = load_pdf_library()
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A short note, but not a table.")
    pdf = tmp_path / "note.pdf"
    document.save(pdf)
    document.close()

    tables, rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
        cloud_config=_config(tmp_path),
        cloud_opener=_baidu_opener(),
    )
    assert tables == ()
    assert rejected == ()
    assert warnings == ()


def test_an_image_with_a_short_caption_is_escalated(tmp_path):
    fitz = load_pdf_library()
    document = fitz.open()
    page = document.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
    page.insert_image(fitz.Rect(40, 40, 300, 300), pixmap=pixmap)
    page.insert_text((40, 30), "Table 1: image")
    pdf = tmp_path / "image-table.pdf"
    document.save(pdf)
    document.close()

    tables, _rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
        cloud_config=_config(tmp_path),
        cloud_opener=_baidu_opener(),
    )
    assert len(tables) == 1
    assert tables[0].format == "pdf_cloud_ocr"
    assert CLOUD_OCR_WARNING in tables[0].parse_warnings
    assert "no_text_layer" not in warnings
