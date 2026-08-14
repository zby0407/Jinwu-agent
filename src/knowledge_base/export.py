"""Markdown export / re-import for knowledge entries (plan §5.1).

Every store write is mirrored to
``<workspace>/knowledge_base/<type>/<id>.md``: YAML frontmatter carrying all
metadata fields, body
carrying the per-type content sub-fields as ``## <field>`` sections (list
fields rendered as ``- `` bullets). Frontmatter values are JSON-serialized
scalars/arrays — valid YAML 1.2 and round-trippable without a YAML
dependency. ``import_entry_file`` parses the same format back (and also
tolerates hand-written plain-YAML frontmatter such as the original
llm_wiki notes).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from .contracts import CONTENT_FIELDS, ContractError, validate_entry

_FRONTMATTER_KEYS = (
    "id",
    "type",
    "title",
    "source_type",
    "source_ref",
    "confidence",
    "status",
    "valid_range",
    "related_ids",
    "provenance",
    "version",
    "created_at",
    "updated_at",
    "created_by",
)

_SECTION_HEADING = re.compile(r"^##\s+(?P<field>\S+)\s*$", re.MULTILINE)
_CATALOG_LOCK = threading.RLock()


def export_path(entry: dict[str, Any], export_dir: str | Path) -> Path:
    return Path(export_dir) / str(entry["type"]) / f"{entry['id']}.md"


def render_markdown(entry: dict[str, Any]) -> str:
    """Serialize an entry to frontmatter + sectioned markdown."""

    lines = ["---"]
    for key in _FRONTMATTER_KEYS:
        value = entry.get(key)
        if value is None:
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    lines.append("")
    content = entry.get("content", {})
    spec = CONTENT_FIELDS.get(entry.get("type", ""), {})
    ordered = list(spec.get("required", ())) + [
        field for field in spec.get("optional", ()) if field in content
    ]
    list_fields = spec.get("list_fields", set())
    for field in ordered:
        if field not in content:
            continue
        value = content[field]
        lines.append(f"## {field}")
        lines.append("")
        if field in list_fields and isinstance(value, list):
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_entry(entry: dict[str, Any], export_dir: str | Path) -> Path:
    """Write one entry and refresh the LLM-Wiki navigation files."""

    path = export_path(entry, export_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(entry), encoding="utf-8")
    refresh_wiki_catalog(export_dir, changed_entry=entry)
    return path


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",")]
    return text.strip("'\"")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---`` frontmatter from the body; tolerate plain YAML values."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, Any] = {}
    body_start = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        key, sep, raw = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = _parse_scalar(raw)
    return meta, "\n".join(lines[body_start:])


def parse_markdown(text: str) -> dict[str, Any]:
    """Parse an exported markdown file back into an entry dict."""

    meta, body = parse_frontmatter(text)
    if not meta.get("id"):
        raise ContractError(
            "markdown file has no frontmatter id",
            error_code="import_id_missing",
            field_path="id",
            suggestion="在 frontmatter 中提供条目 id（kb_<type>_<slug>_<seq>）。",
        )
    content: dict[str, Any] = {}
    headings = list(_SECTION_HEADING.finditer(body))
    for position, heading in enumerate(headings):
        field = heading.group("field")
        start = heading.end()
        end = (
            headings[position + 1].start()
            if position + 1 < len(headings)
            else len(body)
        )
        section = body[start:end].strip("\n")
        lines = section.splitlines()
        if lines and all(
            not line.strip() or line.strip().startswith("- ") for line in lines
        ):
            items = [line.strip()[2:].strip() for line in lines if line.strip()]
            content[field] = items
        else:
            content[field] = section.strip()
    entry: dict[str, Any] = dict(meta)
    entry["content"] = content
    if isinstance(entry.get("related_ids"), str):
        entry["related_ids"] = [entry["related_ids"]]
    if not isinstance(entry.get("related_ids"), list):
        entry["related_ids"] = []
    if not isinstance(entry.get("provenance"), dict):
        entry["provenance"] = {}
    if "version" in entry:
        try:
            entry["version"] = int(entry["version"])
        except (TypeError, ValueError):
            entry["version"] = 1
    return entry


def import_entry_file(path: str | Path) -> dict[str, Any]:
    """Load and validate one markdown entry file."""

    text = Path(path).read_text(encoding="utf-8")
    entry = parse_markdown(text)
    return validate_entry(entry)


_DEFAULT_PURPOSE = """# Wiki 目的

这个科研 Wiki 从文献、数据集说明和可复现实验中持续编译可追溯知识。

## 边界

- 原始来源与生成知识分开保存。
- 每项科学主张都绑定证据、溯源和适用范围。
- 冲突和不确定性必须显式保留，不能静默合并。
- 候选知识只有在获得跨运行复现证据后才能晋升。

## 当前研究方向

项目演进时由研究者维护本节。Agent 在文献搜集、精炼和综合之前必须先读取这里。
"""

_DEFAULT_SCHEMA = """# Wiki 结构

