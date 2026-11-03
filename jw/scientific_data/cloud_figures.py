"""Cloud chart reading for figures the local pipeline cannot digitise.

``figures.py`` digitises points only when axis ticks, pixels and a calibration
payload exist.  A published chart usually has none of that: it is a raster or a
vector drawing with a caption.  This module is the opt-in escalation for those
pages — the page is rendered to a PNG and sent to a configured multimodal API
with a fixed chart-reading prompt, and the returned markdown/TSV table becomes
an :class:`ExtractedTable` like any other reader's output.

The same three rules as :mod:`jw.scientific_data.cloud_tables` apply:

* network use is explicit (``allow_network=True`` for a fresh call),
* responses are cached by file hash, page, model and DPI, so a run replays
  offline,
* failures are typed warnings, never exceptions through a workflow.

Chart readings are weaker evidence than a text layer: the table is emitted with
confidence 0.4, ``columns_from="cloud_figure"``, ``format="pdf_cloud_figure"``
and a ``cloud_figure_extraction`` warning, and should be checked before use.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ExtractedTable, file_digest
from .pdf_layout import PDF_TEXT_WARNING, load_pdf_library
from .table_builder import build_table

CLOUD_FIGURE_WARNING = "cloud_figure_extraction"
CLOUD_FIGURE_CONFIDENCE = 0.4

FIGURE_PROMPT = (
    "Read the scientific chart in this image and return only its plotted data "
    "as one markdown table with exactly three columns: series | x | y. "
    "One row per plotted data point. Copy the axis values as printed; do not "
    "normalise, smooth or interpolate. Skip fitted curves that have no point "
    "markers. Do not add commentary before or after the table."
)

Opener = Callable[[urllib.request.Request, float], bytes]

__all__ = [
    "CLOUD_FIGURE_CONFIDENCE",
    "CLOUD_FIGURE_WARNING",
    "CloudFigureConfig",
    "CloudFigureError",
    "extract_cloud_figures",
    "resolve_figure_config",
]


class CloudFigureError(RuntimeError):
    """A chart API call failed in a way the caller should report, not retry."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CloudFigureConfig:
    """Where to send a page and which credential to use.

    Only the ``generic`` provider exists: a JSON endpoint that accepts an image
    plus a prompt and answers with a markdown/TSV table (or a ``tables`` list).
    Any hosted multimodal API can be put behind such a gateway.
    """

    provider: str = ""
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    cache_dir: str = ""
    dpi: int = 200
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.provider == "generic" and bool(self.endpoint)

    def missing(self) -> tuple[str, ...]:
        if self.provider == "generic":
            return () if self.endpoint else ("JW_FIGURE_CLOUD_ENDPOINT",)
        return ("JW_FIGURE_CLOUD_PROVIDER",)


def _env(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name, "") or "").strip()


def _config_attr(config: Any, name: str) -> str:
    return str(getattr(config, name, "") or "").strip()


