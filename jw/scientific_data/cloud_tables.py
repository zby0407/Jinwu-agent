"""Cloud table recognition for PDF pages the local text layer cannot read.

Local extraction is the default and always works offline.  This module is the
opt-in escalation for scanned or image-only pages: the page is rendered to a
PNG and sent to a configured OCR/table API, and the returned cells become the
same :class:`ExtractedTable` contract as every other reader.

Three rules keep the escalation honest:

* **Network is explicit.**  A fresh call needs ``allow_network=True``; without
  it the caller gets a typed warning, never a silent request.
* **Replays are free.**  Every response is cached by file hash, page, provider
  and DPI, so an evaluation run can replay cloud results offline and a demo
  without Wi-Fi still shows the same tables.
* **Failure is data.**  A missing key, a quota error or a timeout becomes a
  ``cloud_ocr_failed:<code>`` warning; it never raises through a workflow.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ExtractedTable, file_digest
from .pdf_layout import PDF_TEXT_WARNING, load_pdf_library
from .table_builder import build_table

CLOUD_OCR_WARNING = "cloud_table_ocr"
CLOUD_CONFIDENCE = 0.45
_BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_BAIDU_TABLE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/table"
_BAIDU_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}

Opener = Callable[[urllib.request.Request, float], bytes]

__all__ = [
    "CLOUD_CONFIDENCE",
    "CLOUD_OCR_WARNING",
    "CloudTableConfig",
    "CloudTableError",
    "extract_cloud_tables",
    "resolve_cloud_config",
]


class CloudTableError(RuntimeError):
    """A cloud call failed in a way the caller should report, not retry blind."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CloudTableConfig:
    """Where to send a page and which credential to use.

    ``provider`` is ``"baidu"`` for Baidu AI Cloud's table OCR (the hosted
    PaddleOCR family) or ``"generic"`` for any JSON endpoint that returns
    ``{"tables": [{"rows": ...}]}`` or a Baidu-shaped ``body`` cell list.
    """

    provider: str = ""
    client_id: str = ""
    client_secret: str = ""
    endpoint: str = ""
    api_key: str = ""
    cache_dir: str = ""
    dpi: int = 200
    timeout: float = 30.0

    @property
    def enabled(self) -> bool:
        if self.provider == "baidu":
            return bool(self.client_id and self.client_secret)
        if self.provider == "generic":
            return bool(self.endpoint)
        return False

    def missing(self) -> tuple[str, ...]:
        if self.provider == "baidu":
            return tuple(
                name
                for name, value in (
                    ("BAIDU_OCR_API_KEY", self.client_id),
                    ("BAIDU_OCR_SECRET_KEY", self.client_secret),
                )
                if not value
            )
        if self.provider == "generic":
            return () if self.endpoint else ("JW_PDF_CLOUD_ENDPOINT",)
        return ("JW_PDF_CLOUD_PROVIDER",)


def _env(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name, "") or "").strip()


def _config_attr(config: Any, name: str) -> str:
    return str(getattr(config, name, "") or "").strip()


