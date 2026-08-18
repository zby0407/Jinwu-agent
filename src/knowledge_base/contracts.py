"""Closed contracts for the knowledge base (LLM Wiki) subsystem.

The model owns titles, prose content, and lifecycle reasons. Deterministic
code owns identifiers, status transitions, schema validation, and the
promotion gate. Pure standard library only.

Entry shape (stored as one row in ``entries``, JSON text in ``content`` /
``related_ids`` / ``provenance``)::

    {
        "id": "kb_<type>_<slug>_<seq>",
        "type": "concept" | "mechanism" | "data_source" | "experiment_paradigm"
                | "hypothesis_template" | "finding" | "counterexample",
        "title": str,
        "content": {...},                # per-type sub-fields, see CONTENT_FIELDS
        "source_type": "literature" | "textbook" | "dataset_doc"
                       | "historical_run" | "expert" | "derived",
        "source_ref": str,               # DOI / URL / run_id / reviewer / book page
        "confidence": "high" | "medium" | "low",
        "status": "candidate" | "canonical" | "deprecated" | "superseded",
        "valid_range": str,
        "related_ids": [str, ...],
        "provenance": {...},
        "version": int,
        "created_at": str, "updated_at": str, "created_by": str,
    }
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "knowledge-entry-v1"

ENTRY_TYPES = {
    "concept",
    "mechanism",
    "data_source",
    "experiment_paradigm",
    "hypothesis_template",
    "finding",
    "counterexample",
}
SOURCE_TYPES = {
    "literature",
    "textbook",
    "dataset_doc",
    "historical_run",
    "expert",
    "derived",
}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
STATUSES = {"candidate", "canonical", "deprecated", "superseded"}


# Legal lifecycle transitions (R2: everything starts as candidate; the
# promotion gate is the only way into canonical; canonical can never go
# back to candidate; deprecated/superseded never resurrect).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "candidate": {"canonical", "deprecated", "superseded"},
    "canonical": {"deprecated", "superseded"},
    "deprecated": {"superseded"},
    "superseded": {"deprecated"},
}

# Per-type content sub-fields (design doc §4.9.4 / plan §5.1). Required
# fields must be present and non-empty; optional fields may be absent and
# are dropped when empty. List fields hold lists of strings.
CONTENT_FIELDS: dict[str, dict[str, Any]] = {
    "concept": {
        "required": ("definition",),
        "optional": ("physical_notes", "see_also"),
        "list_fields": {"see_also"},
    },
    "mechanism": {
        "required": ("claim",),
        "optional": (
            "supporting_evidence",
            "counter_evidence",
            "controversy",
            "testable_predictions",
        ),
        "list_fields": {"testable_predictions"},
    },
    "data_source": {
        "required": ("collection_method",),
        "optional": ("known_biases", "calibration_history", "coverage"),
        "list_fields": set(),
    },
    "experiment_paradigm": {
        "required": ("design",),
        "optional": ("metrics", "pitfalls"),
        "list_fields": set(),
    },
    "hypothesis_template": {
        "required": ("structure",),
        "optional": ("example", "applicable_when"),
        "list_fields": set(),
    },
    "finding": {
        "required": ("statement", "run_id"),
        "optional": ("effect_size", "uncertainty"),
        "list_fields": set(),
    },
    "counterexample": {
        "required": ("statement", "run_id"),
        "optional": ("effect_size", "uncertainty"),
        "list_fields": set(),
    },
}

_ENTRY_ID_PATTERN = re.compile(
    r"^kb_(?P<type>concept|mechanism|data_source|experiment_paradigm|"
    r"hypothesis_template|finding|counterexample)"
    r"_(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]{0,127})_(?P<seq>\d{3,})$"
)


class ContractError(ValueError):
    """A user- or model-correctable contract failure with optional repair metadata."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "contract_validation_failed",
        field_path: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.field_path = field_path
        self.suggestion = suggestion


def _fail(message: str, *, error_code: str, field_path: str, suggestion: str) -> None:
    raise ContractError(
        message,
        error_code=error_code,
        field_path=field_path,
        suggestion=suggestion,
    )


def parse_entry_id(entry_id: str) -> re.Match[str] | None:
    """Return the regex match for a well-formed entry id, else ``None``."""

    return _ENTRY_ID_PATTERN.match(entry_id or "")


