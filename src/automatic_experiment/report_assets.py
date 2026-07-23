"""Deterministic report assets generated only from verified numeric evidence."""

from __future__ import annotations

import csv
import html
import math
import re
from pathlib import Path
from typing import Any

from .state import atomic_write_text, file_sha256


class ReportAssetError(RuntimeError):
    """A report-qualified asset could not be generated without weakening evidence."""


COLORS = {
    "target": "#222222",
    "baseline": "#D55E00",
    "candidate": "#0072B2",
    "sensitivity": "#009E73",
    "sensitivity_secondary": "#CC79A7",
    "grid": "#D9D9D9",
    "text": "#222222",
}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _finite(value: object, label: str) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ReportAssetError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise ReportAssetError(f"{label} is not finite")
    return result


def _display_name(name: str, plan: dict[str, dict[str, Any]]) -> str:
    if name in plan:
        return str(plan[name]["display_name"])
    lowered = name.lower()
    if "without" in lowered and ("suspect" in lowered or "flag" in lowered):
        return "排除标记行后模型的平均绝对误差"
    if "with" in lowered and ("suspect" in lowered or "flag" in lowered):
        return "包含标记行模型的平均绝对误差"
    if "raw" in lowered and "mae" in lowered:
        return "原始读数平均绝对误差"
    if "calibrated" in lowered and "mae" in lowered:
        return "校准预测平均绝对误差"
    return "已核验测量"


def _condition_label(value: object) -> str:
    text = str(value).strip()
    fit_excluded = re.search(
        r"排除[^，。；;]{0,16}(?:标记|可疑)[^，。；;]{0,12}拟合",
        text,
    ) is not None
    fit_included = re.search(
        r"(?:包含|保留)[^，。；;]{0,16}(?:标记|可疑)[^，。；;]{0,12}拟合",
        text,
    ) is not None
    excluded = fit_excluded or (
        not fit_included
        and "排除" in text
        and ("标记" in text or "可疑" in text)
    )
    included = fit_included or (
        not fit_excluded
        and ("包含" in text or "保留" in text)
        and ("标记" in text or "可疑" in text)
    )
    scope = "排除标记观测" if excluded else "包含被标记观测" if included else ""
    uncalibrated = any(token in text for token in ("未校正", "未经校正", "未校准", "未经校准"))
    calibrated = ("校准" in text or "校正" in text) and not uncalibrated
    raw = "原始" in text and "误差" in text
    if scope:
        if uncalibrated:
            return f"{scope}时未校准"
        if calibrated:
            return f"{scope}时校准后"
        if raw:
            return f"{scope}时原始读数"
        return scope
    if "条件" in text:
        return text.split("条件", 1)[0] + "条件"
    if "未校正" in text or "未经校正" in text:
        return "未校正读数"
    if "未校准" in text or "未经校准" in text:
        return "未校准读数"
    if "原始" in text and "误差" in text:
        return "原始读数"
    if ("校准" in text or "校正" in text) and "误差" in text:
        return "校准后"
    for metric_name in ("平均绝对误差", "均方根误差", "平均误差"):
        if metric_name in text:
            prefix = text.split(metric_name, 1)[0].rstrip("的 ")
            if prefix:
                return prefix
    return text


def _public_artifact(
    run_root: Path,
    record: dict[str, Any],
    relative: str,
) -> Path:
    matches = [
        row
        for row in record.get("public_artifacts", [])
        if row.get("path") == f"public/{relative}"
        or str(row.get("path", "")).endswith(f"/{relative}")
    ]
    if len(matches) != 1:
        raise ReportAssetError(
            f"verified evidence artifact cannot be resolved uniquely: {relative}"
        )
    path = run_root / Path(*str(matches[0]["path"]).split("/"))
    if not path.is_file() or file_sha256(path) != matches[0].get("sha256"):
        raise ReportAssetError(
            f"verified evidence artifact changed after verification: {relative}"
        )
    return path