def _default_cache_dir(environ: Mapping[str, str]) -> str:
    explicit = _env(environ, "JW_PDF_CLOUD_CACHE_DIR")
    if explicit:
        return explicit
    base = _env(environ, "XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return str(root / "jw" / "pdf_cloud")


def resolve_cloud_config(
    explicit: CloudTableConfig | Mapping[str, Any] | None = None,
    *,
    config: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> CloudTableConfig:
    """Merge an explicit config, the process environment, and saved settings."""

    if isinstance(explicit, CloudTableConfig):
        return explicit
    if isinstance(explicit, Mapping):
        return CloudTableConfig(
            **{
                key: value
                for key, value in explicit.items()
                if key in CloudTableConfig.__dataclass_fields__
            }
        )

    environment = os.environ if environ is None else environ
    if config is None:
        try:
            from jw.config.settings import load_config

            config = load_config()
        except Exception:
            config = None

    client_id = _env(environment, "BAIDU_OCR_API_KEY") or _config_attr(
        config, "baidu_ocr_api_key"
    )
    client_secret = _env(environment, "BAIDU_OCR_SECRET_KEY") or _config_attr(
        config, "baidu_ocr_secret_key"
    )
    endpoint = _env(environment, "JW_PDF_CLOUD_ENDPOINT") or _config_attr(
        config, "pdf_cloud_endpoint"
    )
    api_key = _env(environment, "JW_PDF_CLOUD_API_KEY") or _config_attr(
        config, "pdf_cloud_api_key"
    )
    provider = _env(environment, "JW_PDF_CLOUD_PROVIDER") or _config_attr(
        config, "pdf_cloud_provider"
    )
    if not provider:
        if client_id and client_secret:
            provider = "baidu"
        elif endpoint:
            provider = "generic"

    dpi = _env(environment, "JW_PDF_CLOUD_DPI")
    timeout = _env(environment, "JW_PDF_CLOUD_TIMEOUT")
    try:
        dpi_value = int(dpi) if dpi else 200
    except ValueError:
        dpi_value = 200
    try:
        timeout_value = float(timeout) if timeout else 30.0
    except ValueError:
        timeout_value = 30.0

    return CloudTableConfig(
        provider=provider,
        client_id=client_id,
        client_secret=client_secret,
        endpoint=endpoint,
        api_key=api_key,
        cache_dir=_default_cache_dir(environment),
        dpi=max(72, min(dpi_value, 400)),
        timeout=max(1.0, timeout_value),
    )


def _default_opener(request: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        code = f"http_{exc.code}"
        detail = ""
        if body:
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
                detail = str(
                    payload.get("error_msg")
                    or payload.get("message")
                    or payload.get("error")
                    or ""
                )
                if payload.get("error_code"):
                    code = str(payload["error_code"])
            except (ValueError, AttributeError):
                detail = body.decode("utf-8", "replace")[:200]
        raise CloudTableError(code, detail) from None
    except urllib.error.URLError as exc:
        raise CloudTableError("network_unreachable", str(exc.reason)) from None
    except TimeoutError:
        raise CloudTableError("timeout", f"no response in {timeout:.0f}s") from None


def _json(
    request: urllib.request.Request, opener: Opener, timeout: float
) -> Mapping[str, Any]:
    try:
        raw = opener(request, timeout)
    except CloudTableError:
        raise
    except Exception as exc:
        raise CloudTableError("transport_error", str(exc)) from None
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        raise CloudTableError("invalid_json", "response was not JSON") from None
    if not isinstance(payload, Mapping):
        raise CloudTableError("invalid_json", "response was not an object")
    return payload


def _baidu_token(config: CloudTableConfig, opener: Opener) -> str:
    key = (config.client_id, config.client_secret)
    cached = _BAIDU_TOKENS.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    query = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        }
    )
    payload = _json(
        urllib.request.Request(f"{_BAIDU_TOKEN_URL}?{query}"), opener, config.timeout
    )
    token = str(payload.get("access_token") or "")
    if not token:
        code = str(payload.get("error") or "token_failed")
        raise CloudTableError(code, str(payload.get("error_description") or ""))
    expires = float(payload.get("expires_in") or 2592000)
    _BAIDU_TOKENS[key] = (token, time.time() + max(60.0, expires - 60.0))
    return token


def _first_int(value: object, default: int = 0) -> int:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _matrix_from_cells(
    cells: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], ...]:
    grid: dict[tuple[int, int], str] = {}
    max_row = -1
    max_column = -1
    for cell in cells:
        row = _first_int(cell.get("row", cell.get("row_start", 0)))
        column = _first_int(
            cell.get("column", cell.get("col", cell.get("col_start", 0)))
        )
        text = cell.get(
            "words",
            cell.get("text", cell.get("content", cell.get("word", ""))),
        )
        grid[(row, column)] = str(text or "").strip()
        max_row = max(max_row, row)
        max_column = max(max_column, column)
    if max_row < 1 or max_column < 1:
        return ()
    rows = [
        [grid.get((row, column), "") for column in range(max_column + 1)]
        for row in range(max_row + 1)
    ]
    while rows and not any(rows[0]):
        rows.pop(0)
    while rows and not any(rows[-1]):
        rows.pop()
    return tuple(tuple(row) for row in rows) if len(rows) >= 2 else ()


