"""Public article retrieval uses bounded mocked transport and honest scopes."""

from __future__ import annotations

import hashlib
import json
import socket
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from jw import literature_full_text as full
from jw.tools import knowledge_base as tools

SOURCE = {
    "source_id": "crossref:10.1234/synthetic-current",
    "title": "Synthetic polar field evolution study",
    "doi": "https://doi.org/10.1234/synthetic-current",
    "url": "https://example.org/current-article",
    "is_retracted": False,
}


def article_text():
    return "\n".join(
        [
            SOURCE["title"],
            "10.1234/synthetic-current",
            "Introduction",
            "Synthetic methodological text for retrieval validation only. " * 15,
            "Methods",
            "Artificial fixture material with no scientific result. " * 15,
            "Results",
            "No real observations or claims are supplied by this fixture. " * 15,
            "References",
            "Example reference metadata only.",
        ]
    )


def html_article():
    return (
        "<html><body><article>"
        + "".join("<p>" + line + "</p>" for line in article_text().splitlines())
        + "</article></body></html>"
    ).encode()


def fetched(raw=None, content_type="text/html"):
    return {
        "raw": html_article() if raw is None else raw,
        "content_type": content_type,
        "final_url": SOURCE["url"],
        "redirect_chain": [],
        "http_status": 200,
    }


def test_full_article_preserves_raw_text_hashes_and_does_not_claim_review(
    tmp_path, monkeypatch
):
    raw = html_article()
    monkeypatch.setattr(full, "_request_public_article", lambda url: fetched(raw))
    result = full.fetch_article_full_text(
        SOURCE,
        article_url=SOURCE["url"],
        directory=tmp_path / "task",
        binding_id="current",
    )
    assert result["status"] == "ok"
    assert result["full_text_retrieved"] is True
    assert result["scientific_assessment_performed"] is False
    assert (
        result["reading_level"] == "retrieved_article_body_not_independently_reviewed"
    )
    assert Path(result["raw_path"]).read_bytes() == raw
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    text = Path(result["path"]).read_bytes()
    assert result["text_sha256"] == hashlib.sha256(text).hexdigest()
    assert json.loads(Path(result["receipt_path"]).read_text()) == result
    assert result["extraction_limits"]


def test_plain_article_is_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(
        full,
        "_request_public_article",
        lambda url: fetched(article_text().encode(), "text/plain"),
    )
    result = full.fetch_article_full_text(
        SOURCE, article_url=SOURCE["url"], directory=tmp_path, binding_id="current"
    )
    assert result["status"] == "ok"
    assert result["format"] == "plain_article_text"


