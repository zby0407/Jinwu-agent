from __future__ import annotations

import gzip
import json
import shutil
import socket
from pathlib import Path

import httpx
import pytest


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _Client:
    last_request: dict | None = None
    response = _Response(
        {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"query": "solar polar field precursor"},
                    "results": [
                        {
                            "title": "Polar field precursor",
                            "url": "https://example.test/paper",
                            "snippet": "A bounded excerpt.",
                        }
                    ],
                },
                {
                    "type": "web_extractor_call",
                    "action": {"url": "https://example.test/paper"},
                    "content": "Full extracted paragraph with locator.",
                },
                {"type": "message", "content": [{"text": "Done."}]},
            ],
        }
    )

    def __init__(self, *args, **kwargs) -> None:
        self.headers = kwargs.get("headers", {})

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, json: dict, timeout: float):
        type(self).last_request = {"url": url, "json": json, "timeout": timeout}
        return type(self).response


class _StreamingResponse:
    def __init__(
        self,
        *,
        url: str,
        chunks: list[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/plain; charset=utf-8"}
        self.iterated_chunks = 0
        self.text = b"".join(chunks).decode("utf-8", errors="replace")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self):
        for chunk in self._chunks:
            self.iterated_chunks += 1
            yield chunk

    def iter_raw(self, chunk_size=None):
        yield from self.iter_bytes()


class _StreamingClient:
    responses: list[_StreamingResponse] = []
    calls: list[str] = []
    init_kwargs: list[dict[str, object]] = []
    follow_redirects: bool | None = None
    get_called = False

    def __init__(self, *args, **kwargs) -> None:
        type(self).follow_redirects = kwargs.get("follow_redirects")
        type(self).init_kwargs.append(dict(kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def get(self, url: str, *, timeout: float):
        type(self).get_called = True
        type(self).calls.append(url)
        return type(self).responses.pop(0)

    def stream(self, method: str, url: str, *, timeout: float):
        assert method == "GET"
        type(self).calls.append(url)
        return type(self).responses.pop(0)


def _public_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def test_source_url_rejects_domain_resolving_to_private_address(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("169.254.169.254", 443))],
    )

    with pytest.raises(ValueError, match="private|public"):
        harness._public_http_url("https://metadata.internal/latest")


def test_source_url_rejects_userinfo_before_request(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    _StreamingClient.responses = [
        _StreamingResponse(url="https://example.test/page", chunks=[b"unused"])
    ]
    _StreamingClient.calls = []
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    with pytest.raises(ValueError, match="userinfo"):
        harness._fetch_url_text("https://user:password@example.test/page", 1.0)

    assert _StreamingClient.calls == []


def test_redirect_target_is_validated_before_private_request(monkeypatch) -> None:
    import jw.research_harness as harness

    def _dns(host: str, *args, **kwargs):
        address = "10.0.0.8" if host == "private.internal" else "93.184.216.34"
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    _StreamingClient.responses = [
        _StreamingResponse(
            url="https://public.example/start",
            chunks=[],
            status_code=302,
            headers={"location": "https://private.internal/secret"},
        )
    ]
    _StreamingClient.calls = []
    _StreamingClient.init_kwargs = []
    _StreamingClient.get_called = False
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    with pytest.raises(ValueError, match="private|public"):
        harness._fetch_url_text("https://public.example/start", 1.0)

    assert _StreamingClient.follow_redirects is False
    assert _StreamingClient.calls == ["https://public.example/start"]


def test_fetch_pins_the_single_validated_ip_and_disables_environment_proxy(
    monkeypatch,
) -> None:
    import jw.research_harness as harness

    dns_calls: list[tuple[str, int]] = []

    def _dns(host: str, port: int, *args, **kwargs):
        dns_calls.append((host, port))
        if len(dns_calls) > 1:
            raise AssertionError("the original hostname was resolved more than once")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    _StreamingClient.responses = [
        _StreamingResponse(
            url="https://example.test/page", chunks=[b"public research page"]
        )
    ]
    _StreamingClient.calls = []
    _StreamingClient.init_kwargs = []
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    assert harness._fetch_url_text("https://example.test/page", 1.0) == (
        "public research page"
    )

    assert dns_calls == [("example.test", 443)]
    assert len(_StreamingClient.init_kwargs) == 1
    client_kwargs = _StreamingClient.init_kwargs[0]
    assert client_kwargs["trust_env"] is False
    assert getattr(client_kwargs["transport"], "pinned_address") == "93.184.216.34"
    assert _StreamingClient.calls == ["https://example.test/page"]


def test_pinned_network_backend_never_resolves_or_connects_to_original_hostname() -> (
    None
):
    import jw.research_harness as harness

    class RecordingBackend:
        def __init__(self) -> None:
            self.hosts: list[tuple[str, int]] = []

        def connect_tcp(self, host: str, port: int, **kwargs):
            self.hosts.append((host, port))
            return object()

    delegate = RecordingBackend()
    backend = harness._PinnedNetworkBackend("2001:4860:4860::8888", delegate=delegate)

    stream = backend.connect_tcp("original.example", 443, timeout=1.0)

    assert stream is not None
    assert delegate.hosts == [("2001:4860:4860::8888", 443)]


def test_each_public_redirect_hop_is_resolved_and_pinned_independently(
    monkeypatch,
) -> None:
    import jw.research_harness as harness

    dns_calls: list[str] = []
    addresses = {
        "first.example": "93.184.216.34",
        "second.example": "2001:4860:4860::8888",
    }

    def _dns(host: str, port: int, *args, **kwargs):
        dns_calls.append(host)
        address = addresses[host]
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (
            (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
        )
        return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    _StreamingClient.responses = [
        _StreamingResponse(
            url="https://first.example/start",
            chunks=[],
            status_code=302,
            headers={"location": "https://second.example/final"},
        ),
        _StreamingResponse(
            url="https://second.example/final", chunks=[b"redirected page"]
        ),
    ]
    _StreamingClient.calls = []
    _StreamingClient.init_kwargs = []
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    assert harness._fetch_url_text("https://first.example/start", 1.0) == (
        "redirected page"
    )

    assert dns_calls == ["first.example", "second.example"]
    assert [
        getattr(kwargs["transport"], "pinned_address")
        for kwargs in _StreamingClient.init_kwargs
    ] == ["93.184.216.34", "2001:4860:4860::8888"]
    assert all(kwargs["trust_env"] is False for kwargs in _StreamingClient.init_kwargs)
    assert _StreamingClient.calls == [
        "https://first.example/start",
        "https://second.example/final",
    ]


@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "239.255.255.250",
        "ff02::1",
    ],
)
def test_source_url_rejects_multicast_even_when_ipaddress_marks_it_global(
    address: str,
) -> None:
    import jw.research_harness as harness

    with pytest.raises(ValueError, match="public|unicast|reserved"):
        harness._public_http_url(
            f"https://[{address}]/page" if ":" in address else f"https://{address}/page"
        )


def test_source_url_rejects_mixed_public_and_private_dns_answers(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port)),
        ],
    )

    with pytest.raises(ValueError, match="private|public|unicast"):
        harness._public_http_url("https://mixed.example/page")


