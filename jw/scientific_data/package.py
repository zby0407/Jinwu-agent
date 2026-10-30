"""The deliverable: one deterministic, self-describing data package.

Everything a reader needs sits in one directory: the merged table, where each
row came from, what was questioned, what was fixed and who approved it. The
writer never invents values: it only serialises what the ledger already holds.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    ArtifactIndex,
    IntegrationLedger,
    ObservationRecord,
    RepairLedgerEntry,
    file_digest,
    utc_now,
)
from .domain import DomainPlugin
from .figures import FigureCalibration, OverlayReport
from .human_loop import HumanQueue
from .sources import catalog_csv

__all__ = [
    "OBSERVATION_COLUMNS",
    "FigurePackage",
    "PackageResult",
    "refresh_manifest",
    "write_package",
]

OBSERVATION_COLUMNS = (
    "record_uid",
    "entity_id",
    "entity_label",
    "observable",
    "value",
    "unit",
    "value_raw",
    "unit_raw",
    "time",
    "time_raw",
    "time_system",
    "uncertainty",
    "uncertainty_kind",
    "value_kind",
    "missing_reason",
    "confidence",
    "confidence_score",
    "flags",
    "conflict",
    "source_ids",
    "source_count",
    "provenance",
)

_ISSUE_COLUMNS = (
    "issue_id",
    "code",
    "severity",
    "state",
    "stage",
    "repair_mode",
    "blocking",
    "auto_applied",
    "subject",
    "detail",
    "evidence",
    "proposed_action",
)

_REPAIR_COLUMNS = (
    "repair_id",
    "issue_id",
    "code",
    "mode",
    "actor",
    "target",
    "before",
    "after",
    "rationale",
    "reversible",
    "applied",
    "approved_at",
    "revalidated_at",
    "revalidation",
    "before_hash",
    "after_hash",
)

_MAPPING_COLUMNS = (
    "mapping_id",
    "source_id",
    "table_id",
    "source_column",
    "observable",
    "confidence",
    "applied",
    "target_unit",
    "source_unit",
    "transformation",
    "evidence",
)

_FIGURE_COLUMNS = (
    "series",
    "pixel_x",
    "pixel_y",
    "x_value",
    "y_value",
    "value",
    "unit",
    "time",
    "pixel_residual",
    "pixel_noise",
    "x_scale",
    "y_scale",
)


@dataclass(frozen=True, slots=True)
class FigurePackage:
    """One digitised figure: its calibration, points and overlay verdict."""

    figure_id: str
    source_id: str
    calibration: FigureCalibration
    records: tuple[ObservationRecord, ...] = ()
    overlay: OverlayReport | None = None


@dataclass(slots=True)
class PackageResult:
    """Where the package landed and what each file hashes to."""

    directory: str
    files: dict[str, str] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def observations(self) -> str:
        return self.files.get("observations", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "files": dict(self.files),
            "digests": dict(self.digests),
            "counts": dict(self.counts),
        }


def write_package(
    *,
    directory: str | Path,
    ledger: IntegrationLedger,
    plugin: DomainPlugin | None = None,
    queue: HumanQueue | None = None,
    figures: Sequence[FigurePackage] = (),
    notes: Sequence[str] = (),
) -> PackageResult:
    """Write the standard output package for one integration run."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    overlays = root / "figure_overlays"
    overlays.mkdir(exist_ok=True)

    result = PackageResult(directory=str(root))
    records = _sorted_records(ledger.records)

    result.files["observations"] = _write_csv(
        root / "integrated_observations.csv",
        OBSERVATION_COLUMNS,
        (_observation_row(record) for record in records),
    )
    result.files["sources"] = _write_csv(
        root / "source_catalog.csv",
        _CATALOG_HEADER,
        _catalog_rows(ledger),
    )
    result.files["mappings"] = _write_csv(
        root / "field_mapping.csv", _MAPPING_COLUMNS, _mapping_rows(ledger)
    )
    result.files["issues"] = _write_csv(
        root / "quality_issues.csv", _ISSUE_COLUMNS, _issue_rows(ledger)
    )
    result.files["repairs"] = _write_csv(
        root / "repair_log.csv", _REPAIR_COLUMNS, _repair_rows(ledger)
    )
    result.files["provenance"] = _write_jsonl(
        root / "provenance.jsonl", _provenance_rows(records)
    )
    figure_files = {
        item.figure_id: _write_csv(
            overlays / f"{_safe_name(item.figure_id)}.csv",
            _FIGURE_COLUMNS,
            _figure_rows(item),
        )
        for item in figures
    }
    if queue is not None:
        result.files["human_review"] = _write_json(
            root / "human_review.json", queue.to_dict()
        )
    result.files["report"] = _write_text(
        root / "data_quality_report.md",
        _report(ledger, plugin=plugin, records=records, figures=figures, queue=queue),
    )

    digests = {role: file_digest(path) for role, path in result.files.items()}
    digests.update(
        {
            f"figure_overlays/{name}": file_digest(path)
            for name, path in figure_files.items()
        }
    )
    result.files.update(
        {f"figure_overlays/{name}": path for name, path in figure_files.items()}
    )
    result.digests = digests

    counts = {
        "records": len(records),
        "entities": len({record.entity_id for record in records}),
        "observables": len({record.observable for record in records}),
        "sources": len(ledger.sources),
        "issues": len(ledger.issues),
        "repairs": len(ledger.repairs),
        "figures": len(figures),
    }
    result.counts = counts

    result.files["readme"] = _write_text(
        root / "README_data.md", _readme(ledger, counts)
    )
    result.digests["readme"] = file_digest(result.files["readme"])

    refresh_manifest(directory=root, ledger=ledger, result=result, notes=notes)
    return result


