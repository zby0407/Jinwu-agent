#!/usr/bin/env python3
"""P1 一次性初始导入：静态知识雏形 + 历史 runs -> knowledge.db。

内容来源（方案 §6）：
- ``knowledge/`` 4 个 md -> concept 类 canonical（source_type=expert，
  created_by=human，confidence=medium）
- ``jw/skills/solar-cycle/references/llm_wiki/`` 5 个 md -> 按文件名
  前缀类型导入为 canonical（source_type=textbook，confidence=high）
- ``experiment/runs/*/report.md`` 与 ``hypothesis/runs/*/hypotheses.md`` ->
  每个 run 一条 finding 类 candidate（source_type=historical_run，
  source_ref=run_id，confidence=low）

幂等：按 (source_ref, title) 判重，重复运行不产生重复条目。
用法：``PYTHONPATH=src .venv/bin/python knowledge_base/import_initial.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_base.contracts import validate_entry  # noqa: E402
from knowledge_base.export import export_entry, parse_frontmatter  # noqa: E402
from knowledge_base.store import KnowledgeStore, utc_now  # noqa: E402

KNOWLEDGE_DIR = ROOT / "knowledge"
LLM_WIKI_DIR = ROOT / "JW" / "skills" / "solar-cycle" / "references" / "llm_wiki"
EXPERIMENT_RUNS = ROOT / "experiment" / "runs"
HYPOTHESIS_RUNS = ROOT / "hypothesis" / "runs"

# llm_wiki 文件名前缀 -> (条目 type, content 必填字段)
WIKI_PREFIX_TYPES = {
    "concept_": ("concept", "definition"),
    "mechanism_": ("mechanism", "claim"),
    "data_source_": ("data_source", "collection_method"),
    "experiment_paradigm_": ("experiment_paradigm", "design"),
    "hypothesis_template_": ("hypothesis_template", "structure"),
}

_HEADING = re.compile(r"^#{1,2}\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    return re.sub(r"-{2,}", "-", slug)[:48].strip("-") or "entry"


def _insert_once(
    store: KnowledgeStore,
    entry: dict,
    stats: dict,
) -> None:
    """校验 + 判重 + 写库 + 导出；重复条目计入 skipped。"""

    entry = validate_entry(entry)
    if store.find_entry_by_source(entry["source_ref"], entry["title"]) is not None:
        stats["skipped"] += 1
        return
    store.create_entry(entry, changed_by=entry["created_by"], reason="initial import")
    export_entry(entry, store.export_dir)
    stats["inserted"] += 1
    key = f"{entry['type']}/{entry['status']}"
    stats["by_type_status"][key] = stats["by_type_status"].get(key, 0) + 1


def _entry(
    *,
    entry_id: str,
    entry_type: str,
    title: str,
    content: dict,
    source_type: str,
    source_ref: str,
    confidence: str,
    status: str,
    created_by: str,
    valid_range: str = "",
    related_ids: list | None = None,
) -> dict:
    now = utc_now()
    return {
        "id": entry_id,
        "type": entry_type,
        "title": title,
        "content": content,
        "source_type": source_type,
        "source_ref": source_ref,
        "confidence": confidence,
        "status": status,
        "valid_range": valid_range,
        "related_ids": related_ids or [],
        "provenance": {"imported_by": "import_initial", "imported_at": now},
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
    }


def import_knowledge_dir(store: KnowledgeStore, stats: dict) -> None:
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        heading = _HEADING.search(text)
        title = heading.group("title") if heading else path.stem
        entry_id = f"kb_concept_{_slugify(path.stem)}_001"
        _insert_once(
            store,
            _entry(
                entry_id=entry_id,
                entry_type="concept",
                title=title,
                content={"definition": text.strip()},
                source_type="expert",
                source_ref=f"knowledge/{path.name}",
                confidence="medium",
                status="canonical",
                created_by="human",
            ),
            stats,
        )


def import_llm_wiki(store: KnowledgeStore, stats: dict) -> None:
    # 两遍导入：先收集 frontmatter id -> 规范 id 的别名映射（个别历史 id 的
    # type 段不合 kb_<type>_<slug>_<seq> 规范，导入时改写），再重写 related_ids。
    parsed: list[dict] = []
    aliases: dict[str, str] = {}
    for path in sorted(LLM_WIKI_DIR.glob("*.md")):
        matched_prefix = next(
            (prefix for prefix in WIKI_PREFIX_TYPES if path.name.startswith(prefix)),
            None,
        )
        if matched_prefix is None:
            continue
        entry_type, required_field = WIKI_PREFIX_TYPES[matched_prefix]
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        old_id = str(meta.get("id") or "")
        stem_slug = _slugify(path.stem[len(matched_prefix) :])
        new_id = f"kb_{entry_type}_{stem_slug}_001"
        if old_id and old_id != new_id:
            aliases[old_id] = new_id
        parsed.append(
            {
                "meta": meta,
                "body": body,
                "entry_type": entry_type,
                "required_field": required_field,
                "new_id": new_id,
                "fallback_ref": f"llm_wiki/{path.name}",
                "fallback_title": path.stem,
            }
        )

    for item in parsed:
        meta = item["meta"]
        entry_id = item["new_id"]
        title = str(meta.get("title") or item["fallback_title"])
        related = meta.get("related_ids")
        related_ids = [
            aliases.get(str(ref), str(ref)) for ref in related
        ] if isinstance(related, list) else []
        _insert_once(
            store,
            _entry(
                entry_id=entry_id,
                entry_type=item["entry_type"],
                title=title,
                content={item["required_field"]: item["body"].strip()},
                source_type="textbook",
                source_ref=str(meta.get("source_ref") or item["fallback_ref"]),
                confidence="high",
                status="canonical",
                created_by="import_initial",
                valid_range=str(meta.get("valid_range") or ""),
                related_ids=related_ids,
            ),
            stats,
        )


def _first_heading(text: str) -> str:
    match = _HEADING.search(text)
    return match.group("title") if match else ""


def _section_outline(text: str, limit: int = 5) -> str:
    headings = [match.group("title") for match in _HEADING.finditer(text)]
    return "；".join(headings[:limit])


def import_runs(store: KnowledgeStore, stats: dict) -> None:
    targets = [
        (EXPERIMENT_RUNS, "report.md", "实验运行报告"),
        (HYPOTHESIS_RUNS, "hypotheses.md", "假设组合"),
    ]
    for runs_dir, filename, label in targets:
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            artifact = run_dir / filename
            if not run_dir.is_dir() or not artifact.exists():
                continue
            run_id = run_dir.name
            text = artifact.read_text(encoding="utf-8", errors="replace")
            outline = _section_outline(text)
            statement = f"运行 {run_id} 产出《{_first_heading(text) or label}》。"
            if outline:
                statement += f"章节：{outline}。"
            prefix = f"kb_finding_{_slugify(run_id)}_"
            _insert_once(
                store,
                _entry(
                    entry_id=f"{prefix}{store.next_seq(prefix):03d}",
                    entry_type="finding",
                    title=f"{label}：{run_id}",
                    content={"statement": statement, "run_id": run_id},
                    source_type="historical_run",
                    source_ref=run_id,
                    confidence="low",
                    status="candidate",
                    created_by="import_initial",
                ),
                stats,
            )


def main() -> None:
    store = KnowledgeStore()
    stats: dict = {"inserted": 0, "skipped": 0, "by_type_status": {}}
    try:
        import_knowledge_dir(store, stats)
        import_llm_wiki(store, stats)
        import_runs(store, stats)
    finally:
        db_path = store.db_path
        export_dir = store.export_dir
        store.close()
    print(f"db: {db_path}")
    print(f"export: {export_dir}")
    print(f"inserted: {stats['inserted']}  skipped(duplicate): {stats['skipped']}")
    for key in sorted(stats["by_type_status"]):
        print(f"  {key}: {stats['by_type_status'][key]}")


if __name__ == "__main__":
    main()