def normalize_content(entry_type: str, content: Any) -> dict[str, Any]:
    """Drop empty optional fields and coerce list fields to ``list[str]``."""

    if not isinstance(content, dict):
        _fail(
            "content must be an object with per-type sub-fields",
            error_code="content_not_object",
            field_path="content",
            suggestion="按条目 type 提供结构化 content 对象，例如 concept 需要 definition 字段。",
        )
    spec = CONTENT_FIELDS.get(entry_type, {})
    list_fields = spec.get("list_fields", set())
    known = set(spec.get("required", ())) | set(spec.get("optional", ()))
    normalized: dict[str, Any] = {}
    for key, value in content.items():
        if key not in known:
            continue  # unknown fields are rejected in validate_content
        if key in list_fields:
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                items = [str(item) for item in value if str(item).strip()]
                if items:
                    normalized[key] = items
            continue
        if value is None:
            continue
        text = str(value)
        if text.strip():
            normalized[key] = text
    return normalized


def validate_content(entry_type: str, content: Any) -> dict[str, Any]:
    """Validate per-type content sub-fields; return the normalized content."""

    if entry_type not in CONTENT_FIELDS:
        _fail(
            f"unknown entry type: {entry_type!r}",
            error_code="unknown_entry_type",
            field_path="type",
            suggestion=f"type 必须是 {sorted(ENTRY_TYPES)} 之一。",
        )
    if not isinstance(content, dict):
        _fail(
            "content must be an object with per-type sub-fields",
            error_code="content_not_object",
            field_path="content",
            suggestion="按条目 type 提供结构化 content 对象，例如 concept 需要 definition 字段。",
        )
    spec = CONTENT_FIELDS[entry_type]
    known = set(spec["required"]) | set(spec["optional"])
    for key in content:
        if key not in known:
            _fail(
                f"content field {key!r} is not defined for type {entry_type!r}",
                error_code="unknown_content_field",
                field_path=f"content.{key}",
                suggestion=(
                    f"{entry_type} 条目的合法 content 子字段为 {sorted(known)}；"
                    "删除其他字段。"
                ),
            )
    normalized = normalize_content(entry_type, content)
    for key in spec["required"]:
        if key not in normalized:
            _fail(
                f"content.{key} is required for type {entry_type!r}",
                error_code="content_required_field_missing",
                field_path=f"content.{key}",
                suggestion=f"补齐 {entry_type} 条目的必填子字段 {key}。",
            )
    for key in spec["list_fields"]:
        value = normalized.get(key)
        if value is not None and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            _fail(
                f"content.{key} must be a list of strings",
                error_code="content_field_type_invalid",
                field_path=f"content.{key}",
                suggestion=f"把 content.{key} 改为字符串数组。",
            )
    return normalized


def validate_entry(entry: dict[str, Any], *, require_id: bool = True) -> dict[str, Any]:
    """Validate a full knowledge entry; return it with normalized content.

    Raises ``ContractError`` on any violation so callers (tools, importer)
    can surface a model-correctable message.
    """

    if not isinstance(entry, dict):
        _fail(
            "entry must be an object",
            error_code="entry_not_object",
            field_path="",
            suggestion="提供完整的知识条目 JSON 对象。",
        )
    entry_id = entry.get("id", "")
    if require_id:
        if not isinstance(entry_id, str) or parse_entry_id(entry_id) is None:
            _fail(
                f"invalid entry id: {entry_id!r}",
                error_code="invalid_entry_id",
                field_path="id",
                suggestion="id 格式为 kb_<type>_<slug>_<seq>，例如 kb_concept_sunspot_cycle_001。",
            )
    entry_type = entry.get("type", "")
    if entry_type not in ENTRY_TYPES:
        _fail(
            f"unknown entry type: {entry_type!r}",
            error_code="unknown_entry_type",
            field_path="type",
            suggestion=f"type 必须是 {sorted(ENTRY_TYPES)} 之一。",
        )
    if require_id and parse_entry_id(entry_id) is not None:
        id_type = parse_entry_id(entry_id).group("type")  # type: ignore[union-attr]
        if id_type != entry_type:
            _fail(
                f"entry id prefix type {id_type!r} does not match type {entry_type!r}",
                error_code="entry_id_type_mismatch",
                field_path="id",
                suggestion="id 中的 type 段必须与条目 type 一致。",
            )
    title = entry.get("title", "")
    if not isinstance(title, str) or not title.strip():
        _fail(
            "title must be a non-empty string",
            error_code="title_missing",
            field_path="title",
            suggestion="提供一句话标题。",
        )
    source_type = entry.get("source_type", "")
    if source_type not in SOURCE_TYPES:
        _fail(
            f"unknown source_type: {source_type!r}",
            error_code="unknown_source_type",
            field_path="source_type",
            suggestion=f"source_type 必须是 {sorted(SOURCE_TYPES)} 之一。",
        )
    source_ref = entry.get("source_ref", "")
    if not isinstance(source_ref, str) or not source_ref.strip():
        _fail(
            "source_ref must be a non-empty string (DOI / URL / run_id / reviewer / page)",
            error_code="source_ref_missing",
            field_path="source_ref",
            suggestion="提供可追溯来源：DOI、URL、run_id、审核人或书页。",
        )
    confidence = entry.get("confidence", "")
    if confidence not in CONFIDENCE_LEVELS:
        _fail(
            f"unknown confidence: {confidence!r}",
            error_code="unknown_confidence",
            field_path="confidence",
            suggestion=f"confidence 必须是 {sorted(CONFIDENCE_LEVELS)} 之一。",
        )
    status = entry.get("status", "")
    if status not in STATUSES:
        _fail(
            f"unknown status: {status!r}",
            error_code="unknown_status",
            field_path="status",
            suggestion=f"status 必须是 {sorted(STATUSES)} 之一。",
        )
    related_ids = entry.get("related_ids", [])
    if not isinstance(related_ids, list) or not all(
        isinstance(item, str) for item in related_ids
    ):
        _fail(
            "related_ids must be a list of entry id strings",
            error_code="related_ids_invalid",
            field_path="related_ids",
            suggestion="related_ids 改为条目 id 字符串数组。",
        )
    version = entry.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        _fail(
            "version must be a positive integer",
            error_code="version_invalid",
            field_path="version",
            suggestion="version 从 1 开始的整数。",
        )
    provenance = entry.get("provenance", {})
    if provenance is None:
        provenance = {}
    if not isinstance(provenance, dict):
        _fail(
            "provenance must be an object",
            error_code="provenance_invalid",
            field_path="provenance",
            suggestion="provenance 记录晋升理由、支持 run_id 列表与审核人，使用 JSON 对象。",
        )
    normalized = dict(entry)
    normalized["content"] = validate_content(entry_type, entry.get("content"))
    normalized["provenance"] = provenance
    normalized.setdefault("related_ids", [])
    normalized.setdefault("valid_range", "")
    return normalized