def test_real_synthetic_pdf_is_extracted_without_network():
    commands = ["BT", "/F1 10 Tf", "50 750 Td"]
    for line in article_text().splitlines():
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend([f"({escaped}) Tj", "0 -14 Td"])
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_offset = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{offset:010} 00000 n \n".encode() for offset in offsets[1:]
    )
    raw += (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    result = full._extract_article(raw, "application/pdf", SOURCE)
    assert result["format"] == "pdf_text"
    assert "[page 1]" in result["text"]
    assert "References" in result["text"]


@pytest.mark.parametrize(
    "kind", ["abstract", "title", "doi", "invalid_utf8", "identity"]
)
def test_failed_extraction_never_becomes_full_text(tmp_path, monkeypatch, kind):
    raw = html_article()
    if kind == "abstract":
        raw = (
            "<article><h1>"
            + SOURCE["title"]
            + "</h1><p>Abstract only. " * 200
            + "</p></article>"
        ).encode()
    elif kind == "title":
        raw = raw.replace(
            SOURCE["title"].encode(), b"Unrelated marine ecology classification"
        )
    elif kind == "doi":
        raw = raw.replace(b"10.1234/synthetic-current", b"10.9876/unrelated")
    elif kind == "invalid_utf8":
        raw = b"\xff\xfe"
    monkeypatch.setattr(full, "_request_public_article", lambda url: fetched(raw))

    def validator(value):
        if kind == "identity":
            raise ValueError("prohibited source content")

    result = full.fetch_article_full_text(
        SOURCE,
        article_url=SOURCE["url"],
        directory=tmp_path,
        binding_id="current",
        content_validator=validator,
    )
    assert result["status"] == "unavailable"
    assert result["full_text_retrieved"] is False
    assert result["evidence_scope"] == "none"
    assert "path" not in result
    assert "raw_path" not in result
    assert list(tmp_path.rglob("source.*")) == []
    assert Path(result["receipt_path"]).is_file()


@pytest.mark.parametrize(
    "url",
    [
        "file:///private/file.pdf",
        "http://user:password@example.org/article",
        "https://example.org/article?api_key=never-print-this",
        "https://example.org:8000/article",
    ],
)
def test_unsafe_url_is_rejected_before_network_or_files(tmp_path, monkeypatch, url):
    monkeypatch.setattr(
        full,
        "_request_public_article",
        lambda u: pytest.fail("unsafe input reached network"),
    )
    with pytest.raises(full.ArticleRetrievalError):
        full.fetch_article_full_text(
            SOURCE, article_url=url, directory=tmp_path, binding_id="current"
        )
    assert list(tmp_path.iterdir()) == []


def install_response(
    monkeypatch, status, headers, raw=b"", url="https://8.8.8.8/article"
):
    from jw import research_harness as harness

    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            assert kwargs["headers"]["Accept-Encoding"] == "identity"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, current, **kwargs):
            calls.append(current)
            response = httpx.Response(
                status,
                headers=headers,
                stream=httpx.ByteStream(raw),
                request=httpx.Request(method, current),
            )
            yield response
            response.close()

    monkeypatch.setattr(
        harness, "_PinnedHTTPTransport", lambda address, expected_host: object()
    )
    monkeypatch.setattr(full.httpx, "Client", Client)
    return calls


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_no_authentication_or_paywall_bypass_or_retry(monkeypatch, status):
    calls = install_response(monkeypatch, status, {"content-type": "text/html"})
    with pytest.raises(full.ArticleRetrievalError, match="public_access_unavailable"):
        full._request_public_article("https://8.8.8.8/article")
    assert len(calls) == 1


def test_private_redirect_rejected_before_second_request(monkeypatch):
    calls = install_response(monkeypatch, 302, {"location": "http://127.0.0.1/private"})
    with pytest.raises(ValueError, match="public"):
        full._request_public_article("https://8.8.8.8/article")
    assert len(calls) == 1


def test_private_or_mixed_dns_never_reaches_transport(monkeypatch):
    from jw import research_harness as harness

    calls = install_response(monkeypatch, 200, {"content-type": "text/html"})
    monkeypatch.setattr(
        full,
        "_resolve_public_target",
        lambda url, timeout: harness._public_http_target(url),
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="public"):
        full._request_public_article("https://article.invalid/current")
    assert calls == []


def test_dns_resolution_has_a_killable_deadline(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        full.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("synthetic", 1)
        ),
    )
    with pytest.raises(full.ArticleRetrievalError, match="deadline"):
        full._resolve_public_target("https://article.invalid/current", 0.1)


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/html", "content-length": str(full.MAX_BYTES + 1)},
        {"content-type": "text/html", "content-encoding": "gzip"},
        {"content-type": "application/octet-stream"},
    ],
)
def test_bounded_reader_rejects_size_compression_and_binary(monkeypatch, headers):
    install_response(monkeypatch, 200, headers)
    with pytest.raises(full.ArticleRetrievalError):
        full._request_public_article("https://8.8.8.8/article")


def test_streaming_bytes_are_bounded_without_content_length(monkeypatch):
    monkeypatch.setattr(full, "MAX_BYTES", 5)
    install_response(
        monkeypatch, 200, {"content-type": "text/plain"}, b"more than five bytes"
    )
    with pytest.raises(full.ArticleRetrievalError, match="byte_limit"):
        full._request_public_article("https://8.8.8.8/article")


def test_missing_current_profile_rejects_before_store_read(monkeypatch):
    monkeypatch.setattr(tools, "_get_store", lambda: pytest.fail("legacy store read"))
    result = json.loads(
        tools.lit_fetch_full_text.invoke({"source_id": SOURCE["source_id"]})
    )
    assert result["status"] == "error"


