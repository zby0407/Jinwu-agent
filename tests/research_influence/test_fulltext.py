"""Full text must be verified, hashed and content-addressed before use."""

from __future__ import annotations

from pathlib import Path

import pytest

from jw.research_influence.access import AccessPolicy
from jw.research_influence.fulltext import (
    describe_fulltext,
    fetch_fulltext,
    read_local_fulltext,
    verify_fulltext,
)

from .conftest import FakeTransport


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory) -> Path:
    """A real PDF with a text layer, built with matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = tmp_path_factory.mktemp("pdf") / "paper.pdf"
    figure = plt.figure(figsize=(8.5, 11))
    figure.text(0.1, 0.95, "1. Introduction", fontsize=12)
    figure.text(0.1, 0.90, "We observed SN 2005cf over 60 nights.", fontsize=10)
    figure.text(0.1, 0.60, "Data availability", fontsize=12)
    figure.text(
        0.1, 0.55, "Photometry at https://zenodo.org/record/1234567", fontsize=10
    )
    figure.savefig(target)
    plt.close(figure)
    return target


def test_download_is_stored_content_addressed_and_readable(tmp_path, sample_pdf):
    """The bytes land in a hash-named file and the text layer is extracted."""
    transport = FakeTransport().add(
        "arxiv.org/pdf", sample_pdf.read_bytes(), content_type="application/pdf"
    )
    result = fetch_fulltext(
        "https://arxiv.org/pdf/1509.00001",
        transport=transport,
        policy=AccessPolicy(),
        directory=tmp_path,
        doi="10.1/X",
    )
    assert result.is_pdf
    assert result.available
    assert result.page_count == 1
    assert "SN 2005cf" in result.text
    assert result.doi == "10.1/x"
    assert Path(result.path).is_file()
    assert Path(result.path).name.startswith(result.sha256.split(":")[1][:32])
    assert verify_fulltext(result)


def test_same_bytes_twice_are_stored_once(tmp_path, sample_pdf):
    """Identical payloads do not create a second copy."""
    payload = sample_pdf.read_bytes()
    first = fetch_fulltext(
        "https://a.example/x.pdf",
        transport=FakeTransport().add("a.example", payload),
        policy=AccessPolicy(),
        directory=tmp_path,
    )
    second = fetch_fulltext(
        "https://b.example/y.pdf",
        transport=FakeTransport().add("b.example", payload),
        policy=AccessPolicy(),
        directory=tmp_path,
    )
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert len(list((tmp_path / "fulltext").iterdir())) == 1


def test_html_error_page_is_not_treated_as_a_pdf(tmp_path):
    """A paywall landing page returns 200 and is still not a paper."""
    transport = FakeTransport().add(
        "publisher.example",
        "<!DOCTYPE html><html><body>Sign in to read</body></html>",
        content_type="text/html",
    )
    result = fetch_fulltext(
        "https://publisher.example/x.pdf",
        transport=transport,
        policy=AccessPolicy(),
        directory=tmp_path,
    )
    assert result.is_pdf is False
    assert result.available is False
    assert "html_instead_of_pdf" in result.warnings
    assert "not a PDF" in describe_fulltext(result)


def test_shadow_library_url_is_refused_before_any_request(tmp_path):
    """Policy wins over a caller-supplied URL."""
    transport = FakeTransport()
    result = fetch_fulltext(
        "https://sci-hub.se/10.1093/mnras/stv1947",
        transport=transport,
        policy=AccessPolicy(),
        directory=tmp_path,
    )
    assert transport.calls == []
    assert any(
        item.startswith("refused:shadow_library_refused") for item in result.warnings
    )


def test_http_failure_is_reported_with_its_status(tmp_path):
    """403 from a paywall is a typed failure."""
    result = fetch_fulltext(
        "https://academic.oup.com/x.pdf",
        transport=FakeTransport().add("academic.oup.com", None, status=403),
        policy=AccessPolicy(),
        directory=tmp_path,
    )
    assert result.path == ""
    assert result.warnings == ("download_failed:http_403",)


def test_tampered_file_fails_verification(tmp_path, sample_pdf):
    """Verification compares the bytes on disk with the recorded hash."""
    result = read_local_fulltext(sample_pdf, directory=tmp_path)
    assert verify_fulltext(result)
    Path(result.path).write_bytes(b"%PDF-1.4 tampered")
    assert verify_fulltext(result) is False


def test_local_supplied_pdf_is_adopted_and_hashed(tmp_path, sample_pdf):
    """The operator's own copy is copied into the store, not referenced."""
    result = read_local_fulltext(
        sample_pdf, directory=tmp_path, doi="10.1093/mnras/stv1947"
    )
    assert result.is_pdf
    assert result.available
    assert result.url.startswith("file://")
    assert Path(result.path) != sample_pdf
    assert result.doi == "10.1093/mnras/stv1947"


def test_missing_file_is_reported_not_raised(tmp_path):
    """A path that does not exist is a typed absence."""
    result = read_local_fulltext(tmp_path / "nope.pdf", directory=tmp_path)
    assert result.warnings == ("file_missing",)
    assert result.available is False


def test_size_limit_is_recorded_as_a_warning(tmp_path, sample_pdf):
    """Truncation at the byte limit is visible in the result."""
    payload = sample_pdf.read_bytes()
    result = fetch_fulltext(
        "https://a.example/x.pdf",
        transport=FakeTransport().add("a.example", payload),
        policy=AccessPolicy(max_fulltext_bytes=len(payload)),
        directory=tmp_path,
    )
    assert "download_truncated_at_limit" in result.warnings