def check_status_transition(from_status: str, to_status: str) -> None:
    """Enforce the lifecycle state machine.

    Legal moves: candidate→canonical (promotion gate only), *→deprecated,
    *→superseded. canonical can never return to candidate; no self-moves.
    """

    for status, path in ((from_status, "from_status"), (to_status, "to_status")):
        if status not in STATUSES:
            _fail(
                f"unknown status: {status!r}",
                error_code="unknown_status",
                field_path=path,
                suggestion=f"status 必须是 {sorted(STATUSES)} 之一。",
            )
    if to_status == "candidate":
        _fail(
            f"transition {from_status} -> candidate is not allowed",
            error_code="transition_to_candidate_forbidden",
            field_path="status",
            suggestion="条目一旦离开 candidate 就不能回退；新知识请重新 propose 一条 candidate。",
        )
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        _fail(
            f"transition {from_status} -> {to_status} is not allowed",
            error_code="illegal_status_transition",
            field_path="status",
            suggestion=(
                "合法迁移：candidate→canonical/deprecated/superseded，"
                "canonical→deprecated/superseded，deprecated→superseded，"
                "superseded→deprecated。"
            ),
        )


# literature distill anti-hallucination contract (plan §5.3)
# ----------------------------------------------------------------------
DISTILL_VERSION = "literature-distill-v1"
QUOTE_MAX_WORDS = 40
EVIDENCE_GAP = "evidence_gap"


def normalize_quote_text(text: Any) -> str:
    """Case- and whitespace-normalized form for quote grounding checks."""

    return " ".join(str(text).casefold().split())


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """True when ``quote`` is a verbatim substring of ``source_text`` after
    case/whitespace normalization."""

    needle = normalize_quote_text(quote)
    return bool(needle) and needle in normalize_quote_text(source_text)