def refresh_manifest(
    *,
    directory: str | Path,
    ledger: IntegrationLedger,
    result: PackageResult,
    notes: Sequence[str] = (),
) -> str:
    """Write the manifest last, so its digests match the files on disk.

    Call this again after the run records a stage that the manifest reports,
    e.g. once the package stage itself is complete.
    """
    root = Path(directory)
    payload = _manifest(ledger, result, counts=dict(result.counts), notes=notes)
    payload["artifacts"] = [
        {
            "role": role,
            "path": _relative(result.files.get(role, ""), root),
            "sha256": digest,
        }
        for role, digest in sorted(result.digests.items())
        if role != "manifest"
    ]
    path = _write_json(root / "run_manifest.json", payload)
    result.files["manifest"] = path
    result.digests["manifest"] = file_digest(path)
    return path


# -- record rows ---------------------------------------------------------


def _sorted_records(records: Iterable[ObservationRecord]) -> list[ObservationRecord]:
    return sorted(
        records,
        key=lambda item: (
            item.entity_id,
            item.observable,
            item.time,
            item.record_uid,
        ),
    )


def _observation_row(record: ObservationRecord) -> dict[str, Any]:
    reference = record.provenance[0] if record.provenance else None
    return {
        "record_uid": record.record_uid,
        "entity_id": record.entity_id,
        "entity_label": record.attributes.get("label", ""),
        "observable": record.observable,
        "value": _number(record.value),
        "unit": record.unit,
        "value_raw": record.value_raw,
        "unit_raw": record.unit_raw,
        "time": record.time,
        "time_raw": record.time_raw,
        "time_system": record.time_system,
        "uncertainty": _number(record.uncertainty),
        "uncertainty_kind": record.uncertainty_kind,
        "value_kind": str(record.value_kind),
        "missing_reason": str(record.missing_reason) if record.value is None else "",
        "confidence": str(reference.confidence) if reference else "",
        "confidence_score": _number(reference.confidence_score, digits=3)
        if reference
        else "",
        "flags": " | ".join(record.flags),
        "conflict": "yes" if record.conflict else "",
        "source_ids": " | ".join(record.source_ids),
        "source_count": len(record.source_ids),
        "provenance": _locator_summary(reference.locator) if reference else "",
    }


def _provenance_rows(records: Sequence[ObservationRecord]) -> Iterable[dict[str, Any]]:
    for record in records:
        yield {
            "record_uid": record.record_uid,
            "entity_id": record.entity_id,
            "observable": record.observable,
            "value": record.value,
            "unit": record.unit,
            "value_raw": record.value_raw,
            "value_kind": str(record.value_kind),
            "source_ids": list(record.source_ids),
            "provenance": [item.to_dict() for item in record.provenance],
        }


def _locator_summary(locator: Mapping[str, Any]) -> str:
    keys = ("path", "line_start", "line_end", "header_row", "figure_id", "series")
    parts = [
        f"{key}={locator[key]}" for key in keys if locator.get(key) not in (None, "")
    ]
    return " ".join(parts)


# -- catalog, mapping, issue, repair rows --------------------------------


