"""Task-scoped Qwen Responses Harness for research evidence preparation.

This module deliberately sits outside the main LangChain tool-call graph.  It
turns provider-hosted search/extraction/calculation results into ordinary
workspace files that the existing Research Review contract can inspect.
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from bs4 import BeautifulSoup

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|token|secret)", re.IGNORECASE)
_MAX_FETCH_BYTES = 250_000
_RAW_CHUNK_BYTES = 64 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_CHAT_COMPAT_HOST_SUFFIX = ".maas.aliyuncs.com"
_LOCAL_CODE_MAX_BYTES = 80_000
_LOCAL_CODE_MAX_SECONDS = 90
_LOCAL_CODE_MAX_LOG_BYTES = 120_000
_LOCAL_CODE_MAX_FILE_BYTES = 2_000_000
_LOCAL_CODE_MAX_TOTAL_FILE_BYTES = 10_000_000
_LOCAL_CODE_MAX_FILES = 32
_LOCAL_CODE_ALLOWED_IMPORTS = frozenset(
    {
        "builtins",
        "collections",
        "csv",
        "datetime",
        "decimal",
        "itertools",
        "json",
        "math",
        "numpy",
        "os",
        "pandas",
        "pathlib",
        "scipy",
        "statistics",
        "typing",
    }
)
_LOCAL_CODE_BLOCKED_CALLS = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "fork",
        "kill",
        "popen",
        "remove",
        "rmdir",
        "system",
        "unlink",
    }
)
_LOCAL_CODE_BLOCKED_ATTRIBUTES = frozenset(
    {
        "absolute",
        "chdir",
        "chmod",
        "chown",
        "connect",
        "environ",
        "getenv",
        "home",
        "parents",
        "resolve",
        "urlopen",
    }
)
_CHAT_RUN_PYTHON_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Execute one bounded Python calculation in the supplied task data. "
            "Use only relative paths and return concise printed diagnostics."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_json(value: object, *, exact_strings: tuple[str, ...] = ()) -> object:
    """Remove credential-like fields before request/response persistence."""

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if _SECRET_KEY_RE.search(str(key))
            else _safe_json(item, exact_strings=exact_strings)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_json(item, exact_strings=exact_strings) for item in value]
    if isinstance(value, str):
        for secret in exact_strings:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def _task_harness_dir(task_root: Path, task_id: str) -> Path:
    """Return the sole permitted task-local Harness directory."""

    task_root = task_root.resolve()
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-blank validated leaf name")
    candidate = Path(task_id)
    if (
        not task_id
        or candidate.is_absolute()
        or candidate.name != task_id
        or task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
    ):
        raise ValueError("task_id must be one validated leaf name")
    harness_dir = (task_root / "research_review" / "harness" / task_id).resolve()
    try:
        harness_dir.relative_to(task_root)
    except ValueError as exc:
        raise ValueError("Harness paths must stay inside the task workspace") from exc
    return harness_dir


def _invocation_harness_dir(task_root: Path, task_id: str, identity: object) -> Path:
    """Create one collision-safe directory for a task-local Harness invocation."""

    task_root = task_root.resolve()
    task_dir = _task_harness_dir(task_root, task_id)
    encoded_identity = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    name = f"run-{_sha256_bytes(encoded_identity)[:16]}"
    task_dir.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        candidate = task_dir / (name if suffix == 1 else f"{name}-{suffix}")
        try:
            candidate.resolve().relative_to(task_root)
        except ValueError as exc:
            raise ValueError(
                "Harness paths must stay inside the task workspace"
            ) from exc
        try:
            candidate.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return candidate.resolve()


def _relative_ref(task_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(task_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Harness paths must stay inside the task workspace") from exc


def _write_artifact(
    task_root: Path,
    path: Path,
    payload: object,
    *,
    exact_strings: tuple[str, ...] = (),
) -> dict[str, object]:
    _relative_ref(task_root, path)
    _json_dump(path, _safe_json(payload, exact_strings=exact_strings))
    raw = path.read_bytes()
    return {
        "path": _relative_ref(task_root, path),
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "kind": "retrieved_text" if path.parent.name == "sources" else "harness_trace",
    }


def _output_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output", [])
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    if isinstance(output, dict):
        return [output]
    return []


def _is_chat_compatible_base_url(base_url: str) -> bool:
    """Recognize Alibaba Model Studio business-space Chat Completions URLs."""

    parsed = urlparse(base_url)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.hostname.casefold().endswith(_CHAT_COMPAT_HOST_SUFFIX)
        and parsed.path.rstrip("/").endswith("/compatible-mode/v1")
    )


def _validate_local_python_code(code: str) -> None:
    """Reject code that can escape the task-local calculation sandbox."""

    if not isinstance(code, str) or not code.strip():
        raise ValueError("local Python code must be a non-empty string")
    if len(code.encode("utf-8")) > _LOCAL_CODE_MAX_BYTES:
        raise ValueError("local Python code exceeds the bounded execution limit")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"local Python code is not valid syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            root = name.split(".", 1)[0].casefold()
            if root not in _LOCAL_CODE_ALLOWED_IMPORTS:
                raise ValueError(f"local Python import is not allowed: {root}")

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and (
                node.func.id in _LOCAL_CODE_BLOCKED_CALLS
            ):
                raise ValueError(f"local Python call is not allowed: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and (
                node.func.attr in _LOCAL_CODE_BLOCKED_CALLS
                or node.func.attr in _LOCAL_CODE_BLOCKED_ATTRIBUTES
            ):
                raise ValueError(f"local Python call is not allowed: {node.func.attr}")
        if isinstance(node, ast.Attribute) and (
            node.attr in _LOCAL_CODE_BLOCKED_ATTRIBUTES or node.attr.startswith("__")
        ):
            raise ValueError(f"local Python attribute is not allowed: {node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"local Python name is not allowed: {node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace("\\", "/")
            path_like = value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:/", value)
            if path_like or any(part == ".." for part in value.split("/")):
                raise ValueError("local Python paths must remain relative to the task")


def _bounded_text(value: object, limit: int = _LOCAL_CODE_MAX_LOG_BYTES) -> str:
    text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value or "")
    )
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n[output truncated]"


def _prepare_local_code_workspace(
    task_root: Path,
    harness_dir: Path,
    input_refs: list[str],
) -> tuple[Path, set[str]]:
    """Copy verified inputs into a fresh, task-local execution directory."""

    workspace = (harness_dir / "python_workspace").resolve()
    _relative_ref(task_root, workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    for ref in input_refs:
        source = (task_root / ref).resolve()
        _relative_ref(task_root, source)
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"local analysis input is not a regular file: {ref}")
        destination = (workspace / ref).resolve()
        if not destination.is_relative_to(workspace):
            raise ValueError("local analysis input escaped the execution directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.add(destination.relative_to(workspace).as_posix())
    return workspace, copied


def _run_local_python(
    code: str,
    *,
    workspace: Path,
    input_relpaths: set[str],
) -> tuple[dict[str, object], list[tuple[Path, str]]]:
    """Execute model-authored code in a bubblewrap-isolated task directory."""

    _validate_local_python_code(code)
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("local Python Harness requires bubblewrap")
    script = workspace / ".harness_code.py"
    script.write_text(code + "\n", encoding="utf-8")
    command = [
        bwrap,
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(workspace),
        str(workspace),
        "--chdir",
        str(workspace),
        "--unshare-net",
        "--unshare-pid",
        "--proc",
        "/proc",
        "--die-with-parent",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        str(workspace),
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--setenv",
        "MPLBACKEND",
        "Agg",
        sys.executable,
        "-I",
        str(script),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_LOCAL_CODE_MAX_SECONDS,
            check=False,
        )
        return_code = completed.returncode
        stdout = _bounded_text(completed.stdout)
        stderr = _bounded_text(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        stdout = _bounded_text(exc.stdout)
        stderr = _bounded_text(exc.stderr)
        return_code = -9
        stderr = (stderr + "\n" if stderr else "") + "execution timed out"

    output_files: list[tuple[Path, str]] = []
    total_bytes = 0
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative == ".harness_code.py" or relative in input_relpaths:
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        size = path.stat().st_size
        if len(output_files) >= _LOCAL_CODE_MAX_FILES:
            break
        if size > _LOCAL_CODE_MAX_FILE_BYTES:
            continue
        if total_bytes + size > _LOCAL_CODE_MAX_TOTAL_FILE_BYTES:
            break
        output_files.append((path, relative))
        total_bytes += size

    return (
        {
            "status": "completed" if return_code == 0 else "failed",
            "code": code,
            "returncode": return_code,
            "stdout": stdout,
            "stderr": stderr,
        },
        output_files,
    )


def _is_public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not address.is_multicast


def _public_http_target(value: str) -> tuple[str, str, str]:
    """Validate one URL and return its original URL, host, and pinned IP."""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source URL must use public HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL must not include userinfo")
    if parsed.hostname.casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("source URL must not target localhost")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            resolved = socket.getaddrinfo(
                parsed.hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                "source URL hostname could not be resolved safely"
            ) from exc
        addresses = list(
            dict.fromkeys(
                str(sockaddr[0])
                for _family, _socktype, _proto, _canonname, sockaddr in resolved
                if isinstance(sockaddr, tuple) and sockaddr
            )
        )
        if not addresses:
            raise ValueError("source URL hostname did not resolve to a public address")
        for resolved_address in addresses:
            try:
                address = ipaddress.ip_address(resolved_address)
            except ValueError as exc:
                raise ValueError(
                    "source URL hostname returned an invalid address"
                ) from exc
            if not _is_public_unicast(address):
                raise ValueError(
                    "source URL must resolve only to public unicast addresses"
                )
    else:
        if not _is_public_unicast(address):
            raise ValueError("source URL must target a public unicast address")
        addresses = [str(address)]
    return value, parsed.hostname, addresses[0]


def _public_http_url(value: str) -> str:
    return _public_http_target(value)[0]


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    """Connect to one validated IP while httpcore retains the URL host for TLS."""

    def __init__(
        self,
        pinned_address: str,
        *,
        expected_host: str | None = None,
        delegate: Any | None = None,
    ) -> None:
        self.pinned_address = pinned_address
        self.expected_host = expected_host.casefold() if expected_host else None
        self._delegate = delegate or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        if self.expected_host is not None and host.casefold() != self.expected_host:
            raise httpcore.ConnectError("pinned transport received an unexpected host")
        return self._delegate.connect_tcp(
            self.pinned_address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> Any:
        return self._delegate.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    def sleep(self, seconds: float) -> None:
        self._delegate.sleep(seconds)


class _PinnedHTTPTransport(httpx.HTTPTransport):
    """httpx transport whose TCP backend cannot re-resolve the URL hostname."""

    def __init__(self, pinned_address: str, *, expected_host: str) -> None:
        super().__init__(trust_env=False)
        self._pool.close()
        self.pinned_address = pinned_address
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedNetworkBackend(
                pinned_address, expected_host=expected_host
            ),
        )


class _FetchedText(str):
    """String-compatible fetched text carrying the bounded-read status."""

    truncated: bool

    def __new__(cls, value: str, *, truncated: bool = False):
        instance = super().__new__(cls, value)
        instance.truncated = truncated
        return instance


def _read_bounded_response(response: httpx.Response) -> tuple[bytes, bool]:
    """Read identity-encoded response bytes without allowing decoded expansion."""

    content_encoding = response.headers.get("content-encoding", "").strip().casefold()
    if content_encoding not in {"", "identity"}:
        raise ValueError(
            "compressed content-encoding is not accepted by the bounded raw reader"
        )
    content = bytearray()
    truncated = False
    for chunk in response.iter_raw(chunk_size=_RAW_CHUNK_BYTES):
        if not chunk:
            continue
        remaining = _MAX_FETCH_BYTES - len(content)
        if remaining <= 0:
            truncated = True
            break
        content.extend(chunk[:remaining])
        if len(content) >= _MAX_FETCH_BYTES:
            truncated = True
            break
    return bytes(content), truncated


def _fetch_url_text(url: str, timeout: float) -> str:
    """Fetch one public page and return bounded readable text."""

    headers = {
        "User-Agent": "JW-Solar-Research/1.0 (+task-scoped evidence retrieval)",
        "Accept-Encoding": "identity",
    }
    current_url = url
    content = bytearray()
    content_type = ""
    for redirect_count in range(_MAX_REDIRECTS + 1):
        _validated_url, original_host, pinned_address = _public_http_target(current_url)
        transport = _PinnedHTTPTransport(pinned_address, expected_host=original_host)
        with httpx.Client(
            follow_redirects=False,
            headers=headers,
            timeout=timeout,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream("GET", current_url, timeout=timeout) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not isinstance(location, str) or not location.strip():
                        raise ValueError("redirect response is missing Location")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise ValueError("source URL exceeded the redirect limit")
                    current_url = urljoin(current_url, location.strip())
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").casefold()
                raw_content, truncated = _read_bounded_response(response)
                content.extend(raw_content)
                break
        if response.status_code not in _REDIRECT_STATUSES:
            break
    else:  # pragma: no cover - bounded loop always exits or raises
        raise ValueError("source URL exceeded the redirect limit")
    text = bytes(content).decode("utf-8", errors="replace")
    if "html" in content_type:
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    return _FetchedText(text.strip(), truncated=truncated)


class QwenHarnessClient:
    """Task-scoped client for Responses and Token Plan Chat Completions."""

    def __init__(
        self, *, base_url: str, api_key: str, model: str, timeout: float = 180.0
    ):
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.username is not None or parsed_base_url.password is not None:
            raise ValueError("base_url must not include userinfo")
        if not api_key:
            raise ValueError("api_key must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _persist_chat_response(
        self, harness_dir: Path, turn: int, payload: object
    ) -> None:
        name = "response.json" if turn == 0 else f"response-{turn + 1}.json"
        _json_dump(
            harness_dir / name,
            _safe_json(payload, exact_strings=(self.api_key,)),
        )

    def _copy_chat_output_files(
        self,
        *,
        task_root: Path,
        harness_dir: Path,
        output_files: list[tuple[Path, str]],
        turn: int,
    ) -> list[dict[str, object]]:
        copied: list[dict[str, object]] = []
        for source, relative in output_files:
            destination = (
                harness_dir / "calculations" / "files" / f"{turn:02d}-{relative}"
            ).resolve()
            if not destination.is_relative_to(harness_dir.resolve()):
                raise ValueError(
                    "local calculation output escaped the Harness directory"
                )
            raw = source.read_bytes()
            if self.api_key.encode("utf-8") in raw:
                raise ValueError(
                    "local calculation output contains a credential-like value"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            copied.append(
                {
                    "path": _relative_ref(task_root, destination),
                    "sha256": _sha256_bytes(raw),
                    "bytes": len(raw),
                    "kind": "derived_output",
                }
            )
        return copied

    def _chat_compat_analysis(
        self,
        *,
        task_root: Path,
        task_id: str,
        research_question: str,
        focus: str,
        input_text: str,
        input_refs: list[str],
        model: str | None,
    ) -> tuple[dict[str, Any], dict[str, object], Path]:
        """Run analysis through function calling when `/responses` is unavailable."""

        request_identity = {
            "protocol": "chat_completions",
            "model": model or self.model,
            "input": input_text,
            "input_refs": input_refs,
        }
        harness_dir = _invocation_harness_dir(
            task_root,
            task_id,
            _safe_json(request_identity, exact_strings=(self.api_key,)),
        )
        _json_dump(
            harness_dir / "request.json",
            _safe_json(request_identity, exact_strings=(self.api_key,)),
        )
        trace: dict[str, object] = {
            "protocol": "chat_completions",
            "request_id": None,
            "errors": [],
            "warnings": [],
            "truncated": False,
        }
        try:
            workspace, input_relpaths = _prepare_local_code_workspace(
                task_root, harness_dir, input_refs
            )
        except Exception as exc:
            trace["errors"] = [
                {
                    "type": type(exc).__name__,
                    "message": _safe_json(str(exc), exact_strings=(self.api_key,)),
                }
            ]
            return {}, trace, harness_dir

        system_prompt = (
            "The provider is an OpenAI-compatible Chat Completions endpoint. "
            "For this bounded analysis, run_python is the compatibility alias "
            "for code_interpreter and is the only available tool. You must call "
            "it with executable Python before making any calculation claim. "
            "Use only the supplied relative input paths; do not use network, "
            "shell commands, environment variables, or absolute paths. After "
            "the tool result, give a concise scientific-data summary with limits."
        )
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": input_text},
        ]
        outputs: list[dict[str, object]] = []
        successful_executions = 0
        failed_execution = False
        final_text = ""
        max_turns = 4

        for turn in range(max_turns):
            payload = {
                "model": model or self.model,
                "messages": messages,
                "tools": [_CHAT_RUN_PYTHON_TOOL],
                "tool_choice": "auto",
                "temperature": 0,
                "enable_thinking": False,
            }
            try:
                with httpx.Client(
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                ) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        timeout=self.timeout,
                    )
                response.raise_for_status()
                response_payload = response.json()
                if not isinstance(response_payload, dict):
                    raise ValueError(
                        "Chat Completions returned a non-object JSON payload"
                    )
                trace["request_id"] = response_payload.get("id") or trace.get(
                    "request_id"
                )
                self._persist_chat_response(harness_dir, turn, response_payload)
            except Exception as exc:
                message = _safe_json(str(exc), exact_strings=(self.api_key,))
                if successful_executions:
                    trace.setdefault("warnings", []).append(
                        {"type": type(exc).__name__, "message": message}
                    )
                    break
                trace["errors"] = [{"type": type(exc).__name__, "message": message}]
                return {}, trace, harness_dir

            choices = response_payload.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else {}
            message = choice.get("message") if isinstance(choice, dict) else {}
            if not isinstance(message, dict):
                message = {}
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                final_text = str(message.get("content") or "")
                outputs.append(
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": final_text}],
                    }
                )
                break

            assistant_message = {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)
            for call in tool_calls:
                if not isinstance(call, dict):
                    failed_execution = True
                    continue
                call_id = str(call.get("id") or f"call_{turn}")
                function = call.get("function")
                if (
                    not isinstance(function, dict)
                    or function.get("name") != "run_python"
                ):
                    execution = {
                        "status": "failed",
                        "code": "",
                        "returncode": -1,
                        "stdout": "",
                        "stderr": "unsupported function tool",
                    }
                    output_files: list[tuple[Path, str]] = []
                else:
                    raw_arguments = function.get("arguments")
                    code = ""
                    try:
                        arguments = (
                            json.loads(raw_arguments)
                            if isinstance(raw_arguments, str)
                            else raw_arguments
                        )
                        code = (
                            arguments.get("code")
                            if isinstance(arguments, dict)
                            else None
                        )
                        if not isinstance(code, str):
                            raise ValueError("run_python arguments must contain code")
                        execution, output_files = _run_local_python(
                            code,
                            workspace=workspace,
                            input_relpaths=input_relpaths,
                        )
                    except Exception as exc:
                        execution = {
                            "status": "failed",
                            "code": code,
                            "returncode": -1,
                            "stdout": "",
                            "stderr": _safe_json(
                                str(exc), exact_strings=(self.api_key,)
                            ),
                        }
                        output_files = []
                try:
                    file_artifacts = self._copy_chat_output_files(
                        task_root=task_root,
                        harness_dir=harness_dir,
                        output_files=output_files,
                        turn=turn,
                    )
                except Exception as exc:
                    file_artifacts = []
                    execution["status"] = "failed"
                    execution["stderr"] = _safe_json(
                        str(exc), exact_strings=(self.api_key,)
                    )
                execution["files"] = file_artifacts
                if execution.get("status") == "completed":
                    successful_executions += 1
                else:
                    failed_execution = True
                outputs.append(
                    {
                        "type": "code_interpreter_call",
                        "status": execution.get("status"),
                        "code": execution.get("code"),
                        "outputs": [
                            {
                                "type": "logs",
                                "logs": _bounded_text(
                                    str(execution.get("stdout") or "")
                                    + (
                                        "\n" + str(execution.get("stderr") or "")
                                        if execution.get("stderr")
                                        else ""
                                    )
                                ),
                            }
                        ],
                        "files": file_artifacts,
                    }
                )
                tool_content = _safe_json(
                    {
                        "status": execution.get("status"),
                        "stdout": execution.get("stdout"),
                        "stderr": execution.get("stderr"),
                        "files": file_artifacts,
                    },
                    exact_strings=(self.api_key,),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(tool_content, ensure_ascii=False),
                    }
                )

        if not outputs:
            outputs.append(
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": final_text}],
                }
            )
        status = (
            "completed"
            if successful_executions and final_text and not failed_execution
            else "partial"
        )
        synthetic_payload: dict[str, Any] = {
            "id": trace.get("request_id"),
            "status": status,
            "output": outputs,
        }
        _json_dump(
            harness_dir / "chat_compat_payload.json",
            _safe_json(synthetic_payload, exact_strings=(self.api_key,)),
        )
        return synthetic_payload, trace, harness_dir

    def _request(
        self,
        *,
        task_root: Path,
        task_id: str,
        research_question: str,
        focus: str,
        input_text: str,
        tools: list[dict[str, str]],
        model: str | None,
        enable_thinking: bool,
        analysis: bool = False,
        input_refs: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, object] | None, Path]:
        if _is_chat_compatible_base_url(self.base_url):
            if analysis:
                return self._chat_compat_analysis(
                    task_root=task_root,
                    task_id=task_id,
                    research_question=research_question,
                    focus=focus,
                    input_text=input_text,
                    input_refs=list(input_refs or []),
                    model=model,
                )
            harness_dir = _invocation_harness_dir(
                task_root,
                task_id,
                _safe_json(
                    {
                        "protocol": "chat_completions",
                        "tools": tools,
                        "input": input_text,
                    },
                    exact_strings=(self.api_key,),
                ),
            )
            trace: dict[str, object] = {
                "protocol": "chat_completions",
                "request_id": None,
                "errors": [
                    {
                        "type": "UnsupportedHarnessTool",
                        "message": (
                            "Token Plan Chat Completions does not expose the "
                            "provider Responses web-search tool; use the task-bound "
                            "literature path for scholarly retrieval."
                        ),
                    }
                ],
                "truncated": False,
            }
            _json_dump(
                harness_dir / "request.json",
                {"protocol": "chat_completions", "tools": tools},
            )
            return {}, trace, harness_dir
        request_payload: dict[str, object] = {
            "model": model or self.model,
            "input": input_text,
            "tools": tools,
            "enable_thinking": enable_thinking,
        }
        harness_dir = _invocation_harness_dir(
            task_root,
            task_id,
            _safe_json(request_payload, exact_strings=(self.api_key,)),
        )
        _json_dump(
            harness_dir / "request.json",
            _safe_json(request_payload, exact_strings=(self.api_key,)),
        )
        trace: dict[str, object] = {
            "request_id": None,
            "errors": [],
            "truncated": False,
        }
        try:
            with httpx.Client(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            ) as client:
                response = client.post(
                    f"{self.base_url}/responses",
                    json=request_payload,
                    timeout=self.timeout,
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Responses API returned a non-object JSON payload")
            trace["request_id"] = payload.get("id")
            _json_dump(
                harness_dir / "response.json",
                _safe_json(payload, exact_strings=(self.api_key,)),
            )
            return payload, trace, harness_dir
        except Exception as exc:  # provider boundary: preserve a structured failure
            trace["errors"] = [
                {
                    "type": type(exc).__name__,
                    "message": _safe_json(str(exc), exact_strings=(self.api_key,)),
                }
            ]
            return {}, trace, harness_dir

    def _collect(
        self,
        *,
        task_root: Path,
        task_id: str,
        research_question: str,
        focus: str,
        input_text: str,
        tools: list[dict[str, str]],
        model: str | None,
        analysis: bool,
        enable_thinking: bool,
        input_refs: list[str] | None = None,
    ) -> dict[str, object]:
        task_root = Path(task_root).resolve()
        payload, trace, harness_dir = self._request(
            task_root=task_root,
            task_id=task_id,
            research_question=research_question,
            focus=focus,
            input_text=input_text,
            tools=tools,
            model=model,
            enable_thinking=enable_thinking,
            analysis=analysis,
            input_refs=input_refs,
        )
        artifacts: list[dict[str, object]] = []
        items: list[dict[str, object]] = []
        failed_extractions = 0
        failed_outputs = 0
        for index, output in enumerate(_output_items(payload), start=1):
            output_type = str(output.get("type", ""))
            if output_type == "web_search_call":
                action = (
                    output.get("action")
                    if isinstance(output.get("action"), dict)
                    else {}
                )
                results = output.get("results")
                if not isinstance(results, list):
                    results = (
                        action.get("results") or action.get("sources") or []
                        if isinstance(action, dict)
                        else []
                    )
                for result_index, result in enumerate(results, start=1):
                    if not isinstance(result, dict):
                        continue
                    url = str(result.get("url", "")).strip()
                    item: dict[str, object] = {
                        "evidence_id": f"harness-evidence-{index}-{result_index}",
                        "tool": "web_search",
                        "url": url or None,
                        "title": result.get("title"),
                        "locator": "search-result",
                        "quote_or_excerpt": result.get("snippet")
                        or result.get("description"),
                        "source_class": "external_lead",
                        "evidence_scope": "web_result",
                        "claim_role": "gap",
                        "limitations": [
                            "搜索摘要未经过原文抽取，不能单独支持科学主张。"
                        ],
                    }
                    source_path = (
                        harness_dir / "sources" / f"search-{index}-{result_index}.json"
                    )
                    item["source_ref"] = _relative_ref(task_root, source_path)
                    artifacts.append(
                        _write_artifact(
                            task_root,
                            source_path,
                            result,
                            exact_strings=(self.api_key,),
                        )
                    )
                    items.append(item)
            elif output_type == "web_extractor_call":
                call_status = str(output.get("status") or "").casefold()
                if (call_status and call_status != "completed") or output.get("error"):
                    failed_extractions += 1
                    continue
                action = (
                    output.get("action")
                    if isinstance(output.get("action"), dict)
                    else {}
                )
                raw_url = output.get("url") or action.get("url")
                url = raw_url.strip() if isinstance(raw_url, str) else ""
                content = output.get("content")
                if content is None:
                    content = output.get("text")
                if not isinstance(content, str) or not content.strip() or not url:
                    failed_extractions += 1
                    continue
                content = _safe_json(content, exact_strings=(self.api_key,))
                assert isinstance(content, str)
                source_path = harness_dir / "sources" / f"extracted-{index}.md"
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(content, encoding="utf-8")
                raw = source_path.read_bytes()
                artifacts.append(
                    {
                        "path": _relative_ref(task_root, source_path),
                        "sha256": _sha256_bytes(raw),
                        "bytes": len(raw),
                        "kind": "retrieved_text",
                    }
                )
                items.append(
                    {
                        "evidence_id": f"harness-evidence-{index}",
                        "tool": "web_extractor",
                        "url": url or None,
                        "title": None,
                        "locator": output.get("locator") or "extracted-content",
                        "quote_or_excerpt": content[:2000],
                        "source_ref": _relative_ref(task_root, source_path),
                        "source_class": "retrieved_text",
                        "evidence_scope": "full_text",
                        "claim_role": "gap",
                        "limitations": [] if url else ["抽取结果未带来源 URL。"],
                    }
                )
            elif output_type == "message":
                content_items = output.get("content")
                if not isinstance(content_items, list):
                    continue
                for content_index, content_item in enumerate(content_items, start=1):
                    if not isinstance(content_item, dict):
                        continue
                    text = content_item.get("text")
                    if not isinstance(text, str):
                        continue
                    annotations = content_item.get("annotations")
                    if not isinstance(annotations, list):
                        continue
                    for annotation_index, annotation in enumerate(annotations, start=1):
                        if not isinstance(annotation, dict):
                            continue
                        if annotation.get("type") != "url_citation":
                            continue
                        url = str(annotation.get("url") or "").strip()
                        if not url:
                            continue
                        start = annotation.get("start_index")
                        end = annotation.get("end_index")
                        if not isinstance(start, int) or not isinstance(end, int):
                            continue
                        excerpt = _safe_json(
                            text[max(0, start) : max(0, end)],
                            exact_strings=(self.api_key,),
                        )
                        items.append(
                            {
                                "evidence_id": (
                                    f"harness-citation-{index}-{content_index}-"
                                    f"{annotation_index}"
                                ),
                                "tool": "provider_citation",
                                "url": url,
                                "title": annotation.get("title"),
                                "locator": f"response-citation[{start}:{end}]",
                                "quote_or_excerpt": excerpt,
                                "source_class": "external_lead",
                                "evidence_scope": "web_result",
                                "claim_role": "gap",
                                "limitations": [
                                    "响应内引用尚未经过原文抽取，不能单独支持科学主张。"
                                ],
                            }
                        )
            elif output_type == "code_interpreter_call":
                call_status = str(output.get("status") or "").casefold()
                code = output.get("code")
                outputs = output.get("outputs")
                if (
                    (call_status and call_status != "completed")
                    or output.get("error")
                    or not isinstance(code, str)
                    or not code.strip()
                    or not isinstance(outputs, list)
                    or not outputs
                ):
                    failed_outputs += 1
                    continue
                path = harness_dir / "calculations" / f"analysis-{index}.json"
                artifacts.append(
                    _write_artifact(
                        task_root,
                        path,
                        output,
                        exact_strings=(self.api_key,),
                    )
                )
                file_entries = output.get("files")
                if isinstance(file_entries, list):
                    for file_entry in file_entries:
                        if not isinstance(file_entry, dict):
                            continue
                        file_ref = file_entry.get("path")
                        expected_sha = file_entry.get("sha256")
                        expected_bytes = file_entry.get("bytes")
                        if (
                            not isinstance(file_ref, str)
                            or not isinstance(expected_sha, str)
                            or not isinstance(expected_bytes, int)
                            or isinstance(expected_bytes, bool)
                        ):
                            failed_outputs += 1
                            continue
                        try:
                            file_path = (task_root / file_ref).resolve()
                            _relative_ref(task_root, file_path)
                            raw = file_path.read_bytes()
                        except (OSError, ValueError):
                            failed_outputs += 1
                            continue
                        if (
                            len(raw) != expected_bytes
                            or _sha256_bytes(raw) != expected_sha
                        ):
                            failed_outputs += 1
                            continue
                        artifacts.append(
                            {
                                "path": file_ref,
                                "sha256": expected_sha,
                                "bytes": expected_bytes,
                                "kind": "derived_output",
                            }
                        )
                items.append(
                    {
                        "evidence_id": f"harness-evidence-{index}",
                        "tool": "code_interpreter",
                        "url": None,
                        "locator": "execution-record",
                        "quote_or_excerpt": None,
                        "source_ref": _relative_ref(task_root, path),
                        "source_class": "derived_calculation",
                        "evidence_scope": "experiment_record",
                        "claim_role": "gap",
                        "limitations": [
                            "必须由 Evidence 根据输入、代码和输出复核后才能支持主张。"
                        ],
                    }
                )

        provider_status = str(payload.get("status") or "")
        if not payload or (trace or {}).get("errors") or payload.get("error"):
            status = "error"
        elif provider_status == "incomplete" or payload.get("incomplete_details"):
            status = "partial"
        elif provider_status in {"", "completed"}:
            status = "completed"
        else:
            status = "partial"
        if failed_extractions and status == "completed":
            status = "partial"
        if failed_outputs and status == "completed":
            status = "partial"
        derived_refs = {
            str(item.get("source_ref"))
            for item in items
            if item.get("source_class") == "derived_calculation"
            and isinstance(item.get("source_ref"), str)
        }
        artifact_refs = {
            str(artifact.get("path"))
            for artifact in artifacts
            if isinstance(artifact.get("path"), str)
            and isinstance(artifact.get("sha256"), str)
            and artifact.get("sha256")
        }
        missing_analysis_output = analysis and not (derived_refs & artifact_refs)
        if missing_analysis_output and status == "completed":
            status = "partial"
        receipt: dict[str, object] = {
            "schema_version": "harness-evidence-v1",
            "status": status,
            "task_id": task_id,
            "binding": {
                "task_id": task_id,
                "research_question": research_question,
                "focus": focus,
            },
            "items": items,
            "artifacts": artifacts,
            "tool_trace": trace
            or {"request_id": None, "errors": [], "truncated": False},
            "limitations": ["Harness 输出是待审查证据，不自动构成科学结论。"]
            + (
                [f"{failed_extractions} provider reported a failed extraction."]
                if failed_extractions
                else []
            )
            + (
                [
                    f"{failed_outputs} provider output did not contain a valid completed calculation."
                ]
                if failed_outputs
                else []
            )
            + (
                ["Provider returned no verifiable derived calculation artifact."]
                if missing_analysis_output
                else []
            ),
        }
        receipt_path = harness_dir / "receipt.json"
        receipt = _safe_json(receipt, exact_strings=(self.api_key,))
        assert isinstance(receipt, dict)
        _json_dump(receipt_path, receipt)
        receipt["receipt_ref"] = _relative_ref(task_root, receipt_path)
        return receipt

    def collect_evidence(
        self,
        *,
        task_root: Path,
        task_id: str,
        research_question: str,
        focus: str,
        queries: list[str],
        model: str | None = None,
    ) -> dict[str, object]:
        if not queries:
            raise ValueError("queries must not be empty")
        input_text = (
            f"Research question: {research_question}\nFocus: {focus}\n"
            + "Queries:\n"
            + "\n".join(f"- {query}" for query in queries)
            + "\nSearch first, then extract the original pages. Preserve source URLs and locators."
        )
        result = self._collect(
            task_root=task_root,
            task_id=task_id,
            research_question=research_question,
            focus=focus,
            input_text=input_text,
            tools=[{"type": "web_search"}],
            model=model,
            analysis=False,
            enable_thinking=False,
        )
        root = Path(task_root).resolve()
        receipt_ref = result.get("receipt_ref")
        if not isinstance(receipt_ref, str):
            raise ValueError("Harness receipt is missing its task-local reference")
        harness_dir = (root / receipt_ref).parent.resolve()
        _relative_ref(root, harness_dir)
        leads = [
            item
            for item in result.get("items", [])
            if isinstance(item, dict)
            and item.get("source_class") == "external_lead"
            and isinstance(item.get("url"), str)
        ][:5]
        for index, lead in enumerate(leads, start=1):
            url = str(lead["url"])
            try:
                fetched = _fetch_url_text(url, min(self.timeout, 30.0))
            except Exception as exc:
                result.setdefault("limitations", []).append(
                    _safe_json(
                        f"Could not extract {url}: {type(exc).__name__}: {exc}",
                        exact_strings=(self.api_key,),
                    )
                )
                if result.get("status") == "completed":
                    result["status"] = "partial"
                continue
            truncated = bool(getattr(fetched, "truncated", False))
            content = str(fetched)
            if not content:
                result.setdefault("limitations", []).append(
                    f"Could not extract readable text from {url}."
                )
                if result.get("status") == "completed":
                    result["status"] = "partial"
                continue
            if truncated:
                trace = result.get("tool_trace")
                if isinstance(trace, dict):
                    trace["truncated"] = True
                result.setdefault("limitations", []).append(
                    f"Truncated extracted page {url} at {_MAX_FETCH_BYTES} bytes."
                )
                if result.get("status") == "completed":
                    result["status"] = "partial"
            content = _safe_json(content, exact_strings=(self.api_key,))
            assert isinstance(content, str)
            path = harness_dir / "sources" / f"local-extracted-{index}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            raw = path.read_bytes()
            artifact = {
                "path": _relative_ref(root, path),
                "sha256": _sha256_bytes(raw),
                "bytes": len(raw),
                "kind": "retrieved_text",
            }
            result.setdefault("artifacts", []).append(artifact)
            result.setdefault("items", []).append(
                {
                    "evidence_id": f"harness-local-extract-{index}",
                    "tool": "web_extractor_local",
                    "url": url,
                    "title": lead.get("title"),
                    "locator": "retrieved-page-text",
                    "quote_or_excerpt": content[:2000],
                    "source_ref": artifact["path"],
                    "source_class": "retrieved_text",
                    "evidence_scope": "partial_text" if truncated else "full_text",
                    "claim_role": "gap",
                    "limitations": [
                        "网页文本需由 Evidence 检查来源身份、定位和主张蕴含关系。"
                    ]
                    + (
                        [
                            "truncated: 页面达到 Harness 字节上限，内容被截断，不能视为完整原文。"
                        ]
                        if truncated
                        else []
                    ),
                }
            )
        has_retrieval_source = any(
            isinstance(item, dict)
            and item.get("source_class") in {"external_lead", "retrieved_text"}
            for item in result.get("items", [])
        )
        if not has_retrieval_source and result.get("status") == "completed":
            result["status"] = "partial"
            result.setdefault("limitations", []).append(
                "Provider returned no search or extracted source."
            )
        if isinstance(receipt_ref, str):
            _json_dump(
                root / receipt_ref,
                _safe_json(
                    {
                        key: value
                        for key, value in result.items()
                        if key != "receipt_ref"
                    },
                    exact_strings=(self.api_key,),
                ),
            )
        return result

    def run_analysis(
        self,
        *,
        task_root: Path,
        task_id: str,
        research_question: str,
        focus: str,
        input_refs: list[str],
        instructions: str,
        model: str | None = None,
    ) -> dict[str, object]:
        root = Path(task_root).resolve()
        resolved_refs: list[str] = []
        input_records: list[dict[str, object]] = []
        input_blocks: list[str] = []
        total_bytes = 0
        text_suffixes = {".csv", ".json", ".jsonl", ".md", ".tsv", ".txt"}
        for ref in input_refs:
            path = (
                (root / ref).resolve()
                if not Path(ref).is_absolute()
                else Path(ref).resolve()
            )
            _relative_ref(root, path)
            if not path.exists():
                raise ValueError(
                    f"analysis input does not exist in the task workspace: {ref}"
                )
            if path.suffix.casefold() not in text_suffixes:
                raise ValueError(
                    "hosted code analysis accepts only bounded text/CSV/JSON inputs; "
                    f"use a deterministic local tool for {ref}"
                )
            raw = path.read_bytes()
            total_bytes += len(raw)
            if total_bytes > 300_000:
                raise ValueError(
                    "hosted code analysis input exceeds 300000 bytes; use a "
                    "deterministic local tool or the Automatic Experiment stage"
                )
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"analysis input is not UTF-8 text: {ref}") from exc
            relative = _relative_ref(root, path)
            digest = _sha256_bytes(raw)
            resolved_refs.append(relative)
            input_records.append(
                {
                    "source_ref": relative,
                    "sha256": digest,
                    "bytes": len(raw),
                }
            )
            input_blocks.append(
                f"--- BEGIN INPUT {relative} sha256={digest} ---\n"
                f"{content}\n--- END INPUT {relative} ---"
            )
        input_text = (
            f"Research question: {research_question}\nFocus: {focus}\n"
            f"Input files: {', '.join(resolved_refs)}\n"
            + "\n".join(input_blocks)
            + f"\nInstructions: {instructions}\n"
            "Use only the supplied data. MUST invoke the code_interpreter tool and "
            "return its completed execution record; a prose-only answer is invalid. "
            "Write the code and execution result to the response."
        )
        result = self._collect(
            task_root=root,
            task_id=task_id,
            research_question=research_question,
            focus=focus,
            input_text=input_text,
            tools=[{"type": "code_interpreter"}],
            model=model,
            analysis=True,
            enable_thinking=True,
            input_refs=resolved_refs,
        )
        result["analysis_inputs"] = input_records
        receipt_ref = result.get("receipt_ref")
        if isinstance(receipt_ref, str):
            _json_dump(
                root / receipt_ref,
                _safe_json(
                    {
                        key: value
                        for key, value in result.items()
                        if key != "receipt_ref"
                    },
                    exact_strings=(self.api_key,),
                ),
            )
        return result


def write_harness_receipt(task_root: Path, payload: dict[str, object]) -> Path:
    """Persist an already normalized receipt and return its task-local path."""

    task_root = Path(task_root).resolve()
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-blank validated leaf name")
    path = _invocation_harness_dir(task_root, task_id, payload) / "receipt.json"
    _json_dump(path, _safe_json(payload))
    return path
