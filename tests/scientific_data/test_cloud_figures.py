"""Cloud chart reading is opt-in, cached and parsed from model text."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from jw.scientific_data.cloud_figures import (
    CLOUD_FIGURE_WARNING,
    CloudFigureConfig,
    CloudFigureError,
    extract_cloud_figures,
    resolve_figure_config,
)
from jw.scientific_data.contracts import ConfidenceBand
from jw.scientific_data.documents import read_pdf_report
from jw.scientific_data.pdf_layout import load_pdf_library

CHART_MARKDOWN = """```markdown
| series | x | y |
| --- | --- | --- |
| V | 53520 | 15.10 |
| V | 53522 | 14.80 |
| V | 53524 | 14.60 |
```"""

LONG_CAPTION = (
    "Figure 1. Optical photometry of the transient from the published table. "
    "The light curve combines the discovery epoch with the follow-up campaign "
    "and covers the first three months after explosion; the dashed line marks "
    "the epoch of maximum light and the open symbols are the unfiltered "
    "measurements reported by the survey."
)


def _config(tmp_path: Path, **overrides: object) -> CloudFigureConfig:
    values: dict[str, object] = {
        "provider": "generic",
        "endpoint": f"https://charts.example/v1/{uuid.uuid4()}",
        "api_key": "key-abc",
        "model": "vision-test",
        "cache_dir": str(tmp_path / "cache"),
    }
    values.update(overrides)
    return CloudFigureConfig(**values)  # type: ignore[arg-type]


def _opener(payload: object, calls: list[str] | None = None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def opener(request, timeout):
        if calls is not None:
            calls.append(request.full_url)
        return body

    return opener


def _image_pdf(path: Path, *, caption: str = LONG_CAPTION) -> Path:
    fitz = load_pdf_library()
    if fitz is None:
        pytest.skip("pymupdf is not installed")
    document = fitz.open()
    page = document.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
    page.insert_image(fitz.Rect(40, 110, 300, 370), pixmap=pixmap)
    if caption:
        page.insert_textbox(fitz.Rect(40, 40, 560, 100), caption, fontsize=8)
    document.save(path)
    document.close()
    return path


def test_markdown_answer_becomes_a_table(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    tables, rejected, warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=_config(tmp_path),
        allow_network=True,
        opener=_opener({"text": CHART_MARKDOWN}),
    )

    assert rejected == ()
    assert warnings == ()
    assert len(tables) == 1
    table = tables[0]
    assert table.table_id == "pdf:p1:fig0"
    assert table.columns == ("series", "x", "y")
    assert len(table.rows) == 3
    assert table.rows[0]["y"] == "15.10"
    assert table.format == "pdf_cloud_figure"
    assert table.confidence == ConfidenceBand.LOW
    assert table.provenance.locator["columns_from"] == "cloud_figure"
    assert table.provenance.locator["cache_hit"] is False
    assert CLOUD_FIGURE_WARNING in table.parse_warnings


def test_chart_replays_offline_from_cache(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    config = _config(tmp_path)
    first, _rejected, _warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=config,
        allow_network=True,
        opener=_opener({"text": CHART_MARKDOWN}),
    )

    def forbidden(request, timeout):
        raise AssertionError(f"offline replay must not call {request.full_url}")

    second, _rejected, warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=config,
        allow_network=False,
        opener=forbidden,
    )

    assert warnings == ()
    assert len(first) == len(second) == 1
    assert second[0].rows == first[0].rows
    assert second[0].provenance.locator["cache_hit"] is True


def test_network_is_refused_before_any_call(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    calls: list[str] = []

    tables, _rejected, warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=_config(tmp_path),
        allow_network=False,
        opener=_opener({"text": CHART_MARKDOWN}, calls),
    )

    assert tables == ()
    assert warnings == ("cloud_figure_network_disabled",)
    assert calls == []


def test_unconfigured_adapter_refuses_instead_of_raising(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    tables, _rejected, warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=CloudFigureConfig(provider="generic", cache_dir=str(tmp_path)),
        allow_network=True,
    )
    assert tables == ()
    assert warnings == ("cloud_figure_not_configured",)


def test_error_payload_and_transport_failure_become_warnings(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    _tables, _rejected, quota = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=_config(tmp_path),
        allow_network=True,
        opener=_opener({"error_code": "quota_exceeded", "error_msg": "daily limit"}),
    )
    assert quota == ("cloud_figure_failed:quota_exceeded",)

    def broken(request, timeout):
        raise OSError("connection reset")

    _tables, _rejected, failed = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=_config(tmp_path, cache_dir=str(tmp_path / "other")),
        allow_network=True,
        opener=broken,
    )
    assert failed == ("cloud_figure_failed:transport_error",)


def test_openai_style_content_is_parsed(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    tables, _rejected, _warnings = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=_config(tmp_path),
        allow_network=True,
        opener=_opener(
            {
                "choices": [
                    {"message": {"content": "series\tx\ty\nV\t1\t2.5\nV\t2\t2.1"}}
                ]
            }
        ),
    )
    assert len(tables) == 1
    assert tables[0].columns == ("series", "x", "y")
    assert len(tables[0].rows) == 2


def test_empty_answer_is_cached_for_offline_replay(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    config = _config(tmp_path)
    _tables, _rejected, first = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=config,
        allow_network=True,
        opener=_opener({"text": ""}),
    )
    assert first == ("cloud_figure_empty",)

    def forbidden(request, timeout):
        raise AssertionError("the cached empty answer must be replayed offline")

    _tables, _rejected, second = extract_cloud_figures(
        pdf,
        source_id="src:pdf:1",
        pages=[1],
        config=config,
        allow_network=False,
        opener=forbidden,
    )
    assert second == ("cloud_figure_empty",)


def test_read_pdf_report_uses_the_cloud_for_chart_pages(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    tables, rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
        figures=True,
        figure_config=_config(tmp_path),
        figure_opener=_opener({"text": CHART_MARKDOWN}),
    )

    assert rejected == ()
    assert len(tables) == 1
    assert tables[0].table_id == "pdf:p1:fig0"
    assert tables[0].format == "pdf_cloud_figure"
    assert CLOUD_FIGURE_WARNING in tables[0].parse_warnings
    assert "no_text_layer" not in warnings


def test_chart_pages_are_skipped_without_the_opt_in(tmp_path):
    pdf = _image_pdf(tmp_path / "chart.pdf")
    tables, rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
    )
    assert tables == ()
    assert rejected == ()
    assert warnings == ()


def test_a_scan_without_text_stays_on_the_table_path(tmp_path):
    pdf = _image_pdf(tmp_path / "scan.pdf", caption="")
    calls: list[str] = []
    tables, _rejected, warnings = read_pdf_report(
        pdf,
        source_id="src:pdf:1",
        allow_network=True,
        figures=True,
        figure_config=_config(tmp_path),
        figure_opener=_opener({"text": CHART_MARKDOWN}, calls),
    )
    assert tables == ()
    assert calls == []
    assert "no_text_layer" in warnings
    assert "cloud_ocr_not_configured" in warnings


def test_figure_error_codes_are_preserved():
    error = CloudFigureError("quota_exceeded", "daily limit")
    assert error.code == "quota_exceeded"
    assert "daily limit" in str(error)


def test_environment_beats_saved_settings():
    config = SimpleNamespace(
        figure_cloud_provider="",
        figure_cloud_endpoint="",
        figure_cloud_api_key="",
        figure_cloud_model="",
    )
    resolved = resolve_figure_config(
        config=config,
        environ={
            "JW_FIGURE_CLOUD_ENDPOINT": "https://env.example/chart",
            "JW_FIGURE_CLOUD_API_KEY": "env-key",
            "JW_FIGURE_CLOUD_MODEL": "env-model",
        },
    )
    assert resolved.provider == "generic"
    assert resolved.endpoint == "https://env.example/chart"
    assert resolved.api_key == "env-key"
    assert resolved.model == "env-model"