_CATALOG_HEADER = (
    "source_id",
    "role",
    "title",
    "provider",
    "canonical_uri",
    "format_hint",
    "retrieval",
    "identifier",
    "license",
    "bytes",
    "content_sha256",
    "discovered_via",
    "failure_reason",
    "notes",
)


def _catalog_rows(ledger: IntegrationLedger) -> Iterable[dict[str, Any]]:
    if not ledger.sources:
        return ()
    text = catalog_csv(tuple(ledger.sources.values()))
    reader = csv.DictReader(text.splitlines())
    return tuple(dict(row) for row in reader)


def _mapping_rows(ledger: IntegrationLedger) -> Iterable[dict[str, Any]]:
    for mapping in sorted(ledger.mappings, key=lambda item: item.mapping_id):
        yield {
            "mapping_id": mapping.mapping_id,
            "source_id": mapping.source_id,
            "table_id": mapping.table_id,
            "source_column": mapping.source_column,
            "observable": mapping.observable,
            "confidence": str(mapping.confidence),
            "applied": "yes" if mapping.applied else "no",
            "target_unit": mapping.target_unit,
            "source_unit": mapping.source_unit,
            "transformation": mapping.transformation,
            "evidence": mapping.evidence,
        }


def _issue_rows(ledger: IntegrationLedger) -> Iterable[dict[str, Any]]:
    for issue in sorted(ledger.issues.values(), key=lambda item: item.issue_id):
        yield {
            "issue_id": issue.issue_id,
            "code": str(issue.code),
            "severity": str(issue.severity),
            "state": str(issue.state),
            "stage": issue.stage,
            "repair_mode": str(issue.repair_mode),
            "blocking": "yes" if issue.blocking else "",
            "auto_applied": "yes" if issue.auto_applied else "",
            "subject": _compact_json(issue.subject),
            "detail": issue.detail,
            "evidence": _compact_json(issue.evidence),
            "proposed_action": issue.proposed_action,
        }


def _repair_rows(ledger: IntegrationLedger) -> Iterable[dict[str, Any]]:
    for entry in sorted(ledger.repairs.values(), key=lambda item: item.repair_id):
        yield _repair_row(entry)


def _repair_row(entry: RepairLedgerEntry) -> dict[str, Any]:
    return {
        "repair_id": entry.repair_id,
        "issue_id": entry.issue_id,
        "code": str(entry.code),
        "mode": str(entry.mode),
        "actor": entry.actor,
        "target": entry.target,
        "before": entry.before,
        "after": entry.after,
        "rationale": entry.rationale,
        "reversible": "yes" if entry.reversible else "no",
        "applied": "yes" if entry.applied else "no",
        "approved_at": entry.approved_at,
        "revalidated_at": entry.revalidated_at,
        "revalidation": entry.revalidation,
        "before_hash": entry.before_hash,
        "after_hash": entry.after_hash,
    }


# -- figure rows ---------------------------------------------------------


def _figure_rows(item: FigurePackage) -> Iterable[dict[str, Any]]:
    residuals = _residual_map(item)
    for record in item.records:
        locator = record.provenance[0].locator if record.provenance else {}
        yield {
            "series": locator.get("series", ""),
            "pixel_x": locator.get("pixel_x", ""),
            "pixel_y": locator.get("pixel_y", ""),
            "x_value": locator.get("x_value", ""),
            "y_value": locator.get("y_value", ""),
            "value": _number(record.value),
            "unit": record.unit,
            "time": record.time,
            "pixel_residual": _number(residuals.get(record.record_uid), digits=3),
            "pixel_noise": item.calibration.pixel_noise,
            "x_scale": item.calibration.x.scale,
            "y_scale": item.calibration.y.scale,
        }


def _residual_map(item: FigurePackage) -> dict[str, float]:
    overlay = item.overlay
    if overlay is None:
        return {}
    ordered = [record for record in item.records if record.provenance]
    if len(ordered) != len(overlay.residuals):
        return {}
    return {
        record.record_uid: value
        for record, value in zip(ordered, overlay.residuals, strict=True)
    }


# -- report --------------------------------------------------------------