每个生成页面都有 YAML frontmatter 和稳定 id。跨页链接使用兼容 Obsidian 的
`[[type/entry-id|title]]` 语法。

## 页面类型

- `concept`：精确定义及物理解释
- `mechanism`：科学主张、支持证据、反证和可检验预测
- `data_source`：采集方法、覆盖范围、校准和已知偏差
- `experiment_paradigm`：实验设计、指标和失效模式
- `hypothesis_template`：可复用的假设结构及适用条件
- `finding`：来自可追溯运行的可复用结果
- `counterexample`：削弱或限定已有主张的证据

## 生命周期

`candidate → canonical → deprecated/superseded`

原始运行日志和暂态任务产物属于 workspace/history。只有可复用、来源充分的结论才进入 Wiki。
"""


def _read_exported_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.md")):
        try:
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
        if not str(meta.get("id") or "").startswith("kb_"):
            continue
        meta["_path"] = path
        entries.append(meta)
    return entries


def _render_index(entries: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(str(entry.get("type") or "other"), []).append(entry)
    lines = [
        "# Wiki Index",
        "",
        "<!-- generated: do not edit; edit purpose.md or individual pages -->",
        "",
        f"{len(entries)} compiled knowledge pages.",
        "",
    ]
    for entry_type in sorted(groups):
        items = sorted(
            groups[entry_type],
            key=lambda item: (
                str(item.get("status") or ""),
                str(item.get("title") or ""),
            ),
        )
        lines.extend((f"## {entry_type}", ""))
        for item in items:
            path = Path(item["_path"])
            title = str(item.get("title") or item.get("id") or path.stem)
            status = str(item.get("status") or "candidate")
            lines.append(f"- [[{entry_type}/{path.stem}|{title}]] · `{status}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_overview(entries: list[dict[str, Any]]) -> str:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entry in entries:
        entry_type = str(entry.get("type") or "other")
        status = str(entry.get("status") or "unknown")
        by_type[entry_type] = by_type.get(entry_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    recent = sorted(
        entries,
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )[:12]
    lines = [
        "# Wiki Overview",
        "",
        "<!-- generated: do not edit -->",
        "",
        "## State",
        "",
        f"- Pages: {len(entries)}",
        *[f"- {status}: {count}" for status, count in sorted(by_status.items())],
        "",
        "## Coverage by type",
        "",
        *[f"- {entry_type}: {count}" for entry_type, count in sorted(by_type.items())],
        "",
        "## Recently updated",
        "",
    ]
    for entry in recent:
        entry_type = str(entry.get("type") or "other")
        entry_id = str(entry.get("id") or "")
        title = str(entry.get("title") or entry_id)
        updated_at = str(entry.get("updated_at") or "")
        lines.append(
            f"- [[{entry_type}/{entry_id}|{title}]]"
            + (f" · {updated_at}" if updated_at else "")
        )
    lines.append("")
    return "\n".join(lines)


def _append_log(root: Path, entry: dict[str, Any]) -> None:
    path = root / "log.md"
    marker = f"<!-- {entry.get('id')}:v{entry.get('version', 1)} -->"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Wiki Log\n"
    if marker in existing:
        return
    updated_at = str(entry.get("updated_at") or "")
    date = updated_at[:10] if updated_at else "unknown-date"
    block = (
        f"\n## [{date}] compile | {entry.get('title') or entry.get('id')}\n\n"
        f"{marker}\n"
        f"- id: `{entry.get('id')}`\n"
        f"- type: `{entry.get('type')}`\n"
        f"- status: `{entry.get('status')}`\n"
        f"- version: {entry.get('version', 1)}\n"
    )
    path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")


def refresh_wiki_catalog(
    export_dir: str | Path, *, changed_entry: dict[str, Any] | None = None
) -> None:
    """Maintain the source-independent Markdown navigation layer.

    ``purpose.md`` and ``schema.md`` are created once and remain human-editable.
    ``index.md`` and ``overview.md`` are deterministic projections of entry
    pages. ``log.md`` is append-only and idempotent per entry version.
    """

    root = Path(export_dir)
    with _CATALOG_LOCK:
        root.mkdir(parents=True, exist_ok=True)
        purpose_path = root / "purpose.md"
        if not purpose_path.exists():
            purpose_path.write_text(_DEFAULT_PURPOSE, encoding="utf-8")
        schema_path = root / "schema.md"
        if not schema_path.exists():
            schema_path.write_text(_DEFAULT_SCHEMA, encoding="utf-8")
        entries = _read_exported_entries(root)
        (root / "index.md").write_text(_render_index(entries), encoding="utf-8")
        (root / "overview.md").write_text(_render_overview(entries), encoding="utf-8")
        log_path = root / "log.md"
        if not log_path.exists():
            log_path.write_text("# Wiki Log\n", encoding="utf-8")
        if changed_entry is not None:
            _append_log(root, changed_entry)
