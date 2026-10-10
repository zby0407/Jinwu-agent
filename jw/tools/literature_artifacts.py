"""Bounded evidence reads and Markdown outputs for literature-only requests.

These tools never run analysis or grant a scientific-review verdict. Sources
must belong to the active task's hash-bound input manifest; reports can only be
saved as Markdown directly inside that task's outputs directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.workspaces import (
    binding_from_config,
    resolve_scoped_path,
    scope_thread_id,
    workspace_root_from_config,
)

from .registry import register_tool_bundle

_TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".tsv", ".json", ".html", ".xml"})
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MAX_READ_CHARACTERS = 24_000
_MAX_REPORT_BYTES = 1024 * 1024


def _result(**payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _task_root(config: RunnableConfig | None) -> Path:
    if not scope_thread_id(config):
        raise ValueError("an initialized thread workspace is required")
    binding = binding_from_config(config)
    if binding is None or binding.legacy:
        raise ValueError("an isolated task workspace is required; start a new thread")
    return workspace_root_from_config(config)


def _sources(config: RunnableConfig | None) -> list[dict[str, object]]:
    # Reuse the same manifest/hash authority as the data tools, without exposing
    # their computation or review-stage entry points to a literature request.
    from .solar_feature import _eligible_input_records

    _task_root(config)
    return [
        record
        for record in _eligible_input_records(config)
        if PurePosixPath(str(record["path"])).suffix.lower() in _TEXT_SUFFIXES
        and int(record["bytes"]) <= _MAX_SOURCE_BYTES
    ]


@tool(parse_docstring=True)
def literature_sources_list(config: RunnableConfig = None) -> str:
    """List the current task's hash-verified, readable evidence inputs.

    Includes task inputs and registered project inputs, never another thread's
    files. PDF/binary sources must first be supplied as extracted UTF-8 text.
    Source verification confirms file identity, not scientific correctness.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with source references, hashes, byte counts and registration roles.
    """
    try:
        return _result(
            status="ok", sources=_sources(config), review_status="not_reviewed"
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def literature_source_read(
    source_ref: str,
    offset: int = 0,
    limit: int = 12_000,
    config: RunnableConfig = None,
) -> str:
    """Read a bounded text excerpt from a listed, hash-bound evidence source.

    This is source inspection only: no computations, experiments or source
    edits. Read additional pages using next_offset before claiming full-text
    coverage. The file is checked against its manifest again on every call.

    Args:
        source_ref: Exact path returned by literature_sources_list.
        offset: Zero-based character offset into the UTF-8 source text.
        limit: Characters to return, between 1 and 24000.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON containing the excerpt, offsets, source hash and total text length.
    """
    try:
        if offset < 0 or not 1 <= limit <= _MAX_READ_CHARACTERS:
            raise ValueError("offset must be nonnegative and limit must be 1..24000")
        record = next(
            (item for item in _sources(config) if item["path"] == source_ref), None
        )
        if record is None:
            raise ValueError(
                "source_ref is not a readable hash-bound input for this task"
            )
        path = resolve_scoped_path(
            source_ref, config, allow_project=record["source_group"] == "project_inputs"
        )
        raw = path.read_bytes()
        # Check the actual bytes being returned, not only an earlier stat/hash.
        if (
            len(raw) != record["bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError("source changed since its task manifest was recorded")
        content = raw.decode("utf-8-sig")
        if "\x00" in content:
            raise ValueError("source is not a UTF-8 text document")
        if offset > len(content):
            raise ValueError("offset exceeds source text length")
        end = min(offset + limit, len(content))
        return _result(
            status="ok",
            source_ref=source_ref,
            sha256=record["sha256"],
            content=content[offset:end],
            offset=offset,
            next_offset=end if end < len(content) else None,
            total_characters=len(content),
            review_status="not_reviewed",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def literature_report_save(
    path: str,
    markdown: str,
    overwrite: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Save and verify a literature report as outputs/<filename>.md in this task.

    Only a single Markdown filename directly under outputs is allowed. No
    inputs, project files, code or review records can be changed. A successful
    save proves file persistence only, never scientific acceptance. Keep source
    citations and evidence gaps in the report; do not claim independent review.

    Args:
        path: Task-relative outputs/<filename>.md, optionally prefixed with /.
        markdown: Complete nonempty report text, at most 1 MiB in UTF-8.
        overwrite: Explicitly replace an existing report when true.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the verified report reference, size, SHA-256 and review boundary.
    """
    try:
        root = _task_root(config)
        relative = path.removeprefix("/")
        parts = relative.split("/")
        if (
            len(parts) != 2
            or parts[0] != "outputs"
            or not parts[1]
            or parts[1].startswith(".")
            or not parts[1].lower().endswith(".md")
            or any(character in relative for character in '\\:*?"<>|')
            or any(ord(character) < 32 for character in relative)
        ):
            raise ValueError(
                "path must be outputs/<filename>.md with no traversal or subdirectories"
            )
        encoded = markdown.encode("utf-8")
        if not markdown.strip() or len(encoded) > _MAX_REPORT_BYTES:
            raise ValueError(
                "report must contain 1..1048576 UTF-8 bytes of nonempty Markdown"
            )
        outputs = root / "outputs"
        target = root / relative
        if outputs.resolve() != outputs or target.resolve() != target:
            raise ValueError(
                "report path must not use symlinks or redirected directories"
            )
        if target.exists() and (not overwrite or not target.is_file()):
            raise ValueError(
                "report already exists; use overwrite=true for an explicit revision"
            )
        from .solar_feature import _atomic_write_text

        _atomic_write_text(target, markdown)
        written = target.read_bytes()
        if written != encoded:
            raise OSError("saved report did not match the requested UTF-8 content")
        return _result(
            status="saved",
            artifact_ref=relative,
            bytes=len(written),
            sha256=hashlib.sha256(written).hexdigest(),
            review_status="not_reviewed",
            limitation="File persistence only; no independent scientific review was performed.",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


LITERATURE_ARTIFACT_TOOLS = [
    literature_sources_list,
    literature_source_read,
    literature_report_save,
]
register_tool_bundle("literature-artifacts", LITERATURE_ARTIFACT_TOOLS)