def _default_cache_dir(environ: Mapping[str, str]) -> str:
    explicit = _env(environ, "JW_FIGURE_CLOUD_CACHE_DIR")
    if explicit:
        return explicit
    base = _env(environ, "XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return str(root / "jw" / "pdf_figures")


def resolve_figure_config(
    explicit: CloudFigureConfig | Mapping[str, Any] | None = None,
    *,
    config: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> CloudFigureConfig:
    """Merge an explicit config, the process environment, and saved settings."""

    if isinstance(explicit, CloudFigureConfig):
        return explicit
    if isinstance(explicit, Mapping):
        return CloudFigureConfig(
            **{
                key: value
                for key, value in explicit.items()
                if key in CloudFigureConfig.__dataclass_fields__
            }
        )

    environment = os.environ if environ is None else environ
    if config is None:
        try:
            from jw.config.settings import load_config

            config = load_config()
        except Exception:
            config = None

    endpoint = _env(environment, "JW_FIGURE_CLOUD_ENDPOINT") or _config_attr(
        config, "figure_cloud_endpoint"
    )
    api_key = _env(environment, "JW_FIGURE_CLOUD_API_KEY") or _config_attr(
        config, "figure_cloud_api_key"
    )
    model = _env(environment, "JW_FIGURE_CLOUD_MODEL") or _config_attr(
        config, "figure_cloud_model"
    )
    provider = _env(environment, "JW_FIGURE_CLOUD_PROVIDER") or _config_attr(
        config, "figure_cloud_provider"
    )
    if not provider and endpoint:
        provider = "generic"

    dpi = _env(environment, "JW_FIGURE_CLOUD_DPI")
    timeout = _env(environment, "JW_FIGURE_CLOUD_TIMEOUT")
    try:
        dpi_value = int(dpi) if dpi else 200
    except ValueError:
        dpi_value = 200
    try:
        timeout_value = float(timeout) if timeout else 60.0
    except ValueError:
        timeout_value = 60.0

    return CloudFigureConfig(
        provider=provider,
        endpoint=endpoint,
        api_key=api_key,
        model=model,
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
        raise CloudFigureError(code, detail) from None
    except urllib.error.URLError as exc:
        raise CloudFigureError("network_unreachable", str(exc.reason)) from None
    except TimeoutError:
        raise CloudFigureError("timeout", f"no response in {timeout:.0f}s") from None


def _json(
    request: urllib.request.Request, opener: Opener, timeout: float
) -> Mapping[str, Any]:
    try:
        raw = opener(request, timeout)
    except CloudFigureError:
        raise
    except Exception as exc:
        raise CloudFigureError("transport_error", str(exc)) from None
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        raise CloudFigureError("invalid_json", "response was not JSON") from None
    if not isinstance(payload, Mapping):
        raise CloudFigureError("invalid_json", "response was not an object")
    return payload


def _split_pipe_row(line: str) -> list[str]:
    text = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in text.split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(
        set(cell) <= set("-: ") and "-" in cell for cell in cells
    )


def _rows_from_text(text: str) -> tuple[tuple[str, ...], ...]:
    """Turn a model answer (markdown table, TSV or CSV) into a matrix."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if not cleaned:
        return ()

    lines = [line for line in cleaned.splitlines() if line.strip()]
    pipe_lines = [line for line in lines if "|" in line]
    if len(pipe_lines) >= 2 and len(pipe_lines) >= len(lines) - 2:
        rows = [_split_pipe_row(line) for line in pipe_lines]
        rows = [row for row in rows if not _is_separator(row)]
        return _matrix(rows)

    delimiter = "\t" if "\t" in cleaned else ","
    if delimiter in cleaned:
        parsed = list(csv.reader(io.StringIO(cleaned), delimiter=delimiter))
        return _matrix(parsed)

    return _matrix([line.split() for line in lines])


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


def _matrix_from_mapping(item: Mapping[str, Any]) -> tuple[tuple[str, ...], ...]:
    columns = item.get("columns")
    rows = item.get("rows")
    if isinstance(columns, list) and isinstance(rows, list):
        return _matrix([columns, *rows])
    if isinstance(rows, list):
        return _matrix(rows)
    data = item.get("data")
    if isinstance(data, list):
        return _matrix(data)
    return ()


def _response_text(payload: Mapping[str, Any]) -> str:
    for key in ("text", "content", "markdown", "result", "output"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
            text = choice.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def _matrices_from_payload(
    payload: Mapping[str, Any],
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    if payload.get("error_code"):
        raise CloudFigureError(
            str(payload["error_code"]), str(payload.get("error_msg") or "")
        )
    text = _response_text(payload)
    if text:
        matrix = _rows_from_text(text)
        return (matrix,) if matrix else ()
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        first = tables[0]
        if isinstance(first, list):
            matrix = _matrix(first)
            return (matrix,) if matrix else ()
        if isinstance(first, Mapping):
            return tuple(
                matrix
                for item in tables
                if isinstance(item, Mapping)
                for matrix in (_matrix_from_mapping(item),)
                if matrix
            )
    data = payload.get("data")
    if isinstance(data, list):
        matrix = _matrix(data)
        return (matrix,) if matrix else ()
    return ()


def _cache_path(config: CloudFigureConfig, path: str, page: int) -> Path | None:
    digest = file_digest(path)
    if not digest:
        return None
    endpoint = config.endpoint or config.provider
    fingerprint = hashlib.sha256(f"{endpoint}|{config.model}".encode()).hexdigest()[:8]
    cache_dir = Path(config.cache_dir or _default_cache_dir(os.environ))
    return (
        cache_dir
        / digest.removeprefix("sha256:")[:32]
        / (f"p{page}-figure-{config.provider}-{fingerprint}-d{config.dpi}.json")
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


def _generic_page(
    config: CloudFigureConfig, png: bytes, page: int, opener: Opener
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    body = json.dumps(
        {
            "image_base64": base64.b64encode(png).decode("ascii"),
            "mime": "image/png",
            "page": page,
            "dpi": config.dpi,
            "model": config.model,
            "mode": "chart",
            "prompt": FIGURE_PROMPT,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint, data=body, headers=headers, method="POST"
    )
    return _matrices_from_payload(_json(request, opener, config.timeout))


def extract_cloud_figures(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    pages: Sequence[int] | None = None,
    config: CloudFigureConfig | None = None,
    allow_network: bool = False,
    opener: Opener | None = None,
    max_tables: int = 10,
) -> tuple[tuple[ExtractedTable, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
    """Read charts on selected pages, using cached responses when offline."""

    target = str(path)
    resolved = config or resolve_figure_config()
    if not resolved.enabled:
        return (), (), ("cloud_figure_not_configured",)

    fitz = load_pdf_library()
    if fitz is None:
        return (), (), ("cloud_figure_unavailable:pymupdf_not_installed",)
    try:
        document = fitz.open(target)
    except Exception as exc:
        return (), (), (f"cloud_figure_unavailable:{exc}",)

    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    transport = opener or _default_opener
    try:
        if pages is None:
            wanted: list[int] = [
                index + 1
                for index in range(document.page_count)
                if document[index].get_images()
            ]
        else:
            wanted = [int(page) for page in pages]
        for page_number in wanted:
            if page_number < 1 or page_number > document.page_count:
                continue
            cache_path = _cache_path(resolved, target, page_number)
            cached = _read_cache(cache_path)
            cache_hit = cached is not None
            if cached is None:
                if not allow_network:
                    warnings.append("cloud_figure_network_disabled")
                    continue
                try:
                    page = document[page_number - 1]
                    png = page.get_pixmap(dpi=resolved.dpi, alpha=False).tobytes("png")
                    cached = _generic_page(resolved, png, page_number, transport)
                except CloudFigureError as exc:
                    warnings.append(f"cloud_figure_failed:{exc.code}")
                    continue
                except Exception as exc:
                    warnings.append(f"cloud_figure_failed:unexpected:{exc}")
                    continue
                if not cached:
                    warnings.append("cloud_figure_empty")
                    _write_cache(cache_path, ())
                    continue
                _write_cache(cache_path, cached)
            elif not cached:
                warnings.append("cloud_figure_empty")
                continue
            for position, matrix in enumerate(cached):
                table = build_table(
                    matrix,
                    source_id=source_id,
                    table_id=f"{table_id_prefix}:p{page_number}:fig{position}",
                    table_index=position,
                    page=page_number,
                    path=target,
                    lines=["\t".join(row) for row in matrix],
                    first=0,
                    last=len(matrix) - 1,
                    columns_from="cloud_figure",
                    confidence_score=CLOUD_FIGURE_CONFIDENCE,
                    warning=CLOUD_FIGURE_WARNING,
                    extractor="jw.scientific_data.cloud_figures.extract_cloud_figures",
                    format="pdf_cloud_figure",
                    extra_locator={
                        "provider": resolved.provider,
                        "model": resolved.model,
                        "dpi": resolved.dpi,
                        "cache_hit": cache_hit,
                        "mode": "chart",
                    },
                )
                table.parse_warnings = (
                    PDF_TEXT_WARNING,
                    CLOUD_FIGURE_WARNING,
                    *(
                        warning
                        for warning in table.parse_warnings
                        if warning not in {PDF_TEXT_WARNING, CLOUD_FIGURE_WARNING}
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
