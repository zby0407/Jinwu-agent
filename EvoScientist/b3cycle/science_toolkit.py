"""Reviewed compact tool registry for the three solar-cycle science agents."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .analysis import run_registered_analysis
from .data import b3_root, code_root, raw_root, repo_root
from .evidence import evidence_catalog, query_evidence
from .science_agents import (
    REGISTERED_EXPERIMENTS,
    RunStore,
    ScienceAgentError,
    audit_feature_availability,
    canonical_json_sha256,
    order_balanced_tournament,
    preflight_registered_experiment,
    proximity_clusters,
    score_hypothesis_pair,
    validate_research_plan,
)


TOOLKIT_SCHEMA_VERSION = "b3-scientific-toolkit-v1"
TOOL_RESULT_SCHEMA_VERSION = "b3-tool-result-v3"
TOOL_VERIFICATION_SCHEMA_VERSION = "b3-tool-verification-v3"
TOOL_RECEIPT_SCHEMA_VERSION = "b3-tool-execution-receipt-v3-hmac-sha256"
TOOL_VERSION = "1.3.0"
MAX_INPUT_BYTES = 64 * 1024
MAX_DIFF_ROWS = 200
MAX_TRACE_FILES = 200
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ScientificToolkitError(ValueError):
    """Raised for bounded tool input or verification failures."""


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    category: str
    agents: tuple[str, ...]
    description: str
    input_schema: dict[str, Any]
    output_contract: str
    network_policy: str = "offline"


_ALL_AGENTS = (
    "b3-research-planner",
    "b3-experiment",
    "b3-hypothesis",
)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "research.get_context",
        "research",
        _ALL_AGENTS,
        "读取一次性研究背景、课题方向、主张边界和人工复核点。",
        {"type": "object", "additionalProperties": False},
        "research_context_v1",
    ),
    ToolSpec(
        "research.query_evidence",
        "research",
        _ALL_AGENTS,
        "在已核验的本地证据矩阵中检索支持、限制和反证；支持中英文查询。",
        {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        "verified_evidence_hits",
    ),
    ToolSpec(
        "research.search_literature",
        "research",
        ("b3-research-planner", "b3-hypothesis"),
        "通过固定 OpenAlex/Crossref 主机做有界文献发现；结果必须入账核验后才能支撑主张。",
        {
            "type": "object",
            "required": ["provider", "query"],
            "properties": {
                "provider": {"enum": ["openalex", "crossref"]},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
        "discovery_only_literature_hits",
        "fixed-host-https",
    ),
    ToolSpec(
        "planning.audit_data_vintage",
        "planning",
        ("b3-research-planner", "b3-experiment"),
        "核验本地数据快照的哈希、字节数、许可和因果可用时间。",
        {"type": "object", "additionalProperties": False},
        "data_vintage_audit",
    ),
    ToolSpec(
        "planning.audit_feature_availability",
        "planning",
        ("b3-research-planner", "b3-experiment"),
        "检查每个特征在给定 forecast origin 是否已因果可用。",
        {
            "type": "object",
            "required": ["rows", "forecast_origin"],
            "properties": {
                "rows": {"type": "array"},
                "forecast_origin": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "feature_availability_audit",
    ),
    ToolSpec(
        "planning.validate_plan_draft",
        "planning",
        ("b3-research-planner",),
        "在冻结前用同一 Python 合同校验 ResearchPlan 1.0 提交内容。",
        {
            "type": "object",
            "required": ["draft"],
            "properties": {"draft": {"type": "object"}},
            "additionalProperties": False,
        },
        "validated_plan_draft",
    ),
    ToolSpec(
        "planning.diff_plans",
        "planning",
        ("b3-research-planner",),
        "生成两版计划的有界 JSON Pointer 差异，保留预注册修订轨迹。",
        {
            "type": "object",
            "required": ["before", "after"],
            "properties": {"before": {"type": "object"}, "after": {"type": "object"}},
            "additionalProperties": False,
        },
        "plan_diff",
    ),
    ToolSpec(
        "experiment.list_registered",
        "experiment",
        ("b3-research-planner", "b3-experiment", "b3-hypothesis"),
        "列出 E0–E8 的目的、主输出、状态语义和主张角色。",
        {"type": "object", "additionalProperties": False},
        "registered_experiment_catalog",
    ),
    ToolSpec(
        "experiment.preflight",
        "experiment",
        ("b3-experiment",),
        "执行前检查冻结节点、seed、预算、DAG 依赖和不可变目标路径。",
        {
            "type": "object",
            "required": ["run_id", "experiment_id", "plan_node_id", "seed"],
            "properties": {
                "run_id": {"type": "string"},
                "experiment_id": {"type": "string"},
                "plan_node_id": {"type": "string"},
                "seed": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "experiment_preflight",
    ),
    ToolSpec(
        "experiment.design_multiseed_matrix",
        "experiment",
        ("b3-research-planner", "b3-experiment"),
        "为一个注册实验生成去重的多 seed 运行矩阵与停止规则，不直接执行。",
        {
            "type": "object",
            "required": ["experiment_id", "seeds"],
            "properties": {
                "experiment_id": {"type": "string"},
                "seeds": {"type": "array"},
                "stop_after_failures": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "multiseed_design",
    ),
    ToolSpec(
        "experiment.compare_results",
        "experiment",
        ("b3-experiment", "b3-hypothesis"),
        "比较两个不可变实验产物的共同数值字段、状态和哈希。",
        {
            "type": "object",
            "required": ["run_id", "left_path", "right_path"],
            "properties": {
                "run_id": {"type": "string"},
                "left_path": {"type": "string"},
                "right_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "experiment_comparison",
    ),
    ToolSpec(
        "experiment.diagnose_failure",
        "experiment",
        ("b3-experiment",),
        "从不可变 manifest 提取失败门、缺失输入和唯一安全下一步。",
        {
            "type": "object",
            "required": ["run_id", "manifest_path"],
            "properties": {
                "run_id": {"type": "string"},
                "manifest_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "failure_diagnosis",
    ),
    ToolSpec(
        "hypothesis.review_portfolio",
        "hypothesis",
        ("b3-hypothesis",),
        "对完整假设卡做近邻聚类与双顺序成对比较；排序只表示优先级。",
        {
            "type": "object",
            "required": ["cards"],
            "properties": {
                "cards": {"type": "array"},
                "proximity_threshold": {"type": "number"},
            },
            "additionalProperties": False,
        },
        "hypothesis_review",
    ),
    ToolSpec(
        "hypothesis.design_discriminating_test",
        "hypothesis",
        ("b3-hypothesis", "b3-research-planner"),
        "从竞争假设的预测和证伪条件中提出最小判别实验，不生成新证据。",
        {
            "type": "object",
            "required": ["hypotheses"],
            "properties": {"hypotheses": {"type": "array"}},
            "additionalProperties": False,
        },
        "discriminating_test_design",
    ),
    ToolSpec(
        "audit.verify_claim_links",
        "audit",
        _ALL_AGENTS,
        "按登记表精确核验 claim id、主张原文和来源集合；artifact 只能补充。",
        {
            "type": "object",
            "required": ["claims"],
            "properties": {
                "claims": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "required": ["claim_id", "claim_text", "source_ids"],
                        "properties": {
                            "claim_id": {"type": "string"},
                            "claim_text": {"type": "string"},
                            "source_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "artifact_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "run_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "claim_link_audit",
    ),
    ToolSpec(
        "audit.source_license",
        "audit",
        _ALL_AGENTS,
        "汇总数据与方法来源的许可、复用限制和缺口。",
        {
            "type": "object",
            "properties": {"source_ids": {"type": "array"}},
            "additionalProperties": False,
        },
        "source_license_audit",
    ),
    ToolSpec(
        "audit.trace_artifact",
        "audit",
        _ALL_AGENTS,
        "验证一个运行产物的哈希并回溯其父节点、数据源和代码来源。",
        {
            "type": "object",
            "required": ["run_id", "artifact_path"],
            "properties": {
                "run_id": {"type": "string"},
                "artifact_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "artifact_lineage",
    ),
)


SPEC_BY_ID = {spec.tool_id: spec for spec in TOOL_SPECS}


def _effective_agent(
    agent: str | None, *, human_offline: bool = False
) -> str:
    if agent not in _ALL_AGENTS:
        raise ScientificToolkitError("agent is not registered")
    bound_agent = os.getenv("B3_ACTIVE_AGENT")
    if human_offline:
        if bound_agent is not None and bound_agent.strip():
            raise ScientificToolkitError(
                "human-offline mode cannot run inside a parent-bound Agent process"
            )
        return agent
    if bound_agent is None or not bound_agent.strip():
        raise ScientificToolkitError(
            "role-scoped scientific tools require trusted B3_ACTIVE_AGENT binding"
        )
    bound_agent = bound_agent.strip()
    if bound_agent not in _ALL_AGENTS:
        raise ScientificToolkitError("B3_ACTIVE_AGENT is not registered")
    if agent != bound_agent:
        raise ScientificToolkitError(
            "requested agent does not match trusted B3_ACTIVE_AGENT binding"
        )
    return bound_agent


def _authorized_spec(
    tool_id: str, agent: str | None, *, human_offline: bool = False
) -> ToolSpec:
    agent = _effective_agent(agent, human_offline=human_offline)
    spec = SPEC_BY_ID.get(tool_id)
    if spec is None:
        raise ScientificToolkitError("scientific tool is not registered")
    if agent not in spec.agents:
        raise ScientificToolkitError(
            f"agent {agent} is not authorized for scientific tool {tool_id}"
        )
    return spec


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScientificToolkitError(f"reviewed JSON resource unavailable: {path.name}") from exc


def _only(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    extras = sorted(set(payload) - set(allowed))
    if extras:
        raise ScientificToolkitError(f"unexpected input fields: {', '.join(extras)}")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScientificToolkitError(f"{label} must be a JSON object")
    return value


def _string(value: object, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScientificToolkitError(f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise ScientificToolkitError(f"{label} exceeds {maximum} characters")
    return text


def _safe_id(value: object, label: str) -> str:
    text = _string(value, label, 160)
    if _SAFE_ID.fullmatch(text) is None:
        raise ScientificToolkitError(f"{label} is not a safe identifier")
    return text


def _bounded_json(payload: object) -> None:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScientificToolkitError("tool input must be finite JSON") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise ScientificToolkitError("tool input exceeds 64 KiB")


def discover_tools(
    query: str = "",
    agent: str | None = None,
    limit: int = 20,
    *,
    human_offline: bool = False,
) -> dict[str, Any]:
    agent = _effective_agent(agent, human_offline=human_offline)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ScientificToolkitError("limit must be an integer from 1 to 50")
    tokens = [token for token in re.split(r"\s+", query.lower().strip()) if token]
    matches: list[tuple[int, ToolSpec]] = []
    for spec in TOOL_SPECS:
        if agent not in spec.agents:
            continue
        haystack = f"{spec.tool_id} {spec.category} {spec.description}".lower()
        score = sum(1 for token in tokens if token in haystack) if tokens else 1
        if score:
            matches.append((score, spec))
    matches.sort(key=lambda row: (-row[0], row[1].category, row[1].tool_id))
    return {
        "schema_version": TOOLKIT_SCHEMA_VERSION,
        "query": query,
        "agent": agent,
        "execution_trust": (
            "human_offline_unverified" if human_offline else "parent_bound_agent"
        ),
        "total_registry_size": len(TOOL_SPECS),
        "tools": [
            {
                "tool_id": spec.tool_id,
                "category": spec.category,
                "description": spec.description,
                "agents": list(spec.agents),
                "network_policy": spec.network_policy,
            }
            for _, spec in matches[:limit]
        ],
    }


def inspect_tool(
    tool_id: str, agent: str, *, human_offline: bool = False
) -> dict[str, Any]:
    agent = _effective_agent(agent, human_offline=human_offline)
    spec = _authorized_spec(tool_id, agent, human_offline=human_offline)
    return {
        "schema_version": TOOLKIT_SCHEMA_VERSION,
        "tool_id": spec.tool_id,
        "tool_version": TOOL_VERSION,
        "authorized_agent": agent,
        "execution_trust": (
            "human_offline_unverified" if human_offline else "parent_bound_agent"
        ),
        "category": spec.category,
        "agents": list(spec.agents),
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_contract": spec.output_contract,
        "network_policy": spec.network_policy,
        "claim_policy": "Fail closed: only an explicit verified-source or immutable-artifact output contract can be claimable.",
    }


def _search_literature(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"provider", "query", "limit"})
    provider = _string(payload.get("provider"), "provider", 20)
    if provider not in {"openalex", "crossref"}:
        raise ScientificToolkitError("provider must be openalex or crossref")
    query = _string(payload.get("query"), "query", 300)
    limit = payload.get("limit", 5)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ScientificToolkitError("limit must be an integer from 1 to 10")
    if provider == "openalex":
        base = "https://api.openalex.org/works"
        params = urllib.parse.urlencode({"search": query, "per-page": limit})
    else:
        base = "https://api.crossref.org/works"
        params = urllib.parse.urlencode({"query": query, "rows": limit})
    request = urllib.request.Request(
        f"{base}?{params}",
        headers={"User-Agent": "solar-cycle-science-toolkit/1.0 (metadata discovery)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
    except (OSError, urllib.error.URLError) as exc:
        return {
            "status": "inconclusive",
            "provider": provider,
            "query": query,
            "claimable": False,
            "results": [],
            "errors": [type(exc).__name__],
        }
    if len(raw) > 1024 * 1024:
        raise ScientificToolkitError("literature metadata response exceeds 1 MiB")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ScientificToolkitError("literature provider returned invalid JSON") from exc
    items = (
        decoded.get("results", [])
        if provider == "openalex"
        else decoded.get("message", {}).get("items", [])
    )
    results: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        if provider == "openalex":
            authors = [
                row.get("author", {}).get("display_name")
                for row in item.get("authorships", [])[:10]
                if isinstance(row, dict)
            ]
            title = item.get("title")
            year = item.get("publication_year")
            doi = item.get("doi")
            url = item.get("id")
        else:
            authors = [
                " ".join(filter(None, (row.get("given"), row.get("family"))))
                for row in item.get("author", [])[:10]
                if isinstance(row, dict)
            ]
            title_value = item.get("title", [])
            title = title_value[0] if isinstance(title_value, list) and title_value else None
            dates = item.get("published-print") or item.get("published-online") or {}
            date_parts = dates.get("date-parts", [[]]) if isinstance(dates, dict) else [[]]
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            doi = item.get("DOI")
            url = item.get("URL")
        results.append(
            {
                "title": title,
                "authors": [author for author in authors if author],
                "year": year,
                "doi": doi,
                "url": url,
                "claimable": False,
                "verification_state": "discovery_only",
            }
        )
    return {
        "status": "success" if results else "inconclusive",
        "provider": provider,
        "query": query,
        "claimable": False,
        "results": results,
        "next_step": "Verify metadata and evidence passages, then add accepted sources to the local evidence ledger.",
    }


def _validate_plan_draft(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"draft"})
    draft = _mapping(payload.get("draft"), "draft")
    owned = {"run_id", "created_at", "status", "frozen_hash", "artifact_sha256"}
    if owned & set(draft):
        raise ScientificToolkitError(
            "plan draft contains deterministic envelope fields: "
            + ", ".join(sorted(owned & set(draft)))
        )
    plan = {
        **json.loads(json.dumps(draft, ensure_ascii=False, allow_nan=False)),
        "run_id": "preflight",
        "created_at": "2000-01-01T00:00:00+00:00",
        "status": "frozen",
    }
    plan["frozen_hash"] = canonical_json_sha256(plan)
    plan["artifact_sha256"] = canonical_json_sha256(plan)
    validate_research_plan(plan)
    return {
        "status": "valid",
        "schema_version": plan["schema_version"],
        "frozen_preview_sha256": plan["artifact_sha256"],
        "node_count": len(plan["task_graph"]),
        "data_contract_count": len(plan["data_contracts"]),
    }


def _diff_values(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if len(path) > 1000:
        raise ScientificToolkitError("plan diff nesting is too deep")
    if isinstance(before, dict) and isinstance(after, dict):
        rows: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            pointer = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key not in before:
                rows.append({"op": "add", "path": pointer, "after": after[key]})
            elif key not in after:
                rows.append({"op": "remove", "path": pointer, "before": before[key]})
            else:
                rows.extend(_diff_values(before[key], after[key], pointer))
            if len(rows) > MAX_DIFF_ROWS:
                raise ScientificToolkitError("plan diff exceeds 200 changes")
        return rows
    if isinstance(before, list) and isinstance(after, list):
        rows: list[dict[str, Any]] = []
        for index in range(max(len(before), len(after))):
            pointer = f"{path}/{index}"
            if index >= len(before):
                rows.append({"op": "add", "path": pointer, "after": after[index]})
            elif index >= len(after):
                rows.append({"op": "remove", "path": pointer, "before": before[index]})
            else:
                rows.extend(_diff_values(before[index], after[index], pointer))
            if len(rows) > MAX_DIFF_ROWS:
                raise ScientificToolkitError("plan diff exceeds 200 changes")
        return rows
    return [] if before == after else [{"op": "replace", "path": path or "/", "before": before, "after": after}]


def _run_store() -> RunStore:
    return RunStore(_agent_runs_root())


def _agent_runs_root() -> Path:
    value = os.getenv("B3_RUNTIME_ROOT")
    if not value:
        return b3_root() / "agent_runs"
    runtime = Path(value).expanduser().resolve()
    project = repo_root().resolve()
    if runtime != project / "runtime":
        raise ScientificToolkitError(
            "B3_RUNTIME_ROOT must equal the project runtime directory"
        )
    return runtime / "agent_runs"


def _numeric_leaves(value: Any, path: str = "") -> dict[str, float]:
    rows: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"seed", "bytes", "tokens"}:
                continue
            rows.update(_numeric_leaves(child, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value[:100]):
            rows.update(_numeric_leaves(child, f"{path}/{index}"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        rows[path or "/"] = float(value)
    return rows


def _compare_results(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"run_id", "left_path", "right_path"})
    run_id = _safe_id(payload.get("run_id"), "run_id")
    left_path = _string(payload.get("left_path"), "left_path", 500)
    right_path = _string(payload.get("right_path"), "right_path", 500)
    store = _run_store()
    left = store.read_artifact(run_id, left_path)
    right = store.read_artifact(run_id, right_path)
    left_numbers = _numeric_leaves(left.get("result", left))
    right_numbers = _numeric_leaves(right.get("result", right))
    common = sorted(set(left_numbers) & set(right_numbers))[:100]
    return {
        "status": "success" if common else "inconclusive",
        "left": {"path": left_path, "sha256": left.get("artifact_sha256"), "status": left.get("status")},
        "right": {"path": right_path, "sha256": right.get("artifact_sha256"), "status": right.get("status")},
        "numeric_differences": [
            {
                "path": path,
                "left": left_numbers[path],
                "right": right_numbers[path],
                "delta": right_numbers[path] - left_numbers[path],
            }
            for path in common
        ],
        "comparison_count": len(common),
    }


def _diagnose_failure(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"run_id", "manifest_path"})
    run_id = _safe_id(payload.get("run_id"), "run_id")
    path = _string(payload.get("manifest_path"), "manifest_path", 500)
    manifest = _run_store().read_artifact(run_id, path)
    result = manifest.get("result", {}) if isinstance(manifest.get("result"), dict) else {}
    gates = manifest.get("gates", {}) if isinstance(manifest.get("gates"), dict) else {}
    failed_gates = [
        name
        for name, gate in gates.items()
        if isinstance(gate, dict) and gate.get("status") == "failed"
    ]
    missing = list(result.get("missing_required_inputs", []))
    hard = list(result.get("hard_failures", []))
    if manifest.get("status") in {"passed", "warning"}:
        next_action = "No failure recovery is required; preserve the immutable result."
    elif missing:
        next_action = "Create a new plan revision that supplies the missing prerequisite; do not modify this manifest."
    elif hard:
        next_action = "Quarantine the claim branch and inspect the recorded gate before a new run."
    else:
        next_action = "Inspect the immutable error type and create a new run; never overwrite this artifact."
    return {
        "status": manifest.get("status"),
        "manifest_path": path,
        "manifest_sha256": manifest.get("artifact_sha256"),
        "failed_gates": failed_gates,
        "missing_required_inputs": missing,
        "hard_failures": hard,
        "error_type": manifest.get("error", {}).get("type") if isinstance(manifest.get("error"), dict) else None,
        "safe_next_action": next_action,
    }


def _review_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"cards", "proximity_threshold"})
    cards = payload.get("cards")
    if not isinstance(cards, list) or not 2 <= len(cards) <= 20 or not all(isinstance(card, dict) for card in cards):
        raise ScientificToolkitError("cards must contain 2 to 20 hypothesis objects")
    threshold = payload.get("proximity_threshold", 0.72)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.5 <= float(threshold) <= 0.95:
        raise ScientificToolkitError("proximity_threshold must be between 0.5 and 0.95")
    clusters = proximity_clusters(cards, threshold=float(threshold))
    tournament = order_balanced_tournament(cards, score_hypothesis_pair)
    return {
        "status": "success",
        "card_count": len(cards),
        "proximity": clusters,
        "order_balanced_tournament": tournament,
        "ranking_claim_boundary": "Pairwise ranking prioritizes review; it is not scientific truth.",
    }


def _discriminating_test(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"hypotheses"})
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list) or not 2 <= len(hypotheses) <= 8:
        raise ScientificToolkitError("hypotheses must contain 2 to 8 cards")
    rows: list[dict[str, Any]] = []
    candidate_experiments: set[str] = set()
    for card in hypotheses:
        item = _mapping(card, "hypothesis")
        hypothesis_id = _safe_id(item.get("id"), "hypothesis.id")
        predictions = item.get("measurable_predictions", [])
        falsifiers = item.get("falsifiers", [])
        if not isinstance(predictions, list) or not isinstance(falsifiers, list):
            raise ScientificToolkitError("hypothesis predictions and falsifiers must be arrays")
        experiment_ids = sorted(
            {
                str(
                    prediction.get(
                        "experiment_id", prediction.get("target_experiment")
                    )
                )
                for prediction in predictions
                if isinstance(prediction, dict)
                and prediction.get(
                    "experiment_id", prediction.get("target_experiment")
                )
                in REGISTERED_EXPERIMENTS
            }
        )
        candidate_experiments.update(experiment_ids)
        rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "prediction_count": len(predictions),
                "falsifier_count": len(falsifiers),
                "registered_experiments": experiment_ids,
            }
        )
    shared = [
        experiment_id
        for experiment_id in sorted(candidate_experiments)
        if sum(experiment_id in row["registered_experiments"] for row in rows) >= 2
    ]
    recommended = shared or sorted(candidate_experiments)
    return {
        "status": "success" if recommended else "inconclusive",
        "hypotheses": rows,
        "recommended_registered_experiments": recommended[:3],
        "selection_rule": "Prefer one preregistered experiment whose directional outcome differs across at least two hypotheses; otherwise request missing quantitative predictions.",
        "must_include": ["baseline", "negative_control", "uncertainty", "explicit_falsifier", "human_review"],
    }


def _verified_source_ids() -> set[str]:
    _registry, exclusion_reasons, registered_source_ids = (
        _claim_support_contract()
    )
    return registered_source_ids - set(exclusion_reasons)


_CLAIM_LINK_POLICY = {
    "claim_registry": "b3/specs/hypothesis_evidence_matrix.json#hypothesis_links",
    "claim_text_match": "exact_after_outer_whitespace_trim",
    "source_link_match": "exact_set",
    "artifacts_are_supplemental_only": True,
    "excluded_support_classes": [
        "architecture_only",
        "discovery_only",
        "restricted_software",
    ],
}


def _claim_support_contract() -> tuple[
    dict[str, dict[str, Any]], dict[str, list[str]], set[str]
]:
    """Load the reviewed claim registry and fail closed on support-only sources."""

    matrix = evidence_catalog()
    ledger = _load_json(b3_root() / "specs" / "evidence_ledger.json")
    ledger_policy = ledger.get("claim_policy")
    if not isinstance(ledger_policy, dict) or any(
        ledger_policy.get(key) != value for key, value in _CLAIM_LINK_POLICY.items()
    ):
        raise ScientificToolkitError("reviewed claim-link policy is missing or changed")

    source_records: dict[str, list[dict[str, Any]]] = {}
    for source in [*matrix.get("sources", []), *ledger.get("entries", [])]:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            continue
        source_records.setdefault(str(source["id"]), []).append(source)

    exclusion_reasons: dict[str, list[str]] = {}
    for source_id, records in source_records.items():
        source_types = " ".join(
            str(record.get("source_type", "")).lower() for record in records
        )
        licenses = " ".join(
            str(record.get("license_or_reuse", "")).lower() for record in records
        )
        limitations = " ".join(
            str(record.get("limitation", "")).lower() for record in records
        )
        decisions = {
            str(record.get("inclusion_decision", "")).lower()
            for record in records
        }
        reasons: list[str] = []
        if "architecture-only" in licenses:
            reasons.append("architecture_only")
        if (
            "discovery_only" in source_types
            or "discovery results remain non-claimable" in limitations
        ):
            reasons.append("discovery_only")
        if "restricted_software" in source_types or (
            "include_with_restriction" in decisions and "software" in source_types
        ):
            reasons.append("restricted_software")
        if reasons:
            exclusion_reasons[source_id] = sorted(set(reasons))

    registry: dict[str, dict[str, Any]] = {}
    for raw_link in matrix.get("hypothesis_links", []):
        if not isinstance(raw_link, dict):
            raise ScientificToolkitError("reviewed claim registry contains a non-object")
        claim_id = raw_link.get("hypothesis_id")
        claim_text = raw_link.get("claim")
        source_ids = raw_link.get("source_ids")
        if (
            not isinstance(claim_id, str)
            or not isinstance(claim_text, str)
            or not claim_text.strip()
            or not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(value, str) for value in source_ids)
            or len(source_ids) != len(set(source_ids))
            or claim_id in registry
        ):
            raise ScientificToolkitError("reviewed claim registry is invalid")
        missing = sorted(set(source_ids) - set(source_records))
        excluded = sorted(set(source_ids) & set(exclusion_reasons))
        if missing or excluded:
            raise ScientificToolkitError(
                "reviewed claim registry links missing or ineligible support sources"
            )
        registry[claim_id] = {
            "claim_text": claim_text.strip(),
            "source_ids": sorted(source_ids),
        }
    if not registry:
        raise ScientificToolkitError("reviewed claim registry is empty")
    return registry, exclusion_reasons, set(source_records)


def _verify_claim_links(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"claims", "run_id"})
    claims = payload.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 50:
        raise ScientificToolkitError("claims must contain 1 to 50 objects")
    run_id = payload.get("run_id")
    store = _run_store() if run_id is not None else None
    if run_id is not None:
        run_id = _safe_id(run_id, "run_id")
    registry, exclusion_reasons, registered_source_ids = _claim_support_contract()
    rows: list[dict[str, Any]] = []
    for claim in claims:
        item = _mapping(claim, "claim")
        _only(item, {"claim_id", "claim_text", "source_ids", "artifact_paths"})
        claim_id = _safe_id(item.get("claim_id"), "claim_id")
        claim_text = _string(item.get("claim_text"), "claim_text", 2000)
        claimed_sources = item.get("source_ids")
        artifact_paths = item.get("artifact_paths", [])
        if (
            not isinstance(claimed_sources, list)
            or not claimed_sources
            or not all(isinstance(value, str) for value in claimed_sources)
        ):
            raise ScientificToolkitError(
                "claim source_ids must be a non-empty array of strings"
            )
        if not isinstance(artifact_paths, list) or not all(isinstance(value, str) for value in artifact_paths):
            raise ScientificToolkitError("claim artifact_paths must be strings")
        contract = registry.get(claim_id)
        expected_sources = contract["source_ids"] if contract is not None else []
        claimed_source_set = set(claimed_sources)
        duplicate_sources = sorted(
            source_id for source_id in claimed_source_set if claimed_sources.count(source_id) > 1
        )
        missing_sources = sorted(claimed_source_set - registered_source_ids)
        unapproved_sources = sorted(claimed_source_set - set(expected_sources))
        missing_expected_sources = sorted(set(expected_sources) - claimed_source_set)
        ineligible_sources = [
            {
                "source_id": source_id,
                "reasons": exclusion_reasons[source_id],
            }
            for source_id in sorted(claimed_source_set & set(exclusion_reasons))
        ]
        claim_text_matches = bool(
            contract is not None and claim_text == contract["claim_text"]
        )
        source_link_matches = bool(
            contract is not None
            and not duplicate_sources
            and claimed_source_set == set(expected_sources)
        )
        verified_artifacts: list[str] = []
        missing_artifacts: list[str] = []
        for path in artifact_paths:
            if store is None or run_id is None:
                missing_artifacts.append(path)
                continue
            try:
                store.read_artifact(run_id, path)
            except ScienceAgentError:
                missing_artifacts.append(path)
            else:
                verified_artifacts.append(path)
        violations: list[str] = []
        if contract is None:
            violations.append("unregistered_claim")
        if not claim_text_matches:
            violations.append("claim_text_mismatch")
        if duplicate_sources:
            violations.append("duplicate_source_ids")
        if missing_sources:
            violations.append("unregistered_source_ids")
        if unapproved_sources or missing_expected_sources:
            violations.append("source_link_contract_mismatch")
        if ineligible_sources:
            violations.append("ineligible_support_source")
        if missing_artifacts:
            violations.append("missing_artifact_paths")
        claimable = not violations and source_link_matches
        rows.append(
            {
                "claim_id": claim_id,
                "registered_claim": contract is not None,
                "claim_text_matches": claim_text_matches,
                "source_link_matches": source_link_matches,
                "claimable": claimable,
                "expected_source_ids": expected_sources,
                "verified_source_ids": sorted(
                    claimed_source_set
                    & set(expected_sources)
                    & registered_source_ids
                    - set(exclusion_reasons)
                ),
                "missing_source_ids": missing_sources,
                "unapproved_source_ids": unapproved_sources,
                "missing_expected_source_ids": missing_expected_sources,
                "duplicate_source_ids": duplicate_sources,
                "ineligible_source_ids": ineligible_sources,
                "verified_artifact_paths": verified_artifacts,
                "missing_artifact_paths": missing_artifacts,
                "violations": violations,
            }
        )
    return {
        "status": "success" if all(row["claimable"] for row in rows) else "inconclusive",
        "claim_link_policy": dict(_CLAIM_LINK_POLICY),
        "claims": rows,
        "claimable_count": sum(row["claimable"] for row in rows),
        "total": len(rows),
    }


def _source_license(payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"source_ids"})
    requested = payload.get("source_ids")
    if requested is not None and (
        not isinstance(requested, list) or not all(isinstance(value, str) for value in requested)
    ):
        raise ScientificToolkitError("source_ids must be an array of strings")
    manifest = _load_json(raw_root() / "source_manifest.json")
    ledger = _load_json(b3_root() / "specs" / "evidence_ledger.json")
    rows: dict[str, dict[str, Any]] = {}
    for source in manifest:
        rows[str(source["id"])] = {
            "source_id": source["id"],
            "source_type": "data",
            "license_or_reuse": source.get("license"),
            "url": source.get("url"),
        }
    for source in ledger.get("entries", []):
        if isinstance(source, dict) and source.get("id"):
            rows[str(source["id"])] = {
                "source_id": source["id"],
                "source_type": source.get("source_type"),
                "license_or_reuse": source.get("license_or_reuse"),
                "url": source.get("url") or source.get("repository") or source.get("path"),
            }
    selected_ids = sorted(set(requested or rows))
    selected = [rows[source_id] for source_id in selected_ids if source_id in rows]
    missing = [source_id for source_id in selected_ids if source_id not in rows]
    restrictions = [
        row
        for row in selected
        if not row.get("license_or_reuse")
        or any(token in str(row.get("license_or_reuse", "")).lower() for token in ("restriction", "not-explicit", "architecture-only", "reference-only"))
    ]
    return {
        "status": "success" if not missing else "inconclusive",
        "sources": selected,
        "missing_source_ids": missing,
        "restricted_or_reference_only": restrictions,
    }


def trace_artifact_lineage(
    run_id: str,
    artifact_path: str,
    agent: str,
    *,
    human_offline: bool = False,
) -> dict[str, Any]:
    agent = _effective_agent(agent, human_offline=human_offline)
    _authorized_spec(
        "audit.trace_artifact", agent, human_offline=human_offline
    )
    run_id = _safe_id(run_id, "run_id")
    artifact_path = _string(artifact_path, "artifact_path", 500)
    store = _run_store()
    artifact = store.read_artifact(run_id, artifact_path)
    related: list[dict[str, Any]] = []
    for reference in artifact.get("artifacts", []) if isinstance(artifact.get("artifacts"), list) else []:
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            continue
        try:
            linked = store.read_artifact(run_id, reference["path"])
        except ScienceAgentError:
            related.append({"path": reference["path"], "verified": False})
        else:
            related.append(
                {
                    "path": reference["path"],
                    "verified": linked.get("artifact_sha256") == reference.get("sha256"),
                    "sha256": linked.get("artifact_sha256"),
                }
            )
        if len(related) >= MAX_TRACE_FILES:
            break
    provenance = artifact.get("provenance", {}) if isinstance(artifact.get("provenance"), dict) else {}
    result = {
        "status": "success",
        "run_id": run_id,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact.get("artifact_sha256"),
        "schema_version": artifact.get("schema_version"),
        "parent_id": artifact.get("parent_id"),
        "experiment_id": artifact.get("experiment_id"),
        "seed": artifact.get("seed"),
        "plan_artifact_sha256": provenance.get("plan_artifact_sha256"),
        "data_sources": artifact.get("data_sources", []),
        "code_files_sha256": provenance.get("code_files_sha256", {}),
        "related_artifacts": related,
    }
    if human_offline:
        result.update(
            {
                "claimable": False,
                "verification_state": "human_offline_unverified",
            }
        )
    return result


def _dispatch(
    tool_id: str,
    payload: dict[str, Any],
    agent: str,
    *,
    human_offline: bool = False,
) -> dict[str, Any]:
    if tool_id == "research.get_context":
        _only(payload, set())
        return _load_json(b3_root() / "specs" / "research_context.json")
    if tool_id == "research.query_evidence":
        _only(payload, {"query", "limit"})
        query = _string(payload.get("query"), "query", 300)
        limit = payload.get("limit", 6)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ScientificToolkitError("limit must be an integer from 1 to 20")
        return {
            **query_evidence(query, limit),
            "verification_state": "verified_local_matrix",
            "source": "verified_local_matrix",
        }
    if tool_id == "research.search_literature":
        return _search_literature(payload)
    if tool_id == "planning.audit_data_vintage":
        _only(payload, set())
        return run_registered_analysis("E0_data_vintage_audit", 0)["data_vintage_audit"]
    if tool_id == "planning.audit_feature_availability":
        _only(payload, {"rows", "forecast_origin"})
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ScientificToolkitError("rows must be an array")
        origin = _string(payload.get("forecast_origin"), "forecast_origin", 80)
        violations = audit_feature_availability(rows, origin)
        return {"status": "passed" if not violations else "quarantined", "forecast_origin": origin, "violations": violations}
    if tool_id == "planning.validate_plan_draft":
        return _validate_plan_draft(payload)
    if tool_id == "planning.diff_plans":
        _only(payload, {"before", "after"})
        before = _mapping(payload.get("before"), "before")
        after = _mapping(payload.get("after"), "after")
        changes = _diff_values(before, after)
        return {"status": "success", "change_count": len(changes), "changes": changes}
    if tool_id == "experiment.list_registered":
        _only(payload, set())
        return _load_json(b3_root() / "specs" / "experiment_catalog.json")
    if tool_id == "experiment.preflight":
        _only(payload, {"run_id", "experiment_id", "plan_node_id", "seed"})
        run_id = _safe_id(payload.get("run_id"), "run_id")
        experiment_id = _string(payload.get("experiment_id"), "experiment_id", 100)
        node_id = _safe_id(payload.get("plan_node_id"), "plan_node_id")
        seed = payload.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ScientificToolkitError("seed must be a non-negative integer")
        return preflight_registered_experiment(_run_store(), run_id, experiment_id, node_id, seed)
    if tool_id == "experiment.design_multiseed_matrix":
        _only(payload, {"experiment_id", "seeds", "stop_after_failures"})
        experiment_id = _string(payload.get("experiment_id"), "experiment_id", 100)
        if experiment_id not in REGISTERED_EXPERIMENTS:
            raise ScientificToolkitError("experiment_id is not registered")
        seeds = payload.get("seeds")
        if not isinstance(seeds, list) or not 2 <= len(seeds) <= 20 or not all(isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= 2_147_483_647 for seed in seeds):
            raise ScientificToolkitError("seeds must contain 2 to 20 non-negative integers")
        if len(set(seeds)) != len(seeds):
            raise ScientificToolkitError("seeds must be unique")
        stop = payload.get("stop_after_failures", len(seeds))
        if not isinstance(stop, int) or isinstance(stop, bool) or not 1 <= stop <= len(seeds):
            raise ScientificToolkitError("stop_after_failures is out of range")
        return {"status": "success", "experiment_id": experiment_id, "runs": [{"seed": seed, "target": f"experiments/{experiment_id}_seed{seed}"} for seed in seeds], "stop_after_failures": stop, "aggregation": ["median", "interquartile_range", "failure_rate"], "execution": "not_started"}
    if tool_id == "experiment.compare_results":
        return _compare_results(payload)
    if tool_id == "experiment.diagnose_failure":
        return _diagnose_failure(payload)
    if tool_id == "hypothesis.review_portfolio":
        return _review_portfolio(payload)
    if tool_id == "hypothesis.design_discriminating_test":
        return _discriminating_test(payload)
    if tool_id == "audit.verify_claim_links":
        return _verify_claim_links(payload)
    if tool_id == "audit.source_license":
        return _source_license(payload)
    if tool_id == "audit.trace_artifact":
        _only(payload, {"run_id", "artifact_path"})
        return trace_artifact_lineage(
            payload.get("run_id"),
            payload.get("artifact_path"),
            agent,
            human_offline=human_offline,
        )
    raise ScientificToolkitError("scientific tool is not registered")


def _package_lock_hash() -> str | None:
    path = code_root() / "requirements-analysis.lock"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _sha256_text(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _receipt_authentication_key() -> bytes:
    """Return the ephemeral key supplied only by the trusted Pi parent."""

    raw = os.getenv("B3_TOOL_RECEIPT_HMAC_KEY", "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", raw) is None:
        raise ScientificToolkitError(
            "authenticated receipt operations require a parent-held HMAC key"
        )
    return bytes.fromhex(raw)


def _receipt_hmac(receipt_without_hmac: dict[str, Any]) -> str:
    encoded = json.dumps(
        receipt_without_hmac,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hmac.new(
        _receipt_authentication_key(), encoded, hashlib.sha256
    ).hexdigest()


def _claimability(
    spec: ToolSpec, result: dict[str, Any], status: str
) -> dict[str, Any]:
    """Evaluate the small allowlist of evidence-bearing output contracts."""

    conditions: list[dict[str, Any]]
    if spec.tool_id == "research.query_evidence":
        rows = result.get("results")
        registered: set[str] = set()
        try:
            registered = _verified_source_ids()
        except (ScientificToolkitError, ScienceAgentError, OSError, ValueError):
            pass
        source_ids = [
            row.get("source", {}).get("id")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("source"), dict)
        ] if isinstance(rows, list) else []
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {
                "condition": "verified_local_matrix",
                "satisfied": result.get("verification_state") == "verified_local_matrix",
            },
            {
                "condition": "nonempty_evidence_hits",
                "satisfied": isinstance(rows, list) and bool(rows),
            },
            {
                "condition": "all_source_ids_registered",
                "satisfied": bool(source_ids)
                and len(source_ids) == len(rows or [])
                and all(source_id in registered for source_id in source_ids),
            },
        ]
    elif spec.tool_id == "planning.audit_data_vintage":
        rows = result.get("sources")
        required_flags = (
            "file_present",
            "hash_matches",
            "bytes_match",
            "license_recorded",
            "causal_timestamp_recorded",
        )
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {"condition": "audit_passed", "satisfied": result.get("status") == "passed"},
            {
                "condition": "nonempty_source_audit",
                "satisfied": isinstance(rows, list) and bool(rows),
            },
            {
                "condition": "all_snapshot_checks_passed",
                "satisfied": isinstance(rows, list)
                and bool(rows)
                and all(
                    isinstance(row, dict)
                    and all(row.get(flag) is True for flag in required_flags)
                    for row in rows
                ),
            },
        ]
    elif spec.tool_id == "experiment.compare_results":
        left = result.get("left")
        right = result.get("right")
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {
                "condition": "immutable_artifact_hashes",
                "satisfied": isinstance(left, dict)
                and isinstance(right, dict)
                and _sha256_text(left.get("sha256"))
                and _sha256_text(right.get("sha256")),
            },
            {
                "condition": "nonempty_comparison",
                "satisfied": isinstance(result.get("comparison_count"), int)
                and result["comparison_count"] > 0,
            },
        ]
    elif spec.tool_id == "audit.verify_claim_links":
        rows = result.get("claims")
        total = result.get("total")
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {
                "condition": "nonempty_claim_audit",
                "satisfied": isinstance(rows, list)
                and bool(rows)
                and isinstance(total, int)
                and total == len(rows),
            },
            {
                "condition": "every_claim_has_verified_links",
                "satisfied": isinstance(rows, list)
                and bool(rows)
                and all(isinstance(row, dict) and row.get("claimable") is True for row in rows),
            },
        ]
    elif spec.tool_id == "audit.source_license":
        rows = result.get("sources")
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {
                "condition": "nonempty_registered_sources",
                "satisfied": isinstance(rows, list) and bool(rows),
            },
            {
                "condition": "no_missing_source_ids",
                "satisfied": result.get("missing_source_ids") == [],
            },
            {
                "condition": "license_or_reuse_recorded",
                "satisfied": isinstance(rows, list)
                and bool(rows)
                and all(
                    isinstance(row, dict) and bool(row.get("license_or_reuse"))
                    for row in rows
                ),
            },
        ]
    elif spec.tool_id == "audit.trace_artifact":
        related = result.get("related_artifacts")
        conditions = [
            {"condition": "execution_success", "satisfied": status == "success"},
            {
                "condition": "immutable_artifact_hash",
                "satisfied": _sha256_text(result.get("artifact_sha256")),
            },
            {
                "condition": "all_linked_artifacts_verified",
                "satisfied": isinstance(related, list)
                and all(
                    isinstance(row, dict) and row.get("verified") is True
                    for row in related
                ),
            },
        ]
    else:
        conditions = [
            {
                "condition": "output_contract_is_evidence_bearing",
                "satisfied": False,
            }
        ]
    satisfied = bool(conditions) and all(row["satisfied"] is True for row in conditions)
    return {
        "policy": "explicit_evidence_contract_v1",
        "output_contract": spec.output_contract,
        "conditions": conditions,
        "satisfied": satisfied,
    }


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError as exc:
        raise ScientificToolkitError("tool receipt path cannot be inspected") from exc


def _receipt_directory() -> Path:
    trusted_root = repo_root().resolve(strict=True)
    agent_runs = _agent_runs_root()
    directory = agent_runs / "_tool_receipts"
    for path in (agent_runs, directory):
        if path.exists() and _is_link_or_junction(path):
            raise ScientificToolkitError("tool receipt directory crosses a link or junction")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ScientificToolkitError("tool receipt directory cannot be created") from exc
        if _is_link_or_junction(path) or not path.is_dir():
            raise ScientificToolkitError("tool receipt directory is not a trusted directory")
    try:
        directory.resolve(strict=True).relative_to(trusted_root)
    except (OSError, ValueError) as exc:
        raise ScientificToolkitError("tool receipt directory escaped the B3 root") from exc
    return directory


def _receipt_path(call_id: object) -> Path:
    if not isinstance(call_id, str) or re.fullmatch(r"b3call-[0-9a-f]{32}", call_id) is None:
        raise ScientificToolkitError("tool call_id is invalid")
    return _receipt_directory() / f"{call_id}.json"


def _persist_execution_receipt(envelope: dict[str, Any]) -> None:
    call_id = envelope["call_id"]
    receipt_core = {
        "schema_version": TOOL_RECEIPT_SCHEMA_VERSION,
        "call_id": call_id,
        "tool_id": envelope["tool_id"],
        "agent": envelope["agent"],
        "envelope_sha256": canonical_json_sha256(envelope),
    }
    receipt = {**receipt_core, "hmac_sha256": _receipt_hmac(receipt_core)}
    encoded = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 4096:
        raise ScientificToolkitError("tool execution receipt exceeds 4 KiB")
    path = _receipt_path(call_id)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o400)
    except OSError as exc:
        raise ScientificToolkitError("unable to create authenticated tool execution receipt") from exc
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short write while recording tool execution receipt")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ScientificToolkitError("unable to finalize authenticated tool execution receipt") from exc
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, stat.S_IREAD)
    except OSError as exc:
        raise ScientificToolkitError("unable to seal authenticated tool execution receipt") from exc


def _load_execution_receipt(call_id: object) -> dict[str, Any]:
    path = _receipt_path(call_id)
    if not path.is_file() or _is_link_or_junction(path):
        raise ScientificToolkitError("local authenticated tool execution receipt not found")
    try:
        path.resolve(strict=True).relative_to(_receipt_directory().resolve(strict=True))
        file_stat = path.stat()
    except (OSError, ValueError) as exc:
        raise ScientificToolkitError("local authenticated tool execution receipt escaped its store") from exc
    if file_stat.st_nlink != 1 or file_stat.st_mode & (
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    ):
        raise ScientificToolkitError("local tool execution receipt is not sealed")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ScientificToolkitError("local authenticated tool execution receipt is unreadable") from exc
    if not raw or len(raw) > 4096:
        raise ScientificToolkitError("local authenticated tool execution receipt has invalid size")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ScientificToolkitError("local authenticated tool execution receipt is invalid") from exc
    if not isinstance(receipt, dict):
        raise ScientificToolkitError("local authenticated tool execution receipt is invalid")
    return receipt


def run_scientific_tool(
    tool_id: str,
    payload: dict[str, Any],
    agent: str,
    *,
    human_offline: bool = False,
) -> dict[str, Any]:
    agent = _effective_agent(agent, human_offline=human_offline)
    spec = _authorized_spec(tool_id, agent, human_offline=human_offline)
    if not human_offline:
        _receipt_authentication_key()
    payload = _mapping(payload, "input")
    _bounded_json(payload)
    started = datetime.now(timezone.utc)
    input_hash = canonical_json_sha256(payload)
    call_id = f"b3call-{uuid.uuid4().hex}"
    errors: list[dict[str, str]] = []
    try:
        result = _dispatch(
            tool_id, payload, agent, human_offline=human_offline
        )
        status_value = result.get("status") if isinstance(result, dict) else None
        if status_value in {"failed", "quarantined"}:
            status = str(status_value)
        elif status_value in {"inconclusive", "blocked"}:
            status = "inconclusive"
        else:
            status = "success"
    except (ScientificToolkitError, ScienceAgentError, ValueError) as exc:
        result = {}
        status = "failed"
        errors.append({"type": type(exc).__name__, "message": str(exc)[:500]})
    finished = datetime.now(timezone.utc)
    source_ids = sorted(
        {
            str(value)
            for value in re.findall(r"SRC_[A-Z0-9_]+", json.dumps(result, ensure_ascii=False))
        }
    )
    envelope: dict[str, Any] = {
        "schema_version": TOOL_RESULT_SCHEMA_VERSION,
        "tool_id": tool_id,
        "tool_version": TOOL_VERSION,
        "call_id": call_id,
        "agent": agent,
        "input": payload,
        "input_hash": input_hash,
        "status": status,
        "result": result,
        "output_hash": canonical_json_sha256(result),
        "provenance": {
            "parent_call_ids": [],
            "source_ids": source_ids,
            "model_id": os.getenv(
                "B3_AGENT_MODEL", "dashscope/qwen3.7-max-2026-06-08"
            ),
            "prompt_hash": None,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "package_lock_hash": _package_lock_hash(),
            "seed": payload.get("seed"),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "network_policy": spec.network_policy,
            "execution_trust": (
                "human_offline_unverified"
                if human_offline
                else "parent_bound_agent"
            ),
        },
        "claimable": False,
        "claimability": {},
        "errors": errors,
        "receipt": {
            "schema_version": TOOL_RECEIPT_SCHEMA_VERSION,
            "receipt_id": call_id,
            "storage": (
                "not_issued_human_offline"
                if human_offline
                else "local_hmac_authenticated"
            ),
        },
    }
    evaluated_claimability = _claimability(spec, result, status)
    if human_offline:
        envelope["claimability"] = {
            "policy": "human_offline_unverified_v1",
            "output_contract": spec.output_contract,
            "conditions": [
                {
                    "condition": "parent_bound_execution_receipt",
                    "satisfied": False,
                }
            ],
            "underlying_contract": evaluated_claimability,
            "satisfied": False,
        }
        envelope["claimable"] = False
    else:
        envelope["claimability"] = evaluated_claimability
        envelope["claimable"] = evaluated_claimability["satisfied"]
        _persist_execution_receipt(envelope)
    return envelope


def verify_tool_result(envelope: dict[str, Any], agent: str) -> dict[str, Any]:
    envelope = _mapping(envelope, "envelope")
    agent = _effective_agent(agent)
    required = {
        "schema_version",
        "tool_id",
        "tool_version",
        "call_id",
        "agent",
        "input",
        "input_hash",
        "status",
        "result",
        "output_hash",
        "provenance",
        "claimable",
        "claimability",
        "errors",
        "receipt",
    }
    missing = sorted(required - set(envelope))
    violations: list[str] = []
    if missing:
        violations.append("missing_fields:" + ",".join(missing))
    extras = sorted(set(envelope) - required)
    if extras:
        violations.append("unexpected_fields:" + ",".join(extras))
    if envelope.get("schema_version") != TOOL_RESULT_SCHEMA_VERSION:
        violations.append("schema_version")
    if envelope.get("tool_version") != TOOL_VERSION:
        violations.append("tool_version")
    spec = SPEC_BY_ID.get(envelope.get("tool_id"))
    if spec is None:
        violations.append("unregistered_tool_id")
    if agent not in _ALL_AGENTS:
        violations.append("unregistered_agent")
    if envelope.get("agent") != agent:
        violations.append("agent_mismatch")
    if spec is not None and agent not in spec.agents:
        violations.append("agent_not_authorized")
    if isinstance(envelope.get("input"), dict):
        if envelope.get("input_hash") != canonical_json_sha256(envelope["input"]):
            violations.append("input_hash_mismatch")
    else:
        violations.append("input_not_object")
    if isinstance(envelope.get("result"), dict):
        if envelope.get("output_hash") != canonical_json_sha256(envelope["result"]):
            violations.append("output_hash_mismatch")
    else:
        violations.append("result_not_object")
    if spec is not None and isinstance(envelope.get("result"), dict):
        expected_claimability = _claimability(
            spec, envelope["result"], str(envelope.get("status"))
        )
        if envelope.get("claimability") != expected_claimability:
            violations.append("claimability_contract_mismatch")
        if envelope.get("claimable") is not expected_claimability["satisfied"]:
            violations.append("claimable_mismatch")
    try:
        receipt = _load_execution_receipt(envelope.get("call_id"))
    except ScientificToolkitError as exc:
        receipt = None
        if "not found" in str(exc):
            violations.append("receipt_not_found")
        else:
            violations.append("receipt_invalid")
    if receipt is not None:
        stored_hash = receipt.get("envelope_sha256")
        receipt_fields = {
            "schema_version",
            "call_id",
            "tool_id",
            "agent",
            "envelope_sha256",
            "hmac_sha256",
        }
        receipt_core = {
            key: receipt.get(key)
            for key in receipt_fields
            if key != "hmac_sha256"
        }
        if (
            set(receipt) != receipt_fields
            or receipt.get("schema_version") != TOOL_RECEIPT_SCHEMA_VERSION
            or receipt.get("call_id") != envelope.get("call_id")
            or receipt.get("tool_id") != envelope.get("tool_id")
            or receipt.get("agent") != agent
            or not _sha256_text(stored_hash)
            or not _sha256_text(receipt.get("hmac_sha256"))
        ):
            violations.append("receipt_invalid")
        else:
            try:
                authenticated = hmac.compare_digest(
                    str(receipt["hmac_sha256"]), _receipt_hmac(receipt_core)
                )
            except ScientificToolkitError:
                authenticated = False
                violations.append("receipt_authentication_unavailable")
            if not authenticated:
                violations.append("receipt_authentication_failed")
            if stored_hash != canonical_json_sha256(envelope):
                violations.append("receipt_mismatch")
    return {
        "schema_version": TOOL_VERIFICATION_SCHEMA_VERSION,
        "verified": not violations,
        "tool_id": envelope.get("tool_id"),
        "call_id": envelope.get("call_id"),
        "agent": agent,
        "verification_basis": "parent_held_hmac_execution_receipt",
        "violations": violations,
    }


__all__ = [
    "SPEC_BY_ID",
    "TOOL_SPECS",
    "ScientificToolkitError",
    "discover_tools",
    "inspect_tool",
    "run_scientific_tool",
    "trace_artifact_lineage",
    "verify_tool_result",
]
