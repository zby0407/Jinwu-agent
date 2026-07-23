"""上游自动实验 Agent 产出的确定性读取与核验。

只读自动实验 runs/<run_id>/ 下的公开产物（entry_result.json / record.json /
report.md / audit.md / public/），由代码完成哈希核验与科学终态门。
未核验、缺哈希、哈希不匹配或终态非 completed_* 的输入一律阻断，
绝不降级为猜测（诚实性命根子）。

record.json 的 input_snapshot 中若引用了研究规划反馈（规划 Agent 已冻结，
其产出只能经此链路透入），一并登记为上游证据来源。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contracts import ContractError

ENTRY_RESULT_VERSION = "automatic-experiment-entry-result-v1"
RECORD_VERSION = "automatic-experiment-record-v1"

# 只有科学终态为 completed_* 的产出允许作为证据来源；其余一律阻断。
ELIGIBLE_OUTCOME_PREFIX = "completed_"

ENTRY_RESULT_REQUIRED = {
    "schema_version",
    "status",
    "run_id",
    "outcome",
    "record_path",
    "record_sha256",
    "report_path",
    "report_sha256",
    "created_at",
    "entry_sha256",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_PLANNING_HINT = re.compile(r"研究规划|规划反馈|research_plan|plan_feedback", re.IGNORECASE)

# 已知数据产品的覆盖范围（写死的领域约束：可泛化表述超出覆盖范围时
# 置信度不得为 high）。pattern 命中材料文本即认为该材料来自对应产品。
KNOWN_DATA_COVERAGES: tuple[dict[str, Any], ...] = (
    {
        "product": "JW-FD 磁图数据集",
        "pattern": re.compile(r"JW-FD|JW_FD", re.IGNORECASE),
        "coverage": "仅覆盖 2011 年前后个别活动区（AR 系列）的短时磁图观测",
        "scope_pattern": re.compile(r"跨周期|所有活动周|普遍成立|任意活动周|每个活动周"),
    },
)

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SUMMARY_CHARS = 400
MAX_EXCERPT_CHARS = 1_500


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_limited(path: Path, label: str) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ContractError(f"{label} 超过 4 MiB 上限")
    return path.read_text(encoding="utf-8")


def _resolve_within(root: Path, relative_path: object, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ContractError(f"{label} 必须是非空字符串")
    text = relative_path.strip()
    if Path(text).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", text):
        raise ContractError(f"{label} 必须是 run 目录内的相对路径")
    parts = [part for part in re.split(r"[/\\]+", text) if part not in {"", "."}]
    if not parts or any(part == ".." or not _SAFE_SEGMENT.match(part) for part in parts):
        raise ContractError(f"{label} 含有不安全的路径段")
    resolved = (root / Path(*parts)).resolve()
    if resolved != root and root not in resolved.parents:
        raise ContractError(f"{label} 越出了 run 目录")
    return resolved


def _summarize(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _block(
    run_id: str,
    code: str,
    message: str,
    *,
    outcome: str | None = None,
    integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "scientific-hypothesis-upstream-inspection-v1",
        "status": "blocked",
        "run_id": run_id,
        "blocker_code": code,
        "user_message": (
            "该上游产物未通过完整性核验，已阻断：不得据此形成或支持任何假设，"
            "只能列为证据缺口。原因：" + message
        ),
        "integrity": integrity or {},
    }
    if outcome is not None:
        result["outcome"] = outcome
    return result


def _scan_data_coverages(corpus: str) -> list[dict[str, str]]:
    found = []
    for spec in KNOWN_DATA_COVERAGES:
        if spec["pattern"].search(corpus):
            found.append({"product": spec["product"], "coverage": spec["coverage"]})
    return found


def inspect_experiment_run(payload: object, project_root: Path) -> dict[str, Any]:
    """读取并核验一个自动实验 run 目录，产出已核验证据摘要或阻断结果。"""

    if not isinstance(payload, dict):
        raise ContractError("inspect_upstream 载荷必须是 JSON 对象")
    unknown = sorted(set(payload) - {"run_path"})
    if unknown:
        raise ContractError(f"inspect_upstream 载荷存在未定义字段：{', '.join(unknown)}")
    run_path_raw = payload.get("run_path")
    if not isinstance(run_path_raw, str) or not run_path_raw.strip():
        raise ContractError("run_path 必须是非空字符串")

    run_dir = Path(run_path_raw).expanduser()
    if not run_dir.is_absolute():
        run_dir = Path(project_root) / run_dir
    try:
        run_dir = run_dir.resolve(strict=True)
    except OSError:
        return _block(str(run_path_raw), "run_not_found", "run 目录不存在")
    if not run_dir.is_dir():
        return _block(str(run_path_raw), "run_not_found", "run 路径不是目录")

    run_id = run_dir.name
    integrity: dict[str, Any] = {}

    entry_file = run_dir / "entry_result.json"
    record_file = run_dir / "record.json"
    if not entry_file.is_file():
        return _block(run_id, "missing_entry_result", "缺少 entry_result.json")
    if not record_file.is_file():
        return _block(run_id, "missing_record", "缺少 record.json")

    try:
        entry = json.loads(_read_limited(entry_file, "entry_result.json"))
        record = json.loads(_read_limited(record_file, "record.json"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return _block(run_id, "malformed_json", f"产出文件不是合法 UTF-8 JSON：{exc}")

    if not isinstance(entry, dict) or not isinstance(record, dict):
        return _block(run_id, "malformed_json", "产出文件必须是 JSON 对象")

    missing = sorted(ENTRY_RESULT_REQUIRED - set(entry))
    if missing:
        return _block(
            run_id, "missing_fields", f"entry_result.json 缺少字段：{', '.join(missing)}"
        )
    if entry.get("schema_version") != ENTRY_RESULT_VERSION:
        return _block(
            run_id,
            "unexpected_schema",
            f"entry_result schema_version 应为 {ENTRY_RESULT_VERSION}，"
            f"实际为 {entry.get('schema_version')}",
        )
    if record.get("schema_version") != RECORD_VERSION:
        return _block(
            run_id,
            "unexpected_schema",
            f"record schema_version 应为 {RECORD_VERSION}，"
            f"实际为 {record.get('schema_version')}",
        )
    if entry.get("run_id") != run_id:
        return _block(
            run_id,
            "run_id_mismatch",
            f"entry_result 的 run_id 为 {entry.get('run_id')}，与目录名 {run_id} 不一致",
        )
    if entry.get("status") != "finalized":
        return _block(
            run_id,
            "not_finalized",
            f"entry_result status 为 {entry.get('status')}，非 finalized",
            outcome=str(entry.get("outcome")),
        )

    for field in ("record_sha256", "report_sha256", "entry_sha256"):
        if not _SHA256_RE.match(str(entry.get(field, ""))):
            return _block(run_id, "missing_hash", f"entry_result.{field} 缺失或不是 64 位小写 sha256")

    actual_record_sha = _file_sha256(record_file)
    integrity["record_sha256_match"] = actual_record_sha == entry["record_sha256"]
    if not integrity["record_sha256_match"]:
        return _block(
            run_id,
            "hash_mismatch",
            "record.json 实际哈希与 entry_result 声明不一致，产出可能已被改动",
            outcome=str(entry.get("outcome")),
            integrity=integrity,
        )

    report_file = _resolve_within(run_dir, entry.get("report_path"), "entry_result.report_path")
    if not report_file.is_file():
        return _block(run_id, "missing_report", "report_path 指向的文件不存在")
    actual_report_sha = _file_sha256(report_file)
    integrity["report_sha256_match"] = actual_report_sha == entry["report_sha256"]
    if not integrity["report_sha256_match"]:
        return _block(
            run_id,
            "hash_mismatch",
            "report.md 实际哈希与 entry_result 声明不一致，产出可能已被改动",
            outcome=str(entry.get("outcome")),
            integrity=integrity,
        )

    audit_sha_match: bool | None = None
    audit_path = entry.get("audit_path")
    audit_declared = entry.get("audit_sha256")
    if audit_path is not None or audit_declared is not None:
        if not (isinstance(audit_path, str) and _SHA256_RE.match(str(audit_declared or ""))):
            return _block(run_id, "missing_hash", "audit_path/audit_sha256 声明不完整")
        audit_file = _resolve_within(run_dir, audit_path, "entry_result.audit_path")
        if not audit_file.is_file():
            return _block(run_id, "missing_audit", "audit_path 指向的文件不存在")
        audit_sha_match = _file_sha256(audit_file) == audit_declared
        if not audit_sha_match:
            integrity["audit_sha256_match"] = False
            return _block(
                run_id,
                "hash_mismatch",
                "audit.md 实际哈希与 entry_result 声明不一致，产出可能已被改动",
                outcome=str(entry.get("outcome")),
                integrity=integrity,
            )
    integrity["audit_sha256_match"] = audit_sha_match

    outcome = str(entry.get("outcome"))
    integrity["verification_checks_all_passed"] = (
        record.get("verification_checks_all_passed")
        if "verification_checks_all_passed" in record
        else None
    )
    if outcome.startswith(ELIGIBLE_OUTCOME_PREFIX):
        integrity["outcome_eligible"] = True
    else:
        integrity["outcome_eligible"] = False
        return _block(
            run_id,
            "outcome_not_eligible",
            f"科学终态为 {outcome}；只有 completed_* 终态的产出可作为证据来源",
            outcome=outcome,
            integrity=integrity,
        )

    record_path_declared = entry.get("record_path")
    try:
        declared_record_file = _resolve_within(
            run_dir, record_path_declared, "entry_result.record_path"
        )
        integrity["record_path_consistent"] = declared_record_file == record_file
    except ContractError:
        integrity["record_path_consistent"] = False

    narrative = (
        (record.get("scientific_assessment") or {}).get("report_narrative") or {}
        if isinstance(record.get("scientific_assessment"), dict)
        else {}
    )
    claim_boundary = narrative.get("claim_boundary")
    data_scope = narrative.get("data_scope")
    evidence_strength = narrative.get("evidence_strength")

    report_text = _read_limited(report_file, "report.md")

    planning_sources: list[dict[str, Any]] = []
    snapshot = record.get("input_snapshot")
    if isinstance(snapshot, dict):
        for input_row in snapshot.get("inputs") or []:
            if not isinstance(input_row, dict):
                continue
            for file_row in input_row.get("files") or []:
                if not isinstance(file_row, dict):
                    continue
                label = " ".join(
                    str(file_row.get(key) or "")
                    + " "
                    + str(input_row.get("source_path") or "")
                    for key in ("path",)
                )
                if _PLANNING_HINT.search(label):
                    sha = file_row.get("sha256")
                    planning_sources.append(
                        {
                            "input_id": str(input_row.get("id") or ""),
                            "path": str(file_row.get("path") or ""),
                            "source_path": str(input_row.get("source_path") or ""),
                            "sha256": sha if _SHA256_RE.match(str(sha or "")) else None,
                            "note": "研究规划反馈（经自动实验 input_snapshot 透入）",
                        }
                    )

    public_artifacts: list[dict[str, Any]] = []
    for artifact in record.get("public_artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        public_artifacts.append(
            {
                "path": str(artifact.get("path") or ""),
                "kind": str(artifact.get("kind") or ""),
                "sha256": str(artifact.get("sha256") or ""),
                "description": str(artifact.get("description") or ""),
            }
        )

    coverage_corpus = "\n".join(
        str(part)
        for part in (report_text, claim_boundary, data_scope, json.dumps(
            record.get("task") or {}, ensure_ascii=False
        ))
    )
    data_coverages = _scan_data_coverages(coverage_corpus)

    return {
        "schema_version": "scientific-hypothesis-upstream-inspection-v1",
        "status": "verified",
        "run_id": run_id,
        "outcome": outcome,
        "verified_at": str(record.get("verified_at") or entry.get("created_at") or ""),
        "integrity": integrity,
        "evidence_summary": {
            "outcome_reason": _summarize(str(record.get("outcome_reason") or "")),
            "claim_boundary": str(claim_boundary or ""),
            "data_scope": str(data_scope or ""),
            "evidence_strength": str(evidence_strength or ""),
            "report_excerpt": _summarize(report_text, MAX_EXCERPT_CHARS),
        },
        "public_artifacts": public_artifacts,
        "planning_sources": planning_sources,
        "data_coverages": data_coverages,
        "user_message": (
            f"上游产物 {run_id} 已通过哈希与终态核验，可作为已核验证据来源绑定。"
        ),
    }


__all__ = [
    "ELIGIBLE_OUTCOME_PREFIX",
    "KNOWN_DATA_COVERAGES",
    "inspect_experiment_run",
]