def _read_evidence(
    path: Path,
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            required = {
                audit["evidence_row_id_column"],
                audit["evidence_target_column"],
                audit["evidence_baseline_column"],
                audit["evidence_candidate_column"],
            }
            if not required.issubset(columns):
                raise ReportAssetError(
                    f"verified evidence is missing columns: {sorted(required - columns)}"
                )
            rows: list[dict[str, Any]] = []
            for index, row in enumerate(reader, start=2):
                row_id = str(row[audit["evidence_row_id_column"]]).strip()
                if not row_id:
                    raise ReportAssetError(f"evidence row {index} has an empty id")
                rows.append(
                    {
                        "row_id": row_id,
                        "target": _finite(
                            row[audit["evidence_target_column"]],
                            f"target at row {index}",
                        ),
                        "baseline": _finite(
                            row[audit["evidence_baseline_column"]],
                            f"baseline at row {index}",
                        ),
                        "candidate": _finite(
                            row[audit["evidence_candidate_column"]],
                            f"candidate at row {index}",
                        ),
                    }
                )
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ReportAssetError("verified comparison CSV is not readable") from exc
    if not rows:
        raise ReportAssetError("verified comparison CSV contains no rows")
    if len({row["row_id"] for row in rows}) != len(rows):
        raise ReportAssetError("verified comparison row ids are not unique")
    return rows


def _metric(metric: str, values: list[float], targets: list[float]) -> float:
    errors = [
        value - target for value, target in zip(values, targets, strict=True)
    ]
    if metric == "mae":
        return sum(abs(error) for error in errors) / len(errors)
    if metric == "rmse":
        return math.sqrt(sum(error * error for error in errors) / len(errors))
    if metric == "mean_signed_error":
        return sum(errors) / len(errors)
    raise ReportAssetError(f"unsupported verified comparison metric: {metric}")


def _svg(
    rows: list[dict[str, Any]],
    bars: list[tuple[str, float, str]],
    unit: str,
    scope: str,
    series_labels: tuple[str, str, str],
) -> str:
    width = 1040
    height = 650 if len(rows) <= 200 else 360
    margin_left = 90
    plot_right = 990
    plot_width = plot_right - margin_left
    bar_labels = "、".join(label for label, _, _ in bars)
    unit_label = str(unit).strip()
    if unit_label in {"", "与数值列相同单位", "与原始数值相同单位"}:
        unit_label = "原始数据未注明"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="title desc">'
        ),
        "<title id=\"title\">观测结果与误差指标比较</title>",
        (
            "<desc id=\"desc\">"
            + _escape(
                f"{scope}。图中展示各观测的参考值与比较结果；汇总误差指标包括"
                f"{bar_labels}。单位：{unit_label}。"
            )
            + "</desc>"
        ),
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        (
            '<text x="520" y="34" text-anchor="middle" '
            'font-family="Arial, Microsoft YaHei, sans-serif" font-size="21" '
            'font-weight="700" fill="#222222">观测结果与误差指标比较</text>'
        ),
    ]
    if len(rows) <= 200:
        top = 72
        bottom = 355
        values = [
            value
            for row in rows
            for value in (row["target"], row["baseline"], row["candidate"])
        ]
        low = min(values)
        high = max(values)
        padding = max((high - low) * 0.12, 0.05 if high == low else 1e-9)
        low -= padding
        high += padding

        def x(index: int) -> float:
            if len(rows) == 1:
                return margin_left + plot_width / 2
            return margin_left + index * plot_width / (len(rows) - 1)

        def y(value: float) -> float:
            return bottom - (value - low) * (bottom - top) / (high - low)

        for tick in range(5):
            value = low + tick * (high - low) / 4
            y_pos = y(value)
            lines.extend(
                [
                    (
                        f'<line x1="{margin_left}" y1="{y_pos:.3f}" '
                        f'x2="{plot_right}" y2="{y_pos:.3f}" '
                        f'stroke="{COLORS["grid"]}" stroke-width="1"/>'
                    ),
                    (
                        f'<text x="{margin_left - 12}" y="{y_pos + 5:.3f}" '
                        'text-anchor="end" font-family="Arial, Microsoft YaHei, sans-serif" '
                        f'font-size="12" fill="{COLORS["text"]}">{value:.4g}</text>'
                    ),
                ]
            )
        series = [
            ("target", series_labels[0], COLORS["target"]),
            ("baseline", series_labels[1], COLORS["baseline"]),
            ("candidate", series_labels[2], COLORS["candidate"]),
        ]
        for key, _, color in series:
            points = " ".join(
                f"{x(index):.3f},{y(row[key]):.3f}"
                for index, row in enumerate(rows)
            )
            lines.append(
                f'<polyline points="{points}" fill="none" stroke="{color}" '
                'stroke-width="2.2" stroke-linejoin="round"/>'
            )
            for index, row in enumerate(rows):
                lines.append(
                    f'<circle cx="{x(index):.3f}" cy="{y(row[key]):.3f}" '
                    f'r="3.5" fill="{color}"/>'
                )
        label_stride = max(1, math.ceil(len(rows) / 12))
        for index, row in enumerate(rows):
            if index % label_stride != 0 and index != len(rows) - 1:
                continue
            lines.append(
                f'<text x="{x(index):.3f}" y="{bottom + 23}" text-anchor="middle" '
                'font-family="Arial, Microsoft YaHei, sans-serif" font-size="11" '
                f'fill="{COLORS["text"]}">{_escape(row["row_id"])}</text>'
            )
        lines.append(
            f'<text x="24" y="{(top + bottom) / 2:.3f}" text-anchor="middle" '
            f'transform="rotate(-90 24 {(top + bottom) / 2:.3f})" '
            'font-family="Arial, Microsoft YaHei, sans-serif" font-size="13" '
            f'fill="{COLORS["text"]}">数值（{_escape(unit or "无量纲")}）</text>'
        )
        legend_x = 650
        for offset, (_, label, color) in enumerate(series):
            y_pos = 54 + offset * 18
            lines.append(
                f'<line x1="{legend_x}" y1="{y_pos}" x2="{legend_x + 26}" '
                f'y2="{y_pos}" stroke="{color}" stroke-width="3"/>'
            )
            lines.append(
                f'<text x="{legend_x + 34}" y="{y_pos + 4}" '
                'font-family="Arial, Microsoft YaHei, sans-serif" font-size="12" '
                f'fill="{COLORS["text"]}">{_escape(label)}</text>'
            )
        bar_top = 425
    else:
        lines.append(
            '<text x="520" y="72" text-anchor="middle" '
            'font-family="Arial, Microsoft YaHei, sans-serif" font-size="13" '
            'fill="#555555">数据超过 200 条，折线从略；柱形图使用全部观测计算的汇总指标。</text>'
        )
        bar_top = 105

    if bars:
        maximum = max(value for _, value, _ in bars)
        maximum = maximum if maximum > 0 else 1.0
        bar_bottom = height - 70
        available = plot_width / len(bars)
        bar_width = min(180, available * 0.58)
        for index, (label, value, color) in enumerate(bars):
            center = margin_left + available * (index + 0.5)
            bar_height = (bar_bottom - bar_top) * value / maximum
            y_pos = bar_bottom - bar_height
            lines.extend(
                [
                    (
                        f'<rect x="{center - bar_width / 2:.3f}" y="{y_pos:.3f}" '
                        f'width="{bar_width:.3f}" height="{bar_height:.3f}" '
                        f'fill="{color}"/>'
                    ),
                    (
                        f'<text x="{center:.3f}" y="{y_pos - 8:.3f}" text-anchor="middle" '
                        'font-family="Arial, Microsoft YaHei, sans-serif" font-size="13" '
                        f'fill="{COLORS["text"]}">{value:.6g} {_escape(unit)}</text>'
                    ),
                    (
                        f'<text x="{center:.3f}" y="{bar_bottom + 22}" text-anchor="middle" '
                        'font-family="Arial, Microsoft YaHei, sans-serif" font-size="12" '
                        f'fill="{COLORS["text"]}">{_escape(label)}</text>'
                    ),
                ]
            )
        lines.append(
            f'<line x1="{margin_left}" y1="{bar_bottom}" x2="{plot_right}" '
            f'y2="{bar_bottom}" stroke="{COLORS["text"]}" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="24" y="{(bar_top + bar_bottom) / 2:.3f}" text-anchor="middle" '
            f'transform="rotate(-90 24 {(bar_top + bar_bottom) / 2:.3f})" '
            'font-family="Arial, Microsoft YaHei, sans-serif" font-size="13" '
            f'fill="{COLORS["text"]}">误差指标（{_escape(unit or "无量纲")}）</text>'
        )
    lines.append(
        f'<text x="520" y="{height - 20}" text-anchor="middle" '
        'font-family="Arial, Microsoft YaHei, sans-serif" font-size="11" '
        'fill="#555555">折线展示各观测结果，柱形展示同一评价范围内的误差指标。</text>'
    )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def generate_report_assets(
    run_root: Path,
    record: dict[str, Any],
    design: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paired = (record.get("evidence_ledger") or {}).get("paired_comparisons") or []
    if not paired or design is None:
        return [], {
            "status": "not_applicable",
            "reason": "本次终态没有适合绘制的已核验成对数值证据。",
        }
    primary = next(
        (
            row
            for row in paired
            if row.get("comparison_kind") == "source_baseline_vs_candidate"
        ),
        None,
    )
    if primary is None:
        return [], {
            "status": "not_applicable",
            "reason": "没有原始基线与候选方案的已核验成对比较。",
        }
    audits = {
        row["id"]: row for row in design.get("paired_comparison_audits", [])
    }
    audit = audits.get(primary["id"])
    if audit is None:
        raise ReportAssetError("verified comparison has no matching validated design")
    evidence_path = _public_artifact(
        run_root,
        record,
        primary["evidence_artifact"],
    )
    rows = _read_evidence(evidence_path, audit)
    if len(rows) != primary["row_count"]:
        raise ReportAssetError("verified comparison row count changed before plotting")
    recorded_ids = primary.get("row_ids")
    if recorded_ids is not None and recorded_ids != [
        row["row_id"] for row in rows
    ]:
        raise ReportAssetError("verified comparison row identities changed before plotting")
    targets = [row["target"] for row in rows]
    baselines = [row["baseline"] for row in rows]
    candidates = [row["candidate"] for row in rows]
    recomputed_baseline = _metric(primary["metric"], baselines, targets)
    recomputed_candidate = _metric(primary["metric"], candidates, targets)
    recomputed = primary["recomputed_measurements"]
    baseline_name = audit["baseline_measurement"]
    candidate_name = audit["candidate_measurement"]
    for name, observed in (
        (baseline_name, recomputed_baseline),
        (candidate_name, recomputed_candidate),
    ):
        expected = float(recomputed[name])
        if not math.isclose(
            observed,
            expected,
            rel_tol=1e-7,
            abs_tol=max(5e-8, abs(expected) * 1e-7),
        ):
            raise ReportAssetError(
                f"verified comparison metric changed before plotting: {name}"
            )
    plan = {
        row["name"]: row for row in design.get("measurement_plan", [])
    }

    def measurement_condition(name: str, fit_condition: object = None) -> str:
        label = _condition_label(_display_name(name, plan))
        fit = str(fit_condition or "").casefold()
        if re.search(r"exclude|without|drop|排除|剔除", fit):
            return "排除标记观测时校准后"
        if re.search(r"include|with|retain|all|保留|包含|全部", fit):
            return "包含被标记观测时校准后"
        return label

    worker_by_name = {
        row["name"]: row for row in (record.get("worker_result") or {}).get(
            "measurements", []
        )
    }
    unit = str(
        (plan.get(candidate_name) or worker_by_name.get(candidate_name) or {}).get(
            "unit", ""
        )
    )
    bars: list[tuple[str, float, str]] = [
        (
            measurement_condition(
                baseline_name,
                audit.get("baseline_fit_condition"),
            ),
            recomputed_baseline,
            COLORS["baseline"],
        ),
        (
            measurement_condition(
                candidate_name,
                audit.get("candidate_fit_condition"),
            ),
            recomputed_candidate,
            COLORS["candidate"],
        ),
    ]
    sensitivity = next(
        (
            row
            for row in paired
            if row.get("comparison_kind") == "candidate_vs_candidate"
            and row.get("row_count") == primary.get("row_count")
            and row.get("row_ids", [row_item["row_id"] for row_item in rows])
            == [row_item["row_id"] for row_item in rows]
            and row.get("metric") == primary.get("metric")
        ),
        None,
    )
    if sensitivity is not None:
        sensitivity_audit = audits.get(sensitivity["id"])
        if sensitivity_audit is not None:
            existing_names = {baseline_name, candidate_name}
            additional_names = [
                name
                for name in (
                    sensitivity_audit["baseline_measurement"],
                    sensitivity_audit["candidate_measurement"],
                )
                if name not in existing_names
            ]
            if additional_names:
                colors = (
                    COLORS["sensitivity"],
                    COLORS["sensitivity_secondary"],
                )
                for name, color in zip(additional_names, colors, strict=False):
                    value = float(sensitivity["recomputed_measurements"][name])
                    fit_by_name = {
                        sensitivity_audit["baseline_measurement"]: sensitivity_audit.get(
                            "baseline_fit_condition"
                        ),
                        sensitivity_audit["candidate_measurement"]: sensitivity_audit.get(
                            "candidate_fit_condition"
                        ),
                    }
                    bars.append(
                        (
                            measurement_condition(name, fit_by_name.get(name)),
                            value,
                            color,
                        )
                    )
    distinct_bars: list[tuple[str, float, str]] = []
    for label, value, color in bars:
        if any(
            label == prior_label
            and math.isclose(value, prior_value, rel_tol=1e-9, abs_tol=1e-12)
            for prior_label, prior_value, _prior_color in distinct_bars
        ):
            continue
        distinct_bars.append((label, value, color))
    bars = distinct_bars
    baseline_condition = measurement_condition(
        baseline_name,
        audit.get("baseline_fit_condition"),
    )
    candidate_condition = measurement_condition(
        candidate_name,
        audit.get("candidate_fit_condition"),
    )

    def series_label(condition: str) -> str:
        if condition.endswith(("预测", "读数", "估计", "结果")):
            return condition
        if "未校准" in condition or "未校正" in condition:
            return f"{condition}读数"
        if "校准" in condition or "校正" in condition:
            return f"{condition}预测"
        return f"{condition}结果"

    svg = _svg(
        rows,
        bars,
        unit,
        str(primary["evaluation_scope"]),
        ("参考值", series_label(baseline_condition), series_label(candidate_condition)),
    )
    relative = "report_assets/verified-comparison.svg"
    path = run_root / "report_assets" / "verified-comparison.svg"
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReportAssetError(
                "existing report asset is not readable during finalization recovery"
            ) from exc
        if existing != svg:
            raise ReportAssetError(
                "existing report asset differs from deterministically regenerated evidence"
            )
    else:
        atomic_write_text(path, svg)
    asset = {
        "path": relative,
        "kind": "svg",
        "description": "观测结果与误差指标比较图",
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
        "row_handling": "all_rows" if len(rows) <= 200 else "full_data_aggregate",
        "row_count": len(rows),
    }
    return [asset], {
        "status": "generated",
        "reason": "具备可复核的成对数值结果。",
        "assets": [asset],
    }
