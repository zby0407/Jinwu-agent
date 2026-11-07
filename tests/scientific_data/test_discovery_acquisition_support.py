"""Exercise the discovery-to-original boundary without contacting a provider."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jw.scientific_data.acquisition import HttpFetcher, acquire
from jw.scientific_data.discovery import literature_searcher
from jw.scientific_data.discovery_support import (
    inspect_original,
    web_candidates,
    write_receipt,
)
from jw.scientific_data.ledger_io import workspace_run_dir
from jw.scientific_data.policy import AcquisitionPolicy
from jw.scientific_data.sources import SourceCatalog
from jw.tools import scientific_data as tools


def catalog(*urls):
    result = SourceCatalog()
    for url in urls:
        result.add(url=url, title=url, role="unknown")
    return result


class Fetcher:
    def __init__(self, body=b"t,value\n1,2\n", content_type="text/csv"):
        self.body, self.content_type, self.calls = body, content_type, []

    def fetch(self, url, *, policy):
        self.calls.append(url)
        return 200, self.body, self.content_type


def test_wrapped_literature_tool_uses_invoke(monkeypatch):
    from jw.tools import knowledge_base

    calls = []

    class Tool:
        def invoke(self, payload):
            calls.append(payload)
            return '{"status":"ok","results":[]}'

    monkeypatch.setattr(knowledge_base, "lit_search", Tool())
    assert literature_searcher("a real query", 3)["status"] == "ok"
    assert calls == [{"query": "a real query", "source": "all", "limit": 3}]


def test_empty_allowlist_and_private_hosts_are_refused():
    assert (
        AcquisitionPolicy(allow_network=True).allows("https://data.example/x").reason
        == "host_allowlist_empty"
    )
    for host in ("localhost", "127.0.0.1", "10.0.0.1", "169.254.169.254"):
        assert not AcquisitionPolicy(allow_network=True, allowed_hosts=(host,)).allows(
            f"http://{host}/"
        )
    assert not AcquisitionPolicy(
        allow_network=True, allowed_hosts=("data.example",)
    ).allows("https://u:p@data.example/x")


def test_redirect_is_checked_before_second_request():
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "https://other.example/data.csv"}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        fetcher = HttpFetcher(client=client)
        with pytest.raises(
            ValueError, match="host_not_allowlisted.*host=other.example"
        ):
            fetcher.fetch(
                "https://data.example/start",
                policy=AcquisitionPolicy(
                    allow_network=True, allowed_hosts=("data.example",)
                ),
            )
    assert calls == ["https://data.example/start"]


def test_stream_size_limit_and_redirect_final_url():
    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/data.csv"})
        return httpx.Response(
            200, content=b"12345", headers={"content-type": "text/csv"}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        fetcher = HttpFetcher(client=client)
        policy = AcquisitionPolicy(
            allow_network=True, allowed_hosts=("data.example",), max_bytes=4
        )
        with pytest.raises(ValueError, match="byte limit"):
            fetcher.fetch("https://data.example/start", policy=policy)
        assert fetcher.last_url == "https://data.example/data.csv"


def test_verified_remote_cache_survives_offline_and_detects_tampering(tmp_path):
    url = "https://data.example/a.csv"
    fetcher = Fetcher()
    first = acquire(
        catalog(url),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=fetcher,
    )
    second = acquire(
        catalog(url),
        policy=AcquisitionPolicy(max_fetches=0),
        workdir=tmp_path,
        fetcher=fetcher,
    )
    assert len(fetcher.calls) == 1
    assert second.fetched[0].from_cache
    assert second.stats["attempts"] == 0
    Path(first.fetched[0].path).write_bytes(b"changed")
    third = acquire(
        catalog(url), policy=AcquisitionPolicy(), workdir=tmp_path, fetcher=fetcher
    )
    assert third.stats["denied"] == 1
    assert len(fetcher.calls) == 1


def test_duplicate_payloads_are_acquired_but_never_independent(tmp_path):
    report = acquire(
        catalog("https://data.example/a", "https://data.example/b"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=Fetcher(),
    )
    assert report.stats["acquired"] == 2
    assert report.stats["duplicate_payloads"] == 1
    assert report.results[0].path == report.results[1].path
    assert len(list((tmp_path / "sources").iterdir())) == 1


def test_changed_content_addressed_original_is_never_silently_reused(tmp_path):
    url = "https://data.example/a.csv"
    policy = AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",))
    first = acquire(catalog(url), policy=policy, workdir=tmp_path, fetcher=Fetcher())
    path = Path(first.fetched[0].path)
    path.write_bytes(b"tampered evidence")
    second = acquire(catalog(url), policy=policy, workdir=tmp_path, fetcher=Fetcher())
    assert second.stats["failed"] == 1
    assert "stored original hash changed" in second.failed[0].error
    assert path.read_bytes() == b"tampered evidence"


def test_failed_http_response_is_preserved_as_failure_evidence(tmp_path):
    class Unavailable(Fetcher):
        def fetch(self, url, *, policy):
            return 503, b"provider temporarily unavailable", "text/plain"

    report = acquire(
        catalog("https://data.example/table"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=Unavailable(),
    )
    assert not report.fetched
    failure = report.failed[0]
    assert failure.error == "http_503"
    assert Path(failure.path).read_bytes() == b"provider temporarily unavailable"
    assert failure.sha256.startswith("sha256:")


def test_pdf_inspection_reports_annotation_links_without_claiming_ocr(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "engineering_fixture.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_uri(0, "https://data.example/supplement.csv", [0, 0, 100, 100])
    writer.write(path)
    report = acquire(
        catalog(path.as_uri()),
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "acquired",
    )
    receipt = tmp_path / "pdf-acquisition.json"
    write_receipt(receipt, report.to_dict())
    inspected = inspect_original(tmp_path, receipt, report.fetched[0].source_id)
    assert inspected["links"][0]["url"] == "https://data.example/supplement.csv"
    assert "no OCR" in inspected["notes"][0]


def test_inspection_follows_saved_links_and_refuses_changed_original(tmp_path):
    html = b'<html><a href="../table.csv">Supplementary table</a><a href="javascript:run()">bad</a></html>'
    report = acquire(
        catalog("https://data.example/paper/index.html"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=Fetcher(html, "text/html"),
    )
    receipt = tmp_path / "acquisition.json"
    write_receipt(receipt, report.to_dict())
    source_id = report.fetched[0].source_id
    inspection = inspect_original(tmp_path, receipt, source_id)
    assert [link["url"] for link in inspection["links"]] == [
        "https://data.example/table.csv"
    ]
    assert inspection["links"][0]["independence"] == "unverified"
    assert Path(inspection["receipt"]).is_file()
    Path(report.fetched[0].path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        inspect_original(tmp_path, receipt, source_id)


def test_web_discovery_keeps_response_and_distinguishes_challenge(tmp_path):
    body = b'<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdata.example%2Fa.csv">Data</a><a class="result__snippet">observations</a></div>'
    result = web_candidates(
        "observations", workspace=tmp_path, fetcher=Fetcher(body, "text/html")
    )
    assert result["status"] == "ok"
    assert result["found"][0]["url"] == "https://data.example/a.csv"
    assert Path(result["receipt"]).is_file()
    blocked = web_candidates(
        "another query",
        workspace=tmp_path,
        fetcher=Fetcher(b'<form id="challenge-form">captcha</form>', "text/html"),
    )
    assert blocked["status"] == "unavailable"
    assert blocked["found"] == []


def test_inspection_detects_tgz_saved_as_binary_and_never_extracts_members(tmp_path):
    import io
    import tarfile

    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
        for name, body in (
            ("README.txt", b"Observed with instrument A"),
            ("../outside.txt", b"unsafe path"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    report = acquire(
        catalog("https://data.example/archive/content"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path / "originals",
        fetcher=Fetcher(archive_bytes.getvalue(), "application/octet-stream"),
    )
    receipt = tmp_path / "acquisition.json"
    write_receipt(receipt, report.to_dict())
    inspection = inspect_original(tmp_path, receipt, report.fetched[0].source_id)
    assert "README.txt" in inspection["preview"]
    assert "Observed with instrument A" in inspection["preview"]
    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "originals" / "README.txt").exists()
    assert any("TAR/gzip detected from bytes" in note for note in inspection["notes"])


@pytest.mark.parametrize("run_id", ["../outside", "..", "a/b", "a\\b", "C:evil", ""])
def test_run_directory_cannot_escape(tmp_path, run_id):
    with pytest.raises(ValueError):
        workspace_run_dir(tmp_path, run_id)


def test_pdf_pagination_and_literal_search_beyond_first_pages(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for number in range(1, 7):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        words = "late sample evidence" if number == 5 else f"page number {number}"
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({words}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    path = tmp_path / "fixture.pdf"
    writer.write(path)
    report = acquire(
        catalog(path.as_uri()),
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "acquired",
    )
    receipt = tmp_path / "acquisition.json"
    write_receipt(receipt, report.to_dict())
    source_id = report.fetched[0].source_id
    original = Path(report.fetched[0].path).read_bytes()
    assert (
        "late sample" not in inspect_original(tmp_path, receipt, source_id)["preview"]
    )
    found = inspect_original(
        tmp_path,
        receipt,
        source_id,
        pdf_start_page=4,
        pdf_page_count=3,
        search_text="SAMPLE",
    )
    assert found["selection"]["matched_pages"] == [5]
    assert "[page 5]" in found["preview"] and "late sample evidence" in found["preview"]
    assert found["selection"]["pdf_end_page"] == 6
    with pytest.raises(ValueError, match="past the end"):
        inspect_original(tmp_path, receipt, source_id, pdf_start_page=7)
    with pytest.raises(ValueError, match="1..50"):
        inspect_original(tmp_path, receipt, source_id, pdf_page_count=51)
    assert Path(report.fetched[0].path).read_bytes() == original


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_targeted_archive_read_and_inventory_pagination(tmp_path, kind):
    import io
    import tarfile
    from zipfile import ZipFile

    buffer = io.BytesIO()
    names = [f"tables/item-{index}.dat" for index in range(110)]
    if kind == "tar":
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in names:
                body = (name + "\ntime,value\n1,2\n").encode()
                entry = tarfile.TarInfo(name)
                entry.size = len(body)
                archive.addfile(entry, io.BytesIO(body))
            entry = tarfile.TarInfo("tables/link.dat")
            entry.type = tarfile.SYMTYPE
            entry.linkname = names[0]
            archive.addfile(entry)
            entry = tarfile.TarInfo("tables/large.dat")
            entry.size = 128001
            archive.addfile(entry, io.BytesIO(b"x" * entry.size))
    else:
        from zipfile import ZipInfo

        with ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, name + "\ntime,value\n1,2\n")
            entry = ZipInfo("tables/link.dat")
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, names[0])
            archive.writestr("tables/large.dat", b"x" * 128001)
    report = acquire(
        catalog("https://data.example/blob"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=Fetcher(buffer.getvalue(), "application/octet-stream"),
    )
    receipt = tmp_path / "acquisition.json"
    write_receipt(receipt, report.to_dict())
    source_id = report.fetched[0].source_id
    page = inspect_original(tmp_path, receipt, source_id, member_start=100)
    assert names[109] in page["preview"]
    selected = inspect_original(
        tmp_path, receipt, source_id, archive_member=names[109], search_text="time"
    )
    assert "time,value" in selected["preview"]
    assert selected["selection"]["archive_member"] == names[109]
    for name in (
        "../escape.dat",
        "tables/link.dat",
        "tables/large.dat",
        "tables/absent.dat",
    ):
        with pytest.raises(ValueError):
            inspect_original(tmp_path, receipt, source_id, archive_member=name)
    assert not (tmp_path / "tables").exists()
    assert Path(report.fetched[0].path).read_bytes() == buffer.getvalue()


def test_inspect_tool_forwards_bounded_text_search(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    report = acquire(
        catalog("https://data.example/text"),
        policy=AcquisitionPolicy(allow_network=True, allowed_hosts=("data.example",)),
        workdir=tmp_path,
        fetcher=Fetcher(b"prefix\nlate evidence\n", "text/plain"),
    )
    receipt = tmp_path / "acquisition.json"
    write_receipt(receipt, report.to_dict())
    result = json.loads(
        tools.sci_data_inspect.invoke(
            {
                "run_id": "generic",
                "receipt_path": str(receipt),
                "source_id": report.fetched[0].source_id,
                "search_text": "EVIDENCE",
            }
        )
    )
    assert result["selection"]["match_found"] and result["status"] == "ok"


def test_assigned_skill_is_readable_and_obeys_disable(monkeypatch):
    from jw import skill_settings

    monkeypatch.setattr(skill_settings, "disabled_skill_names", lambda: set())
    payload = json.loads(tools.sci_data_skill.invoke({}))
    assert payload["status"] == "ok"
    assert len(payload["sha256"]) == 64
    monkeypatch.setattr(
        skill_settings, "disabled_skill_names", lambda: {"scientific-data-discovery"}
    )
    assert json.loads(tools.sci_data_skill.invoke({}))["status"] == "error"
    assert (
        json.loads(tools.sci_data_skill.invoke({"name": "../secret"}))["status"]
        == "error"
    )


def test_multi_query_discovery_is_persisted_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    request = {"research_goal": "collect published observations", "domain": "generic"}
    payload = json.loads(
        tools.sci_data_discover.invoke(
            {
                "request_json": json.dumps(request),
                "queries": ["first query", "second query"],
                "search_kind": "all",
            }
        )
    )
    assert payload["search_status"] == "policy_blocked"
    assert [entry["query"] for entry in payload["searches"]] == [
        "first query",
        "second query",
    ]
    assert Path(payload["receipt_path"]).is_file()
    refused = json.loads(
        tools.sci_data_discover.invoke(
            {"request_json": json.dumps(request), "queries": ["a", "b", "c", "d"]}
        )
    )
    assert refused["status"] == "error"


def test_acquisition_tool_receipt_can_drive_inspection(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    original = tmp_path / "table.csv"
    original.write_text("time,value\n1,2\n", encoding="utf-8")
    payload = json.loads(
        tools.sci_data_acquire_only.invoke(
            {
                "request_json": '{"research_goal":"inspect a table"}',
                "urls_json": json.dumps(
                    [
                        {
                            "url": original.as_uri(),
                            "title": "table",
                            "license": "unconfirmed",
                        }
                    ]
                ),
                "run_id": "receipt-test",
            }
        )
    )
    assert Path(payload["receipt_path"]).is_file()
    result = json.loads(
        tools.sci_data_inspect.invoke(
            {
                "run_id": payload["run_id"],
                "source_id": payload["results"][0]["source_id"],
            }
        )
    )
    assert result["status"] == "ok", result
    assert "time,value" in result["preview"]

    # A fresh specialist can recover both candidate context and the acquired
    # original without general filesystem tools or another network search.
    tools.sci_data_discover.invoke(
        {
            "request_json": '{"research_goal":"inspect a table"}',
            "extra_urls_json": '["https://candidate.example/data"]',
            "allow_network": False,
        }
    )
    recovered = json.loads(tools.sci_data_runs.invoke({}))
    assert recovered["runs"] == []  # acquire-only work has no integration ledger
    discovery = recovered["discovery_receipts"][0]
    assert discovery["search_status"] == "policy_blocked"
    assert discovery["candidates"][0]["url"] == "https://candidate.example/data"
    assert discovery["evidence_kind"] == "discovery_candidates_not_acquired_originals"
    original = recovered["acquisition_receipts"][0]["results"][0]
    assert original["sha256"] == payload["results"][0]["sha256"]
    assert original["path"] == payload["results"][0]["path"]
    second_file = tmp_path / "second.csv"
    second_file.write_text("time,value\n2,3\n", encoding="utf-8")
    second = json.loads(
        tools.sci_data_acquire_only.invoke(
            {
                "request_json": '{"research_goal":"inspect a second table"}',
                "urls_json": json.dumps([{"url": second_file.as_uri()}]),
                "run_id": "receipt-test",
            }
        )
    )
    recovered = json.loads(tools.sci_data_runs.invoke({}))
    assert len(recovered["acquisition_receipts"]) == 2
    recovered_hashes = {
        item["sha256"]
        for saved in recovered["acquisition_receipts"]
        for item in saved["results"]
    }
    assert recovered_hashes == {
        payload["results"][0]["sha256"],
        second["results"][0]["sha256"],
    }
    targeted = json.loads(
        tools.sci_data_runs.invoke(
            {
                "kind": "acquisition",
                "receipt_path": payload["receipt_path"],
                "limit": 1,
                "item_limit": 1,
            }
        )
    )
    assert targeted["discovery_receipts"] == []
    assert (
        targeted["acquisition_receipts"][0]["results"][0]["sha256"]
        == payload["results"][0]["sha256"]
    )
    page = json.loads(
        tools.sci_data_runs.invoke({"kind": "acquisition", "offset": 1, "limit": 1})
    )
    assert len(page["acquisition_receipts"]) == 1
    assert page["pagination"]["acquisition_total"] == 2
    assert (
        json.loads(
            tools.sci_data_runs.invoke(
                {"receipt_path": str(tmp_path / "../outside.json")}
            )
        )["status"]
        == "error"
    )
    assert (
        json.loads(tools.sci_data_runs.invoke({"item_limit": 6}))["status"] == "error"
    )