def _report(
    ledger: IntegrationLedger,
    *,
    plugin: DomainPlugin | None,
    records: Sequence[ObservationRecord],
    figures: Sequence[FigurePackage],
    queue: HumanQueue | None,
) -> str:
    counts = ledger.summary_counts()
    contract = ledger.contract
    lines: list[str] = [
        "# 数据整合质量报告",
        "",
        "## 1. 运行概要",
        "",
        f"- 运行编号: `{ledger.run_id}`",
        f"- 研究目标: {contract.research_goal or '(未填写)'}",
        f"- 实体类别: {contract.entity_class or '(未指定)'}",
        f"- 领域插件: {(plugin.name if plugin else contract.domain_plugin) or 'generic'}",
        f"- 记录数: {len(records)}；实体数: {len({r.entity_id for r in records})}",
        f"- 来源数: {counts['sources']}（已获取 {counts['acquired_sources']}）",
        f"- 表格数: {counts['tables']}；字段映射数: {len(ledger.mappings)}",
        f"- 质量问题: {counts['issues']}（未关闭 {counts['open_issues']}）",
        f"- 修正记录: {counts['repairs']}（自动执行 {counts['applied_repairs']}）",
        "",
        "## 2. 数据来源",
        "",
    ]
    lines.extend(_source_lines(ledger))
    lines.extend(["", "## 3. 观测覆盖与缺失", ""])
    lines.extend(_coverage_lines(records, contract=contract))
    lines.extend(["", "## 4. 质量问题", ""])
    lines.extend(_issue_lines(ledger))
    lines.extend(["", "## 5. 修正与人工确认", ""])
    lines.extend(_repair_lines(ledger, queue=queue))
    lines.extend(["", "## 6. 图表识别、提取与校验", ""])
    lines.extend(_figure_lines(figures))
    lines.extend(["", "## 7. 复现性", ""])
    lines.extend(
        [
            f"- 合同指纹: `{contract.fingerprint}`",
            f"- 数据模式: `{SCHEMA_VERSION}`",
            f"- 生成时间: {utc_now()}",
            "- 同一冻结输入重跑应得到相同的 `record_uid`、`issue_id` 与文件哈希。",
            "",
        ]
    )
    unresolved = [
        record
        for record in records
        if record.conflict or record.value_kind.value in {"upper_limit", "lower_limit"}
    ]
    lines.extend(["## 8. 局限与待确认", ""])
    if unresolved:
        lines.append(
            f"- 仍有 {len(unresolved)} 条记录存在跨源冲突或上下限标记，未做任何平均或删除，"
            "请结合 `quality_issues.csv` 判断。"
        )
    else:
        lines.append("- 未发现仍未解决的跨源冲突。")
    if figures:
        failed = [
            item.figure_id
            for item in figures
            if item.overlay and not item.overlay.passed
        ]
        if failed:
            lines.append(
                f"- 图表叠加校验未通过: {', '.join(sorted(failed))}；这些点已标记为 "
                "digitized 且不建议直接用于定量拟合。"
            )
    lines.append("")
    return "\n".join(lines)


