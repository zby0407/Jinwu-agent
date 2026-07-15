from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .data import b3_root


EVIDENCE_MATRIX_PATH = b3_root() / "specs" / "hypothesis_evidence_matrix.json"


def _load_matrix() -> dict[str, Any]:
    return json.loads(EVIDENCE_MATRIX_PATH.read_text(encoding="utf-8"))


def evidence_catalog() -> dict[str, Any]:
    matrix = _load_matrix()
    return {
        "schema_version": matrix["schema_version"],
        "project": matrix["project"],
        "search_protocol": matrix["search_protocol"],
        "source_count": len(matrix.get("sources", [])),
        "hypothesis_count": len(matrix.get("hypothesis_links", [])),
        "sources": matrix.get("sources", []),
        "hypothesis_links": matrix.get("hypothesis_links", []),
    }


def evidence_for_hypothesis(hypothesis_id: str) -> dict[str, Any]:
    matrix = _load_matrix()
    source_by_id = {source["id"]: source for source in matrix.get("sources", [])}
    links = [
        link
        for link in matrix.get("hypothesis_links", [])
        if link.get("hypothesis_id") == hypothesis_id
    ]
    return {
        "hypothesis_id": hypothesis_id,
        "links": [
            {
                **link,
                "sources": [source_by_id[source_id] for source_id in link.get("source_ids", []) if source_id in source_by_id],
            }
            for link in links
        ],
    }


def _query_tokens(query: str) -> list[str]:
    """Tokenize English identifiers and Chinese research queries deterministically."""

    lowered = query.lower()
    tokens = {
        token
        for token in re.split(r"[^a-z0-9_.+-]+", lowered)
        if len(token) >= 2
    }
    for span in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(span) == 1:
            tokens.add(span)
            continue
        tokens.update(span[index : index + 2] for index in range(len(span) - 1))
        tokens.add(span)
    return sorted(tokens, key=lambda token: (-len(token), token))


def query_evidence(query: str, limit: int = 6) -> dict[str, Any]:
    matrix = _load_matrix()
    tokens = _query_tokens(query)
    rows: list[dict[str, Any]] = []
    for source in matrix.get("sources", []):
        haystack = " ".join(
            str(source.get(key, ""))
            for key in [
                "id",
                "title",
                "authors",
                "year",
                "source_type",
                "key_claim",
                "design_role",
                "limitation",
                "tags",
            ]
        ).lower()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            rows.append({"score": score, "source": source})
    rows.sort(key=lambda row: (row["score"], row["source"].get("evidence_quality", "")), reverse=True)
    return {
        "query": query,
        "tokens": tokens,
        "results": rows[: max(1, min(limit, 20))],
    }


def evidence_summary_for_run(hypothesis_ids: list[str]) -> dict[str, Any]:
    summaries = [evidence_for_hypothesis(hypothesis_id) for hypothesis_id in hypothesis_ids]
    unresolved = []
    for item in summaries:
        for link in item["links"]:
            unresolved.extend(link.get("unresolved_gaps", []))
    return {
        "matrix_file": "b3/specs/hypothesis_evidence_matrix.json",
        "hypotheses": summaries,
        "unresolved_gaps": sorted(set(unresolved)),
    }