def test_fetch_stream_stops_at_response_byte_limit(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    response = _StreamingResponse(
        url="https://example.test/large",
        chunks=[b"a" * 200_000, b"b" * 100_000, b"c" * 100_000],
    )
    _StreamingClient.responses = [response]
    _StreamingClient.calls = []
    _StreamingClient.get_called = False
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    text = harness._fetch_url_text("https://example.test/large", 1.0)

    assert len(text.encode("utf-8")) == 250_000
    assert response.iterated_chunks == 2
    assert _StreamingClient.get_called is False


def test_fetch_page_records_truncation_when_raw_body_reaches_limit(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    response = _StreamingResponse(
        url="https://example.test/large",
        chunks=[b"a" * harness._MAX_FETCH_BYTES],
        headers={"content-type": "text/plain"},
    )
    _StreamingClient.responses = [response]
    _StreamingClient.calls = []
    _StreamingClient.init_kwargs = []
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    page = harness._fetch_url_text("https://example.test/large", 1.0)

    assert len(page.encode("utf-8")) == harness._MAX_FETCH_BYTES
    assert getattr(page, "truncated") is True


class _CountingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = False

    def __iter__(self):
        self.consumed = True
        yield from self.chunks


def test_raw_reader_rejects_gzip_bomb_before_httpx_decodes_it() -> None:
    import jw.research_harness as harness

    compressed = gzip.compress(b"x" * 5_000_000)
    stream = _CountingStream([compressed])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=stream,
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        with client.stream("GET", "https://example.test/bomb") as response:
            with pytest.raises(ValueError, match="content-encoding"):
                harness._read_bounded_response(response)

    assert stream.consumed is False


def test_raw_reader_bounds_chunked_identity_response_without_content_length() -> None:
    import jw.research_harness as harness

    stream = _CountingStream([b"a" * 100_000, b"b" * 100_000, b"c" * 100_000])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        with client.stream("GET", "https://example.test/chunked") as response:
            body, truncated = harness._read_bounded_response(response)

    assert len(body) == harness._MAX_FETCH_BYTES
    assert truncated is True
    assert stream.consumed is True


def test_truncated_local_page_is_partial_and_not_full_text(monkeypatch, tmp_path: Path):
    import jw.research_harness as harness

    class SearchClient(_Client):
        response = _Response(
            {
                "id": "resp_truncated_page",
                "status": "completed",
                "output": [
                    {
                        "type": "web_search_call",
                        "results": [
                            {
                                "url": "https://example.test/page",
                                "title": "Page",
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", SearchClient)
    monkeypatch.setattr(
        harness,
        "_fetch_url_text",
        lambda _url, _timeout: harness._FetchedText("bounded page", truncated=True),
    )

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="truncated-page",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    extracted = [
        item for item in result["items"] if item.get("source_class") == "retrieved_text"
    ]
    assert result["status"] == "partial"
    assert result["tool_trace"]["truncated"] is True
    assert extracted and extracted[0]["evidence_scope"] == "partial_text"
    assert any("truncated" in str(item).lower() for item in extracted[0]["limitations"])


def test_fetch_stream_accepts_public_https_page(monkeypatch) -> None:
    import jw.research_harness as harness

    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    response = _StreamingResponse(
        url="https://example.test/page",
        chunks=[b"public research page"],
    )
    _StreamingClient.responses = [response]
    _StreamingClient.calls = []
    _StreamingClient.get_called = False
    monkeypatch.setattr(harness.httpx, "Client", _StreamingClient)

    assert (
        harness._fetch_url_text("https://example.test/page", 1.0)
        == "public research page"
    )
    assert _StreamingClient.calls == ["https://example.test/page"]


def test_collect_evidence_persists_bound_sources_and_manifest(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    monkeypatch.setattr(harness.httpx, "Client", _Client)
    monkeypatch.setattr(
        harness,
        "_fetch_url_text",
        lambda url, timeout: "Full locally extracted source text.",
    )
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )

    result = client.collect_evidence(
        task_root=tmp_path,
        task_id="task-1",
        research_question="Can polar field observations predict the next cycle?",
        focus="polar field precursor",
        queries=["solar polar field precursor"],
    )

    assert result["status"] == "completed"
    assert result["schema_version"] == "harness-evidence-v1"
    assert result["binding"]["task_id"] == "task-1"
    assert result["items"][0]["source_class"] == "external_lead"
    assert result["items"][0]["url"] == "https://example.test/paper"
    assert result["items"][1]["source_class"] == "retrieved_text"
    receipt = tmp_path / result["receipt_ref"]
    assert receipt.exists()
    persisted = json.loads(receipt.read_text(encoding="utf-8"))
    assert persisted["artifacts"]
    assert all(
        "secret-test-key" not in path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    request = _Client.last_request
    assert request is not None
    assert request["url"].endswith("/responses")
    assert request["json"]["tools"] == [{"type": "web_search"}]
    assert request["json"]["enable_thinking"] is False


def test_collect_evidence_records_provider_failure_without_fabricating_sources(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    class ErrorClient(_Client):
        response = _Response({"error": {"message": "temporarily unavailable"}}, 503)

    monkeypatch.setattr(harness.httpx, "Client", ErrorClient)
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )

    result = client.collect_evidence(
        task_root=tmp_path,
        task_id="task-2",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert result["status"] == "error"
    assert result["items"] == []
    assert result["tool_trace"]["errors"]
    assert "source_ref" not in result["items"]


def test_collect_evidence_without_search_or_extracted_source_is_partial(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    class MessageOnlyEvidenceClient(_Client):
        response = _Response(
            {
                "id": "resp_evidence_message_only",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "No search or extracted source was returned.",
                            }
                        ],
                    },
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", MessageOnlyEvidenceClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="evidence-message-only",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []
    assert result["tool_trace"]["request_id"] == "resp_evidence_message_only"
    response = tmp_path / str(result["receipt_ref"])
    response = response.parent / "response.json"
    assert "No search or extracted source" in response.read_text(encoding="utf-8")


def test_collect_evidence_parses_live_responses_action_sources(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    class SourceClient(_Client):
        response = _Response(
            {
                "id": "resp_live_shape",
                "status": "completed",
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "type": "search",
                            "query": "polar field",
                            "sources": [
                                {"type": "url", "url": "https://example.test/a"},
                                {"type": "url", "url": "https://example.test/b"},
                            ],
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", SourceClient)
    monkeypatch.setattr(
        harness,
        "_fetch_url_text",
        lambda url, timeout: f"Extracted source text from {url}",
    )
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )

    result = client.collect_evidence(
        task_root=tmp_path,
        task_id="task-live-shape",
        research_question="question",
        focus="focus",
        queries=["polar field"],
    )

    assert [
        item["url"]
        for item in result["items"]
        if item["source_class"] == "external_lead"
    ] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    extracted = [
        item for item in result["items"] if item["source_class"] == "retrieved_text"
    ]
    assert len(extracted) == 2
    assert all(item["tool"] == "web_extractor_local" for item in extracted)


def test_analysis_rejects_input_outside_task_root(tmp_path: Path):
    from jw.research_harness import QwenHarnessClient

    client = QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )
    with pytest.raises(ValueError, match="task workspace"):
        client.run_analysis(
            task_root=tmp_path,
            task_id="task-3",
            research_question="question",
            focus="focus",
            input_refs=["/etc/passwd"],
            instructions="compute a slope",
        )


def test_analysis_embeds_small_task_data_with_hash(monkeypatch, tmp_path: Path):
    import jw.research_harness as harness

    data = tmp_path / "work" / "solar_data" / "cycle.csv"
    data.parent.mkdir(parents=True)
    data.write_text("cycle,value\n24,115.0\n", encoding="utf-8")

    class CodeClient(_Client):
        response = _Response(
            {
                "id": "resp_code",
                "status": "completed",
                "output": [
                    {
                        "type": "code_interpreter_call",
                        "code": "print(115.0)",
                        "outputs": [{"logs": "115.0"}],
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", CodeClient)
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )
    result = client.run_analysis(
        task_root=tmp_path,
        task_id="task-code",
        research_question="question",
        focus="cycle amplitude",
        input_refs=["work/solar_data/cycle.csv"],
        instructions="report the value",
    )

    request = CodeClient.last_request
    assert request is not None
    assert "cycle,value" in request["json"]["input"]
    assert "sha256=" in request["json"]["input"]
    assert "MUST invoke the code_interpreter tool" in request["json"]["input"]
    assert result["status"] == "completed"
    assert result["items"][0]["source_class"] == "derived_calculation"


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is required")
def test_qwen_compatible_chat_analysis_executes_bounded_function_tool(
    monkeypatch, tmp_path: Path
) -> None:
    """The Token Plan chat endpoint must produce a real local calculation record."""

    import jw.research_harness as harness

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("value\n2\n", encoding="utf-8")

    class ChatCompatClient(_Client):
        responses = [
            _Response(
                {
                    "id": "chatcmpl_tool",
                    "model": "qwen3.8-max",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_python",
                                        "type": "function",
                                        "function": {
                                            "name": "run_python",
                                            "arguments": json.dumps(
                                                {
                                                    "code": (
                                                        "from pathlib import Path\n"
                                                        "print(2 + 2)\n"
                                                        "Path('outputs').mkdir()\n"
                                                        "Path('outputs/result.txt').write_text('ok', encoding='utf-8')"
                                                    )
                                                }
                                            ),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ),
            _Response(
                {
                    "id": "chatcmpl_final",
                    "model": "qwen3.8-max",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "已完成计算并返回执行记录。",
                            },
                        }
                    ],
                }
            ),
        ]
        requests: list[dict[str, object]] = []

        def post(self, url: str, *, json: dict, timeout: float):
            type(self).requests.append({"url": url, "json": json, "timeout": timeout})
            return type(self).responses.pop(0)

    monkeypatch.setattr(harness.httpx, "Client", ChatCompatClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    ).run_analysis(
        task_root=tmp_path,
        task_id="chat-analysis",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute the sum",
    )

    assert result["status"] == "completed"
    assert result["items"][0]["source_class"] == "derived_calculation"
    assert len(ChatCompatClient.requests) == 2
    assert all(
        str(request["url"]).endswith("/chat/completions")
        for request in ChatCompatClient.requests
    )
    assert not any(
        str(request["url"]).endswith("/responses")
        for request in ChatCompatClient.requests
    )
    receipt = tmp_path / str(result["receipt_ref"])
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert "4" in json.dumps(payload, ensure_ascii=False)
    output_refs = [
        item["path"]
        for item in payload["artifacts"]
        if item.get("kind") == "derived_output"
    ]
    assert output_refs
    assert (tmp_path / output_refs[0]).read_text(encoding="utf-8") == "ok"


def test_qwen_compatible_chat_analysis_keeps_prose_without_execution_partial(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("value\n2\n", encoding="utf-8")

    class ProseCompatClient(_Client):
        response = _Response(
            {
                "id": "chatcmpl_prose",
                "model": "qwen3.8-max",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "```python\nprint(4)\n```",
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", ProseCompatClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    ).run_analysis(
        task_root=tmp_path,
        task_id="chat-prose",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute the sum",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []


def test_local_python_executor_rejects_network_and_process_imports(
    tmp_path: Path,
) -> None:
    import jw.research_harness as harness

    with pytest.raises(ValueError, match="not allowed"):
        harness._validate_local_python_code("import subprocess\nprint('no')")


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is required")
def test_local_python_executor_cannot_read_files_outside_workspace(
    tmp_path: Path,
) -> None:
    import jw.research_harness as harness

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "host-secret.txt"
    secret = "host-secret-must-not-be-readable"
    outside.write_text(secret, encoding="utf-8")
    encoded_path = list(str(outside).encode("utf-8"))

    execution, outputs = harness._run_local_python(
        "path = bytes(" + repr(encoded_path) + ").decode()\n"
        "print(open(path, encoding='utf-8').read())",
        workspace=workspace,
        input_relpaths=set(),
    )

    assert execution["status"] == "failed"
    assert secret not in str(execution["stdout"])
    assert secret not in str(execution["stderr"])
    assert outputs == []


def test_analysis_without_derived_calculation_is_partial(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    class MessageOnlyAnalysisClient(_Client):
        response = _Response(
            {
                "id": "resp_message_only",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "No execution record."}
                        ],
                    },
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", MessageOnlyAnalysisClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id="analysis-message-only",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []
    assert result["tool_trace"]["request_id"] == "resp_message_only"


def test_analysis_with_code_interpreter_artifact_remains_completed(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    class CodeAnalysisClient(_Client):
        response = _Response(
            {
                "id": "resp_code",
                "status": "completed",
                "output": [
                    {
                        "type": "code_interpreter_call",
                        "status": "completed",
                        "code": "result = 0.42",
                        "outputs": [{"type": "logs", "logs": "0.42"}],
                    }
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", CodeAnalysisClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id="analysis-code",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "completed"
    assert [item["source_class"] for item in result["items"]] == ["derived_calculation"]
    assert len(result["artifacts"]) == 1


@pytest.mark.parametrize(
    "status", ["failed", "incomplete", "cancelled", "error", "queued"]
)
def test_analysis_rejects_non_completed_code_interpreter_calls(
    monkeypatch, tmp_path: Path, status: str
) -> None:
    import jw.research_harness as harness

    class BadCodeClient(_Client):
        response = _Response(
            {
                "id": f"resp_{status}",
                "status": "completed",
                "output": [
                    {
                        "type": "code_interpreter_call",
                        "status": status,
                        "code": "result = 0.42",
                        "outputs": [{"type": "logs", "logs": "0.42"}],
                    }
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", BadCodeClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id=f"analysis-{status}",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []


def test_analysis_rejects_empty_code_interpreter_outputs(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    class EmptyOutputsClient(_Client):
        response = _Response(
            {
                "id": "resp_empty_outputs",
                "status": "completed",
                "output": [
                    {
                        "type": "code_interpreter_call",
                        "status": "completed",
                        "code": "result = 0.42",
                        "outputs": [],
                    }
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", EmptyOutputsClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id="analysis-empty-outputs",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []


def test_message_code_key_does_not_create_code_interpreter_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_harness as harness

    class MessageCodeClient(_Client):
        response = _Response(
            {
                "id": "resp_message_code",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "status": "completed",
                        "code": "result = 0.42",
                        "content": [{"type": "output_text", "text": "No execution."}],
                    }
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", MessageCodeClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id="analysis-message-code",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []


@pytest.mark.parametrize("status", ["queued", "error"])
@pytest.mark.parametrize("url", ["", 123])
def test_extractor_requires_a_non_empty_string_url(
    monkeypatch, tmp_path: Path, status: str, url: object
) -> None:
    import jw.research_harness as harness

    class BadExtractorClient(_Client):
        response = _Response(
            {
                "id": f"resp_{status}",
                "status": "completed",
                "output": [
                    {
                        "type": "web_extractor_call",
                        "status": status,
                        "action": {"url": "https://example.test/paper"},
                        "url": url,
                        "content": "Full extracted paragraph.",
                    }
                ],
            }
        )

    input_path = tmp_path / "inputs" / "analysis.csv"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setattr(harness.httpx, "Client", BadExtractorClient)

    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).run_analysis(
        task_root=tmp_path,
        task_id=f"analysis-extractor-{status}",
        research_question="question",
        focus="focus",
        input_refs=["inputs/analysis.csv"],
        instructions="compute a slope",
    )

    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["artifacts"] == []


@pytest.mark.parametrize("task_id", ["../outside", "/tmp/outside", "nested/task"])
@pytest.mark.parametrize("operation", ["collect", "analysis", "receipt"])
def test_task_operations_reject_non_leaf_task_ids_before_writing(
    tmp_path: Path, task_id: str, operation: str
):
    from jw.research_harness import QwenHarnessClient, write_harness_receipt

    client = QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen3.8-max",
    )
    data = tmp_path / "work" / "input.csv"
    data.parent.mkdir(parents=True)
    data.write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="task_id.*leaf"):
        if operation == "collect":
            client.collect_evidence(
                task_root=tmp_path,
                task_id=task_id,
                research_question="question",
                focus="focus",
                queries=["query"],
            )
        elif operation == "analysis":
            client.run_analysis(
                task_root=tmp_path,
                task_id=task_id,
                research_question="question",
                focus="focus",
                input_refs=["work/input.csv"],
                instructions="compute",
            )
        else:
            write_harness_receipt(tmp_path, {"task_id": task_id})

    assert not (tmp_path / "research_review").exists()


def test_persisted_json_redacts_runtime_key_from_response_and_error(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    runtime_key = "runtime-key-echo-123"

    class EchoClient(_Client):
        response = _Response(
            {
                "id": "resp_echo",
                "status": "completed",
                "output": [],
                "nested": {"echo": f"provider echoed {runtime_key}"},
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", EchoClient)
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key=runtime_key,
        model="qwen3.8-max",
    )
    client.collect_evidence(
        task_root=tmp_path,
        task_id="echo",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    class ErrorClient(_Client):
        def post(self, url: str, *, json: dict, timeout: float):
            raise RuntimeError(f"provider rejected {runtime_key}")

    monkeypatch.setattr(harness.httpx, "Client", ErrorClient)
    client.collect_evidence(
        task_root=tmp_path,
        task_id="error",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert all(
        runtime_key not in path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api-key@token-plan.test/compatible-mode/v1",
        "https://user:password@token-plan.test/compatible-mode/v1",
    ],
)
def test_client_rejects_credential_bearing_endpoint(base_url: str):
    from jw.research_harness import QwenHarnessClient

    with pytest.raises(ValueError, match="userinfo"):
        QwenHarnessClient(base_url=base_url, api_key="secret-test-key", model="qwen")


def test_incomplete_provider_response_is_partial(monkeypatch, tmp_path: Path):
    import jw.research_harness as harness

    class IncompleteClient(_Client):
        response = _Response(
            {"id": "resp_partial", "status": "incomplete", "output": []}
        )

    monkeypatch.setattr(harness.httpx, "Client", IncompleteClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="incomplete",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert result["status"] == "partial"


def test_collect_evidence_parses_response_citation_annotations(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    class CitationClient(_Client):
        response = _Response(
            {
                "id": "resp_citation",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "The cited source supports this statement.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.test/cited",
                                        "title": "Cited paper",
                                        "start_index": 4,
                                        "end_index": 16,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", CitationClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="citation",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    citations = [
        item for item in result["items"] if item["tool"] == "provider_citation"
    ]
    assert citations == [
        {
            "evidence_id": "harness-citation-1-1-1",
            "tool": "provider_citation",
            "url": "https://example.test/cited",
            "title": "Cited paper",
            "locator": "response-citation[4:16]",
            "quote_or_excerpt": "cited source",
            "source_class": "external_lead",
            "evidence_scope": "web_result",
            "claim_role": "gap",
            "limitations": ["响应内引用尚未经过原文抽取，不能单独支持科学主张。"],
        }
    ]


def test_failed_required_local_extraction_is_partial(monkeypatch, tmp_path: Path):
    import jw.research_harness as harness

    monkeypatch.setattr(harness.httpx, "Client", _Client)
    monkeypatch.setattr(
        harness,
        "_fetch_url_text",
        lambda url, timeout: (_ for _ in ()).throw(RuntimeError("fetch failed")),
    )
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="failed-extraction",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert result["status"] == "partial"
    assert any("Could not extract" in item for item in result["limitations"])


def test_failed_provider_extraction_is_partial(monkeypatch, tmp_path: Path):
    import jw.research_harness as harness

    class FailedExtractorClient(_Client):
        response = _Response(
            {
                "id": "resp_failed_extract",
                "status": "completed",
                "output": [
                    {
                        "type": "web_extractor_call",
                        "status": "failed",
                        "action": {"url": "https://example.test/paper"},
                        "error": {"message": "upstream extraction failed"},
                    }
                ],
            }
        )

    monkeypatch.setattr(harness.httpx, "Client", FailedExtractorClient)
    result = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    ).collect_evidence(
        task_root=tmp_path,
        task_id="provider-failed-extraction",
        research_question="question",
        focus="focus",
        queries=["query"],
    )

    assert result["status"] == "partial"
    assert any(
        "provider reported a failed extraction" in item
        for item in result["limitations"]
    )


def test_persisted_request_redacts_runtime_key_from_all_client_inputs(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    runtime_key = "runtime-key-in-request-input-456"
    monkeypatch.setattr(harness.httpx, "Client", _Client)
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key=runtime_key,
        model="qwen",
    )

    client.collect_evidence(
        task_root=tmp_path,
        task_id="secret-collect",
        research_question=f"question {runtime_key}",
        focus=f"focus {runtime_key}",
        queries=[f"query {runtime_key}"],
    )
    input_file = tmp_path / "work" / "secret-input.csv"
    input_file.parent.mkdir(parents=True)
    input_file.write_text(f"value\n{runtime_key}\n", encoding="utf-8")
    client.run_analysis(
        task_root=tmp_path,
        task_id="secret-analysis",
        research_question=f"question {runtime_key}",
        focus=f"focus {runtime_key}",
        input_refs=["work/secret-input.csv"],
        instructions=f"instructions {runtime_key}",
    )

    persisted = tmp_path / "research_review" / "harness"
    assert all(
        runtime_key not in path.read_text(encoding="utf-8")
        for path in persisted.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("link_at", ["research_review", "harness", "task_leaf"])
@pytest.mark.parametrize("operation", ["collect", "receipt"])
def test_symlinked_harness_directories_are_rejected_before_writing(
    monkeypatch, tmp_path: Path, link_at: str, operation: str
):
    import jw.research_harness as harness

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    review_dir = tmp_path / "research_review"
    harness_dir = review_dir / "harness"
    task_dir = harness_dir / "valid-task"
    if link_at == "research_review":
        review_dir.symlink_to(outside, target_is_directory=True)
    elif link_at == "harness":
        review_dir.mkdir()
        harness_dir.symlink_to(outside, target_is_directory=True)
    else:
        harness_dir.mkdir(parents=True)
        task_dir.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(harness.httpx, "Client", _Client)
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    )

    with pytest.raises(ValueError, match="task workspace"):
        if operation == "collect":
            client.collect_evidence(
                task_root=tmp_path,
                task_id="valid-task",
                research_question="question",
                focus="focus",
                queries=["query"],
            )
        else:
            harness.write_harness_receipt(
                tmp_path,
                {"task_id": "valid-task", "status": "completed"},
            )

    assert not any(outside.iterdir())


def test_same_task_invocations_preserve_distinct_receipts_and_requests(
    monkeypatch, tmp_path: Path
):
    import jw.research_harness as harness

    monkeypatch.setattr(harness.httpx, "Client", _Client)
    monkeypatch.setattr(
        harness,
        "_fetch_url_text",
        lambda url, timeout: f"Retrieved text from {url}",
    )
    client = harness.QwenHarnessClient(
        base_url="https://token-plan.test/compatible-mode/v1",
        api_key="secret-test-key",
        model="qwen",
    )

    first = client.collect_evidence(
        task_root=tmp_path,
        task_id="reused-task",
        research_question="first research question",
        focus="first focus",
        queries=["first query"],
    )
    second = client.collect_evidence(
        task_root=tmp_path,
        task_id="reused-task",
        research_question="second research question",
        focus="second focus",
        queries=["second query"],
    )

    assert first["status"] == second["status"] == "completed"
    assert first["receipt_ref"] != second["receipt_ref"]
    first_receipt = tmp_path / first["receipt_ref"]
    second_receipt = tmp_path / second["receipt_ref"]
    assert first_receipt.exists() and second_receipt.exists()
    assert (
        json.loads(first_receipt.read_text(encoding="utf-8"))["binding"][
            "research_question"
        ]
        == "first research question"
    )
    assert (
        json.loads(second_receipt.read_text(encoding="utf-8"))["binding"][
            "research_question"
        ]
        == "second research question"
    )
    assert "first query" in (first_receipt.parent / "request.json").read_text(
        encoding="utf-8"
    )
    assert "second query" in (second_receipt.parent / "request.json").read_text(
        encoding="utf-8"
    )
    assert any(
        item.get("source_ref", "").startswith(
            first_receipt.parent.relative_to(tmp_path).as_posix()
        )
        for item in json.loads(first_receipt.read_text(encoding="utf-8"))["items"]
    )
    assert any(
        item.get("source_ref", "").startswith(
            second_receipt.parent.relative_to(tmp_path).as_posix()
        )
        for item in json.loads(second_receipt.read_text(encoding="utf-8"))["items"]
    )


@pytest.mark.parametrize("payload", [{}, {"task_id": ""}, {"task_id": "   "}])
def test_write_harness_receipt_rejects_missing_or_blank_task_id(
    tmp_path: Path, payload: dict[str, object]
):
    from jw.research_harness import write_harness_receipt

    with pytest.raises(ValueError, match="task_id"):
        write_harness_receipt(tmp_path, payload)