def _source_lines(ledger: IntegrationLedger) -> list[str]:
    if not ledger.sources:
        return ["- 本次运行没有登记任何来源。"]
    lines = [
        "| 来源 | 角色 | 检索状态 | 格式 | 标题 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source in sorted(ledger.sources.values(), key=lambda item: item.source_id):
        lines.append(
            f"| `{source.source_id}` | {source.role} | {source.retrieval} | "
            f"{source.format_hint or '-'} | {_escape(source.title) or '-'} |"
        )
    return lines


def _coverage_lines(
    records: Sequence[ObservationRecord], *, contract: Any
) -> list[str]:
    if not records:
        return ["- 没有可用观测记录。"]
    by_observable = Counter(record.observable for record in records)
    missing = Counter(
        str(record.missing_reason) for record in records if record.value is None
    )
    kinds = Counter(str(record.value_kind) for record in records)
    lines = ["| 观测量 | 记录数 | 单位 |", "| --- | --- | --- |"]
    units: dict[str, set[str]] = {}
    for record in records:
        units.setdefault(record.observable, set()).add(record.unit)
    for observable, count in sorted(by_observable.items()):
        lines.append(
            f"| {observable} | {count} | {', '.join(sorted(units[observable])) or '-'} |"
        )
    lines.extend(["", f"- 取值类型分布: {_counter_text(kinds)}"])
    if missing:
        lines.append(
            f"- 缺失记录 {sum(missing.values())} 条，原因分布: {_counter_text(missing)}"
        )
        lines.append(
            "- 缺失不会被插补：每条记录都保留原始文本与 `missing_reason` 以便人工判断。"
        )
    if contract.coverage_window:
        lines.append(f"- 合同要求覆盖窗口: {contract.coverage_window}")
    return lines


def _issue_lines(ledger: IntegrationLedger) -> list[str]:
    issues = sorted(ledger.issues.values(), key=lambda item: item.issue_id)
    if not issues:
        return ["- 未检测到质量问题。"]
    by_code = Counter(str(issue.code) for issue in issues)
    by_mode = Counter(str(issue.repair_mode) for issue in issues)
    lines = [
        f"- 按代码统计: {_counter_text(by_code)}",
        f"- 按修正策略统计: {_counter_text(by_mode)}",
        "",
        "| 问题 | 代码 | 级别 | 状态 | 修正策略 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for issue in issues:
        lines.append(
            f"| `{issue.issue_id}` | {issue.code} | {issue.severity} | {issue.state} | "
            f"{issue.repair_mode} | {_escape(issue.detail)} |"
        )
    blocking = [issue for issue in issues if issue.blocking]
    if blocking:
        lines.extend(
            ["", f"- 阻断性问题 {len(blocking)} 条，需人工处理后才能给出可用结论。"]
        )
    return lines


def _repair_lines(ledger: IntegrationLedger, *, queue: HumanQueue | None) -> list[str]:
    lines: list[str] = []
    entries = sorted(ledger.repairs.values(), key=lambda item: item.repair_id)
    if entries:
        lines.extend(
            [
                "| 修正 | 代码 | 执行者 | 前 | 后 | 复核 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for entry in entries:
            lines.append(
                f"| `{entry.repair_id}` | {entry.code} | {entry.actor} | "
                f"{_escape(entry.before)} | {_escape(entry.after)} | "
                f"{entry.revalidation or '-'} |"
            )
    else:
        lines.append("- 没有执行任何自动修正。")
    if queue is not None:
        open_items = queue.open_requests
        lines.extend(["", f"- 人工待确认 {len(open_items)} 项。"])
        for request in open_items[:10]:
            lines.append(
                f"  - `{request.request_id}` ({request.code}): {_escape(request.question)}"
            )
        if len(open_items) > 10:
            lines.append(
                f"  - ... 另有 {len(open_items) - 10} 项，见 human_review.json"
            )
    lines.append(
        "- 自动修正只覆盖可逆且不改写科学含义的情形（重复行、非有限值、可判定的单位换算、"
        "截断行）；实体匹配、坐标轴判读、跨源数值冲突一律等待人工。"
    )
    return lines


def _figure_lines(figures: Sequence[FigurePackage]) -> list[str]:
    if not figures:
        return ["- 本次运行未使用图表数字化数据。"]
    lines = [
        "| 图 | 来源 | x 轴 | y 轴 | 像素误差 | 叠加校验 | 最大残差 (px) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in figures:
        overlay = item.overlay
        verdict = "未校验"
        residual = "-"
        if overlay is not None:
            verdict = "通过" if overlay.passed else "未通过"
            residual = f"{overlay.max_residual:.2f}"
        lines.append(
            f"| `{item.figure_id}` | `{item.source_id or '-'}` | "
            f"{item.calibration.x.scale} ({item.calibration.x.unit or '-'}) | "
            f"{item.calibration.y.scale} ({item.calibration.y.unit or '-'}) | "
            f"{item.calibration.pixel_noise:.2f} | {verdict} | {residual} |"
        )
    lines.extend(
        [
            "",
            "识别方式: 刻度位置与其标签值做最小二乘拟合，对数轴在 log10 空间拟合；",
            "提取方式: 标记点像素坐标经拟合映射为数据值，像素噪声按局部斜率折算为不确定度；",
            "校验方式: 把恢复出的数值反投影回像素坐标，与原始标记位置比较残差，超过容差的图"
            "会被标记为 `figure_overlay_mismatch` 并保留 `digitized` 取值类型。",
        ]
    )
    return lines


# -- manifest, readme ----------------------------------------------------


def _manifest(
    ledger: IntegrationLedger,
    result: PackageResult,
    *,
    counts: Mapping[str, int],
    notes: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": ledger.run_id,
        "generated_at": utc_now(),
        "contract": ledger.contract.to_dict(),
        "contract_fingerprint": ledger.contract.fingerprint,
        "counts": dict(counts),
        "summary": ledger.summary_counts(),
        "stages": {
            name: {
                "state": str(receipt.state),
                "counts": dict(receipt.counts),
                "finished_at": receipt.finished_at,
                "error": receipt.error,
            }
            for name, receipt in sorted(ledger.stages.items())
        },
        "artifacts": [],
        "notes": [*notes, *ledger.notes],
        "files": dict(result.files),
    }


def _readme(ledger: IntegrationLedger, counts: Mapping[str, int]) -> str:
    contract = ledger.contract
    lines = [
        "# 数据包说明",
        "",
        f"运行 `{ledger.run_id}` 输出，面向研究目标：{contract.research_goal or '(未填写)'}。",
        "",
        "## 文件清单",
        "",
        "| 文件 | 内容 |",
        "| --- | --- |",
        "| `integrated_observations.csv` | 合并后的多源观测表，每行一条观测并带来源与溯源摘要 |",
        "| `source_catalog.csv` | 所有检索到的来源、角色、检索状态与校验哈希 |",
        "| `field_mapping.csv` | 每个源列到观测量的映射、置信度与单位变换 |",
        "| `quality_issues.csv` | 全部质量问题的代码、证据、修正策略与状态 |",
        "| `repair_log.csv` | 每一次修正的前后值、执行者、复核结果与哈希 |",
        "| `provenance.jsonl` | 逐记录、逐层级的溯源（文件、行号、图区、序列） |",
        "| `data_quality_report.md` | 面向人的质量报告与图表数字化说明 |",
        "| `run_manifest.json` | 合同、阶段回执、产物哈希，用于复现校验 |",
        "| `human_review.json` | 待人工确认的问题与建议选项（如有） |",
        "| `figure_overlays/` | 每个图的数字化点与反投影残差 |",
        "",
        "## 字段字典（integrated_observations.csv）",
        "",
        "| 列 | 含义 |",
        "| --- | --- |",
        "| `record_uid` | 记录稳定标识，同一冻结输入下可复现 |",
        "| `entity_id` | 统一后的实体标识 |",
        "| `entity_label` | 行内实体标签（若来源提供） |",
        "| `observable` | 观测量名，取自领域插件或合同 |",
        "| `value` | 规范化后的数值；不可解析或缺失时为空 |",
        "| `unit` | 规范化单位；未能换算时保留原单位 |",
        "| `value_raw` | 作者原文数值（含 `<`、`>`、千分位等） |",
        "| `unit_raw` | 作者原文单位 |",
        "| `time` | 规范化时间（MJD 会转成 ISO 时间戳） |",
        "| `time_raw` | 作者原文时间 |",
        "| `time_system` | mjd / jd / iso / phase / relative 等 |",
        "| `uncertainty`、`uncertainty_kind` | 不确定度及类型（论文值、数字化估计等） |",
        "| `value_kind` | reported / derived / digitized / upper_limit / lower_limit / model |",
        "| `missing_reason` | 缺失原因：未报告、未解析、未观测、不适用、探测不到 |",
        "| `confidence`、`confidence_score` | 溯源置信度分级与数值 |",
        "| `flags` | 处理标记（换算、重复、时间判读等） |",
        "| `conflict` | `yes` 表示跨源取值冲突，未做平均 |",
        "| `source_ids`、`source_count` | 该记录合并到的来源 |",
        "| `provenance` | 溯源摘要（文件、行号、图表序列） |",
        "",
        "## 使用须知",
        "",
        f"- 本次输出 {counts['records']} 条记录、{counts['entities']} 个实体、"
        f"{counts['sources']} 个来源。",
        "- 任何被标记 `conflict=yes`、`digitized`、上下限或 `missing_reason` 的值都需要人工复核。",
        "- 自动修正仅限可逆操作；所有修正都记录在 `repair_log.csv` 并可回退。",
        "",
    ]
    return "\n".join(lines)


# -- primitives ----------------------------------------------------------


def _write_csv(
    path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})
    return str(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path)


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _number(value: Any, *, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(value)
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _counter_text(counter: Mapping[str, int]) -> str:
    if not counter:
        return "无"
    return ", ".join(f"{name}: {count}" for name, count in sorted(counter.items()))


def _escape(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _safe_name(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-" for char in value
    ).strip("-")
    return cleaned or "figure"


def _relative(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def artifact_for(
    role: str, path: str, *, rows: int = 0, note: str = ""
) -> ArtifactIndex:
    """Small helper so callers register package outputs in the ledger."""
    return ArtifactIndex(
        role=role, path=path, sha256=file_digest(path), rows=rows, note=note
    )