def validate_distill_content(
    entry_type: str, content: Any, source_text: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Validate LLM-distilled content against the cached literature text.

    Every evidence-bearing field must be an object
    ``{"text": str|[str], "quote": str, "location": str}`` whose quote is at
    most ``QUOTE_MAX_WORDS`` words and hits the cached source text verbatim
    (normalized substring); otherwise the field must be the string
    ``"evidence_gap"`` or ``{"evidence_gap": note}`` and is dropped from the
    stored content. Required fields may not be gaps.

    Returns ``(normalized_content, evidence_map, evidence_gaps)`` where
    evidence_map records ``{field: {"quote", "location"}}`` for provenance.
    Raises ``ContractError`` (``quote_not_grounded`` /
    ``required_field_ungrounded`` / ...) on any violation.
    """

    if entry_type not in CONTENT_FIELDS:
        _fail(
            f"unknown entry type: {entry_type!r}",
            error_code="unknown_entry_type",
            field_path="type",
            suggestion=f"type 必须是 {sorted(ENTRY_TYPES)} 之一。",
        )
    if not isinstance(content, dict):
        _fail(
            "distill content must be an object mapping content fields to "
            '{text, quote, location} evidence objects or "evidence_gap"',
            error_code="content_not_object",
            field_path="content",
            suggestion='每个证据性字段提供 {text, quote, location}；无原文支撑的字段写 "evidence_gap"。',
        )
    if not isinstance(source_text, str) or not source_text.strip():
        _fail(
            "no cached source text is available for quote grounding",
            error_code="source_text_unavailable",
            field_path="source_id",
            suggestion="先运行 lit_search / lit_fetch，确保该文献的摘要已缓存。",
        )
    spec = CONTENT_FIELDS[entry_type]
    known = set(spec["required"]) | set(spec["optional"])
    list_fields = spec.get("list_fields", set())
    normalized: dict[str, Any] = {}
    evidence_map: dict[str, Any] = {}
    gaps: list[dict[str, str]] = []
    for key, value in content.items():
        field_path = f"content.{key}"
        if key not in known:
            _fail(
                f"content field {key!r} is not defined for type {entry_type!r}",
                error_code="unknown_content_field",
                field_path=field_path,
                suggestion=f"{entry_type} 条目的合法 content 子字段为 {sorted(known)}。",
            )
        note = ""
        if isinstance(value, str) and value.strip() == EVIDENCE_GAP:
            is_gap = True
        elif isinstance(value, dict) and EVIDENCE_GAP in value:
            is_gap = True
            raw_note = value.get(EVIDENCE_GAP)
            note = raw_note if isinstance(raw_note, str) else ""
        else:
            is_gap = False
        if is_gap:
            if key in spec["required"]:
                _fail(
                    f"content.{key} is required for type {entry_type!r} and cannot "
                    "be an evidence_gap",
                    error_code="required_field_ungrounded",
                    field_path=field_path,
                    suggestion="必填字段必须有原文 quote 支撑；该文献支撑不了此条目类型时请更换 entry_type 或放弃蒸馏。",
                )
            gaps.append({"field": key, "note": note})
            continue
        if not isinstance(value, dict):
            _fail(
                f'content.{key} must be an evidence object or "evidence_gap"',
                error_code="distill_field_shape_invalid",
                field_path=field_path,
                suggestion='证据性字段必须是 {text, quote, location} 对象；无原文支撑时写 "evidence_gap"。',
            )
        text = value.get("text")
        if key in list_fields:
            if isinstance(text, str):
                text = [text]
            if (
                not isinstance(text, list)
                or not all(isinstance(item, str) and item.strip() for item in text)
                or not text
            ):
                _fail(
                    f"content.{key}.text must be a non-empty list of strings",
                    error_code="distill_text_invalid",
                    field_path=f"{field_path}.text",
                    suggestion=f"content.{key} 是列表字段，text 提供字符串数组。",
                )
        elif not isinstance(text, str) or not text.strip():
            _fail(
                f"content.{key}.text must be a non-empty string",
                error_code="distill_text_invalid",
                field_path=f"{field_path}.text",
                suggestion="text 是蒸馏后的字段正文（字符串）。",
            )
        quote = value.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            _fail(
                f"content.{key}.quote must be a non-empty verbatim quote",
                error_code="quote_missing",
                field_path=f"{field_path}.quote",
                suggestion="quote 为原文逐字段落（≤40 词），不得改写。",
            )
        if len(quote.split()) > QUOTE_MAX_WORDS:
            _fail(
                f"content.{key}.quote exceeds {QUOTE_MAX_WORDS} words",
                error_code="quote_too_long",
                field_path=f"{field_path}.quote",
                suggestion=f"quote 截取不超过 {QUOTE_MAX_WORDS} 词的连续原文。",
            )
        location = value.get("location")
        if not isinstance(location, str) or not location.strip():
            _fail(
                f"content.{key}.location must name where the quote sits",
                error_code="location_missing",
                field_path=f"{field_path}.location",
                suggestion="location 标明 quote 位置（如 abstract / paragraph 2）。",
            )
        if not quote_is_grounded(quote, source_text):
            _fail(
                f"content.{key}.quote does not appear verbatim in the cached "
                "source text",
                error_code="quote_not_grounded",
                field_path=f"{field_path}.quote",
                suggestion="quote 必须在该文献缓存文本中原样命中（大小写/空白归一化后子串匹配）；找不到就请改写为原文摘录或标 evidence_gap。",
            )
        normalized[key] = text
        evidence_map[key] = {"quote": quote.strip(), "location": location.strip()}
    missing_required = [key for key in spec["required"] if key not in normalized]
    if missing_required:
        _fail(
            f"distill content is missing required fields: {missing_required}",
            error_code="content_required_field_missing",
            field_path=f"content.{missing_required[0]}",
            suggestion=f"补齐 {entry_type} 条目的必填子字段 {missing_required}（含 quote 支撑）。",
        )
    return normalized, evidence_map, gaps