def _matrices_from_payload(
    payload: Mapping[str, Any],
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Accept Baidu cell lists and the simpler generic row shapes."""

    if payload.get("error_code"):
        raise CloudTableError(
            str(payload["error_code"]), str(payload.get("error_msg") or "")
        )
    for key in ("tables_result", "forms"):
        found = payload.get(key)
        if isinstance(found, list) and found:
            matrices = tuple(
                matrix
                for item in found
                if isinstance(item, Mapping)
                for matrix in (_matrix_from_cells(item.get("body") or ()),)
                if matrix
            )
            if matrices:
                return matrices
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        first = tables[0]
        if isinstance(first, Mapping) and "body" in first:
            return tuple(
                matrix
                for item in tables
                if isinstance(item, Mapping)
                for matrix in (_matrix_from_cells(item.get("body") or ()),)
                if matrix
            )
        if isinstance(first, Mapping) and "rows" in first:
            return tuple(
                _matrix(item["rows"])
                for item in tables
                if isinstance(item, Mapping) and _matrix(item.get("rows") or ())
            )
        if isinstance(first, list):
            matrix = _matrix(first)
            return (matrix,) if matrix else ()
    rows = payload.get("rows")
    if isinstance(rows, list):
        matrix = _matrix(rows)
        return (matrix,) if matrix else ()
    cells = payload.get("cells")
    if isinstance(cells, list):
        matrix = _matrix_from_cells(cells)
        return (matrix,) if matrix else ()
    return ()


def _matrix(rows: object) -> tuple[tuple[str, ...], ...]:
    matrix: list[tuple[str, ...]] = []
    for row in rows or ():  # type: ignore[union-attr]
        if not isinstance(row, (list, tuple)):
            continue
        cells = tuple("" if cell is None else str(cell).strip() for cell in row)
        if any(cells):
            matrix.append(cells)
    if len(matrix) < 2:
        return ()
    width = max(len(row) for row in matrix)
    padded = tuple(tuple(row) + ("",) * (width - len(row)) for row in matrix)
    keep = [index for index in range(width) if any(row[index] for row in padded)]
    if len(keep) < 2:
        return ()
    return tuple(tuple(row[index] for index in keep) for row in padded)


def _cache_path(config: CloudTableConfig, path: str, page: int) -> Path | None:
    digest = file_digest(path)
    if not digest:
        return None
    endpoint = config.endpoint or config.provider
    suffix = f"d{config.dpi}"
    fingerprint = base64.urlsafe_b64encode(
        hashlib.sha256(endpoint.encode()).digest()
    ).decode()[:8]
    return (
        Path(config.cache_dir or _default_cache_dir(os.environ))
        / digest.removeprefix("sha256:")[:32]
        / f"p{page}-{config.provider}-{fingerprint}-{suffix}.json"
    )


def _read_cache(
    cache_path: Path | None,
) -> tuple[tuple[tuple[str, ...], ...], ...] | None:
    if cache_path is None or not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("tables")
    if not isinstance(raw, list):
        return None
    return tuple(
        tuple(tuple(str(cell) for cell in row) for row in table)
        for table in raw
        if isinstance(table, (list, tuple))
    )


def _write_cache(
    cache_path: Path | None, matrices: tuple[tuple[tuple[str, ...], ...], ...]
) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "tables": [[list(row) for row in table] for table in matrices],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # A read-only cache is a missed optimisation, not a failed extraction.
        pass


def _baidu_page(
    config: CloudTableConfig, png: bytes, opener: Opener
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    token = _baidu_token(config, opener)
    body = urllib.parse.urlencode(
        {"image": base64.b64encode(png).decode("ascii")}
    ).encode("ascii")
    request = urllib.request.Request(
        f"{_BAIDU_TABLE_URL}?access_token={urllib.parse.quote(token)}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return _matrices_from_payload(_json(request, opener, config.timeout))


def _generic_page(
    config: CloudTableConfig, png: bytes, page: int, opener: Opener
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    body = json.dumps(
        {
            "image_base64": base64.b64encode(png).decode("ascii"),
            "mime": "image/png",
            "page": page,
            "dpi": config.dpi,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint, data=body, headers=headers, method="POST"
    )
    return _matrices_from_payload(_json(request, opener, config.timeout))


def _cloud_matrices(
    config: CloudTableConfig, png: bytes, page: int, opener: Opener
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    if config.provider == "baidu":
        return _baidu_page(config, png, opener)
    if config.provider == "generic":
        return _generic_page(config, png, page, opener)
    raise CloudTableError("provider_unknown", config.provider)


def extract_cloud_tables(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    pages: Sequence[int] | None = None,
    config: CloudTableConfig | None = None,
    allow_network: bool = False,
    opener: Opener | None = None,
    max_tables: int = 20,
) -> tuple[tuple[ExtractedTable, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
    """Recognise tables on selected pages, using cache when a call is refused."""

    target = str(path)
    resolved = config or resolve_cloud_config()
    if not resolved.enabled:
        return (), (), ("cloud_ocr_not_configured",)

    fitz = load_pdf_library()
    if fitz is None:
        return (), (), ("cloud_ocr_unavailable:pymupdf_not_installed",)
    try:
        document = fitz.open(target)
    except Exception as exc:
        return (), (), (f"cloud_ocr_unavailable:{exc}",)

    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    transport = opener or _default_opener
    try:
        wanted = list(pages) if pages else list(range(1, document.page_count + 1))
        for page_number in wanted:
            if page_number < 1 or page_number > document.page_count:
                continue
            cache_path = _cache_path(resolved, target, page_number)
            cached = _read_cache(cache_path)
            cache_hit = cached is not None
            if cached is None:
                if not allow_network:
                    warnings.append("cloud_ocr_network_disabled")
                    continue
                try:
                    page = document[page_number - 1]
                    png = page.get_pixmap(dpi=resolved.dpi, alpha=False).tobytes("png")
                    cached = _cloud_matrices(resolved, png, page_number, transport)
                except CloudTableError as exc:
                    warnings.append(f"cloud_ocr_failed:{exc.code}")
                    continue
                except Exception as exc:
                    warnings.append(f"cloud_ocr_failed:unexpected:{exc}")
                    continue
                if not cached:
                    warnings.append("cloud_ocr_empty")
                    _write_cache(cache_path, ())
                    continue
                _write_cache(cache_path, cached)
            elif not cached:
                warnings.append("cloud_ocr_empty")
                continue
            for position, matrix in enumerate(cached):
                table = build_table(
                    matrix,
                    source_id=source_id,
                    table_id=f"{table_id_prefix}:p{page_number}:c{position}",
                    table_index=position,
                    page=page_number,
                    path=target,
                    lines=["\t".join(row) for row in matrix],
                    first=0,
                    last=len(matrix) - 1,
                    columns_from="cloud_ocr",
                    confidence_score=CLOUD_CONFIDENCE,
                    warning=CLOUD_OCR_WARNING,
                    extractor="jw.scientific_data.cloud_tables.extract_cloud_tables",
                    format="pdf_cloud_ocr",
                    extra_locator={
                        "provider": resolved.provider,
                        "dpi": resolved.dpi,
                        "cache_hit": cache_hit,
                    },
                )
                table.parse_warnings = (
                    PDF_TEXT_WARNING,
                    CLOUD_OCR_WARNING,
                    *(
                        warning
                        for warning in table.parse_warnings
                        if warning not in {PDF_TEXT_WARNING, CLOUD_OCR_WARNING}
                    ),
                )
                tables.append(table)
                if len(tables) >= max_tables:
                    break
            if len(tables) >= max_tables:
                break
    finally:
        document.close()
    return tuple(tables), (), tuple(dict.fromkeys(warnings))