def test_abstract_tool_descriptions_are_explicit():
    for tool in [tools.lit_fetch, tools.lit_bundle_build, tools.lit_bundle_read]:
        assert "abstract" in tool.description.lower()
    assert "lit_fetch_full_text" in {tool.name for tool in tools.KB_LITERATURE_TOOLS}
    assert "lit_fetch_full_text" not in {
        tool.name for tool in tools.KB_INSPECTION_TOOLS
    }
    assert "lit_fetch_full_text" not in {tool.name for tool in tools.KB_READONLY_TOOLS}


def test_task_tool_fetch_read_and_tamper_guards(tmp_path, monkeypatch):
    task = tmp_path / "current-task"
    task.mkdir()
    (task / "profile.json").write_text(
        json.dumps(
            {
                "schema_version": "research-run-profile-v1",
                "enabled": True,
                "fresh": True,
                "max_candidates": 5,
                "suppress_backend_identity": True,
                "engine_id": "research-engine-01",
                "source_policy": "raw-and-current-literature-only",
            }
        )
    )
    (task / "task.json").write_text(
        json.dumps({"thread_id": "current-task", "run_id": "synthetic-run"})
    )

    class Store:
        db_path = tmp_path / "state/knowledge.db"
        export_dir = task / "knowledge"

        def resolve_lit_source(self, source_id):
            return dict(SOURCE)

    store = Store()
    config = {
        "configurable": {
            "thread_id": "current-task",
            "research_profile_path": "profile.json",
        }
    }
    monkeypatch.setenv("JW_DATA_DIR", str(store.db_path.parent))
    monkeypatch.setenv("JW_KB_EXPORT_DIR", str(store.export_dir))
    monkeypatch.setattr(tools, "workspace_root_from_config", lambda cfg: task)
    monkeypatch.setattr(tools, "_get_store", lambda: store)
    monkeypatch.setattr(full, "_request_public_article", lambda url: fetched())
    binding = {"binding_id": "synthetic-current-binding", "run_id": "current-task"}
    monkeypatch.setitem(tools._DISTILL_BINDINGS, binding["binding_id"], binding)
    monkeypatch.setitem(
        tools._ACTIVE_DISTILL_BINDINGS, "current-task", binding["binding_id"]
    )
    result = json.loads(
        tools.lit_fetch_full_text.invoke(
            {"source_id": SOURCE["source_id"]}, config=config
        )
    )
    assert result["status"] == "ok"
    assert full.verified_full_text_sources(task) == [
        Path(result["receipt_path"]),
        Path(result["path"]),
    ]
    partial = json.loads(
        tools.lit_read_full_text.invoke(
            {"receipt_path": result["receipt_path"], "max_lines": 2}, config=config
        )
    )
    assert partial["status"] == "ok"
    assert partial["start_line"] == 1
    assert partial["end_line"] == 2
    assert partial["complete_extracted_text_returned"] is False
    assert partial["text"].startswith(SOURCE["title"])
    assert Path(partial["reading_receipt_path"]).is_file()
    entire = json.loads(
        tools.lit_read_full_text.invoke(
            {
                "receipt_path": result["receipt_path"],
                "max_lines": 200,
                "max_chars": 20000,
            },
            config=config,
        )
    )
    assert entire["complete_extracted_text_returned"] is True
    Path(result["path"]).write_text("Changed text")
    blocked = json.loads(
        tools.lit_read_full_text.invoke(
            {"receipt_path": result["receipt_path"]}, config=config
        )
    )
    assert blocked["status"] == "error"
    Path(result["receipt_path"]).write_text("{}")
    blocked_receipt = json.loads(
        tools.lit_read_full_text.invoke(
            {"receipt_path": result["receipt_path"]}, config=config
        )
    )
    assert blocked_receipt["status"] == "error"


def test_unregistered_or_arbitrary_read_path_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tools, "_task_literature_directory", lambda cfg: tmp_path / "task/literature"
    )
    arbitrary = tmp_path / "unrelated.txt"
    arbitrary.write_text("No read allowed")
    result = json.loads(
        tools.lit_read_full_text.invoke({"receipt_path": str(arbitrary)})
    )
    assert result["status"] == "error"
    assert "text" not in result
