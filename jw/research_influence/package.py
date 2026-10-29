"""The output bundle: tables a reader can sort, numbers they can re-derive.

Every file is written once, then hashed into ``manifest.json``.  The manifest is
written last and lists every other artefact, so a modified CSV is detectable
without trusting this code.
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cohort import CohortSnapshot, citations_within
from .contracts import PaperRecord, content_hash, file_digest, utc_now
from .report import build_report

__all__ = [
    "PACKAGE_SCHEMA",
    "PackageResult",
    "paper_rows",
    "provenance_rows",
    "write_package",
]

PACKAGE_SCHEMA = "jw.research-influence.package/v1"

PAPER_COLUMNS = (
    "paper_id",
    "title",
    "year",
    "doi",
    "venue",
    "venue_type",
    "publisher",
    "language",
    "author_count",
    "country_count",
    "reference_count",
    "cited_by_count",
    "cited_in_window",
    "access_route",
    "has_full_text",
    "fulltext_pages",
    "open_data_count",
    "missing_fields",
    "coverage",
)

PREDICTION_COLUMNS = (
    "paper_id",
    "title",
    "year",
    "observed_in_window",
    "predicted_low",
    "predicted_median",
    "predicted_high",
    "interval_width",
    "window_status",
    "cohort_percentile",
    "is_subject",
)

FACTOR_COLUMNS = (
    "paper_id",
    "feature",
    "label",
    "observed",
    "reference",
    "delta_counts",
    "ci_low",
    "ci_high",
    "direction",
    "share",
    "note",
)

COMPARATOR_COLUMNS = (
    "paper_id",
    "comparator_id",
    "title",
    "year",
    "venue",
    "observed_citations",
    "predicted_median",
    "distance",
)

BIAS_COLUMNS = (
    "audit",
    "label",
    "status",
    "severity",
    "method",
    "evidence",
    "conclusion",
    "recommendation",
)


@dataclass(slots=True)
class PackageResult:
    """What was written, and the digest of each file."""

    directory: str
    files: Mapping[str, str] = field(default_factory=dict)
    digests: Mapping[str, str] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)
    report_path: str = ""
    manifest_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "directory": self.directory,
            "files": dict(self.files),
            "digests": dict(self.digests),
            "counts": dict(self.counts),
            "report_path": self.report_path,
            "manifest_path": self.manifest_path,
        }


def paper_rows(
    papers: Sequence[PaperRecord], *, reference_year: int, horizon: int
) -> list[dict[str, Any]]:
    """One row per paper, with the window count next to the lifetime count."""
    rows: list[dict[str, Any]] = []
    for paper in papers:
        rows.append(
            {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "year": paper.year or "",
                "doi": paper.doi,
                "venue": paper.venue.name if paper.venue else "",
                "venue_type": paper.venue.venue_type if paper.venue else "",
                "publisher": paper.venue.publisher if paper.venue else "",
                "language": paper.language,
                "author_count": paper.author_count,
                "country_count": len(
                    {author.country for author in paper.authors if author.country}
                ),
                "reference_count": paper.reference_count or len(paper.references),
                "cited_by_count": paper.cited_by_count
                if paper.cited_by_count is not None
                else "",
                "cited_in_window": _or_blank(citations_within(paper, horizon=horizon)),
                "access_route": str(paper.access_route),
                "has_full_text": paper.has_full_text,
                "fulltext_pages": paper.fulltext_pages,
                "open_data_count": len(paper.open_data),
                "missing_fields": " ".join(paper.missing_fields()),
                "coverage": f"{paper.coverage():.3f}",
            }
        )
    return rows


def provenance_rows(papers: Sequence[PaperRecord]) -> list[dict[str, Any]]:
    """Field-level provenance: one row per (paper, field, source)."""
    rows: list[dict[str, Any]] = []
    for paper in papers:
        for name, entries in sorted(paper.provenance.items()):
            for entry in entries:
                locator = entry.locator
                rows.append(
                    {
                        "paper_id": paper.paper_id,
                        "field": name,
                        "provider": entry.provider,
                        "method": entry.method,
                        "url": entry.url,
                        "retrieved_at": entry.retrieved_at,
                        "sha256": entry.sha256,
                        "license": entry.license,
                        "confidence": str(entry.confidence),
                        "page": locator.page if locator else "",
                        "section": locator.section if locator else "",
                        "note": entry.note,
                    }
                )
    return rows


def write_package(
    *,
    directory: Path | str,
    run_id: str,
    reference_year: int,
    horizon: int,
    papers: Sequence[PaperRecord],
    snapshot: CohortSnapshot | None = None,
    predictions: Any = None,
    model: Any = None,
    importances: Sequence[Any] = (),
    explanations: Sequence[Any] = (),
    bias_report: Any = None,
    metrics: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
    started_at: str = "",
    finished_at: str = "",
    wall_seconds: float | None = None,
    subject_ids: Sequence[str] = (),
) -> PackageResult:
    """Write the bundle and hash it."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    figures_dir = root / "figure_overlays"  # reserved, mirrors scientific_data
    del figures_dir

    metrics = dict(metrics or {})
    subject_set = set(subject_ids)
    files: dict[str, str] = {}

    papers_table = paper_rows(papers, reference_year=reference_year, horizon=horizon)
    _write_csv(root / "papers.csv", PAPER_COLUMNS, papers_table)
    files["papers"] = "papers.csv"

    if predictions is not None:
        by_id = {paper.paper_id: paper for paper in papers}
        # Where each paper sits in the reference population.  This is a rank of
        # the prediction, not of the truth: it says "this paper looks like the
        # top decile of this cohort", which is what a reader can act on.
        medians = sorted(predictions.median)
        rows = []
        for index, paper_id in enumerate(predictions.paper_ids):
            paper = by_id.get(paper_id)
            rows.append(
                {
                    "paper_id": paper_id,
                    "title": paper.title if paper else "",
                    "year": paper.year if paper else "",
                    "observed_in_window": (
                        _or_blank(citations_within(paper, horizon=horizon))
                        if paper
                        else ""
                    ),
                    "predicted_low": _round(predictions.low[index]),
                    "predicted_median": _round(predictions.median[index]),
                    "predicted_high": _round(predictions.high[index]),
                    "interval_width": _round(
                        predictions.high[index] - predictions.low[index]
                    ),
                    "window_status": (
                        _window_status(paper, reference_year, horizon) if paper else ""
                    ),
                    "cohort_percentile": _percentile(
                        medians, predictions.median[index]
                    ),
                    "is_subject": paper_id in subject_set,
                }
            )
        _write_csv(root / "predictions.csv", PREDICTION_COLUMNS, rows)
        files["predictions"] = "predictions.csv"

    factor_rows: list[dict[str, Any]] = []
    comparator_rows: list[dict[str, Any]] = []
    for explanation in explanations:
        for effect in explanation.factors:
            factor_rows.append(
                {
                    "paper_id": explanation.paper_id,
                    "feature": effect.feature,
                    "label": effect.label,
                    "observed": ""
                    if effect.observed is None
                    else _round(effect.observed, 6),
                    "reference": _round(effect.reference, 6),
                    "delta_counts": _round(effect.delta_counts),
                    "ci_low": _round(effect.ci_low),
                    "ci_high": _round(effect.ci_high),
                    "direction": effect.direction,
                    "share": _round(effect.share, 4),
                    "note": effect.note,
                }
            )
        for item in explanation.comparators:
            comparator_rows.append(
                {
                    "paper_id": explanation.paper_id,
                    "comparator_id": item.paper_id,
                    "title": item.title,
                    "year": item.year or "",
                    "venue": item.venue,
                    "observed_citations": item.observed_citations,
                    "predicted_median": _round(item.predicted_median),
                    "distance": _round(item.distance, 6),
                }
            )
    if factor_rows:
        _write_csv(root / "factors.csv", FACTOR_COLUMNS, factor_rows)
        files["factors"] = "factors.csv"
    if comparator_rows:
        _write_csv(root / "comparators.csv", COMPARATOR_COLUMNS, comparator_rows)
        files["comparators"] = "comparators.csv"

    importance_rows = [
        {
            "rank": item.rank,
            "feature": item.feature,
            "label": item.label,
            "mean_drop": _round(item.mean_drop, 4),
            "sd_drop": _round(item.sd_drop, 4),
            "ci_low": _round(item.ci_low, 4),
            "ci_high": _round(item.ci_high, 4),
            "supported": item.supported,
        }
        for item in importances
    ]
    if importance_rows:
        _write_csv(
            root / "importance.csv",
            (
                "rank",
                "feature",
                "label",
                "mean_drop",
                "sd_drop",
                "ci_low",
                "ci_high",
                "supported",
            ),
            importance_rows,
        )
        files["importance"] = "importance.csv"

    if bias_report is not None:
        _write_csv(
            root / "bias_audit.csv",
            BIAS_COLUMNS,
            [
                {
                    "audit": finding.name,
                    "label": finding.label,
                    "status": finding.status,
                    "severity": finding.severity,
                    "method": finding.method,
                    "evidence": json.dumps(
                        _jsonable(finding.evidence), ensure_ascii=False, sort_keys=True
                    ),
                    "conclusion": finding.conclusion,
                    "recommendation": finding.recommendation,
                }
                for finding in bias_report.findings
            ],
        )
        files["bias_audit"] = "bias_audit.csv"
        (root / "bias_audit.md").write_text(
            "\n".join(bias_report.summary_lines()) + "\n", encoding="utf-8"
        )
        files["bias_summary"] = "bias_audit.md"

    card = model.card if model is not None else None
    if card is not None:
        _write_json(root / "model_card.json", card.to_dict())
        files["model_card"] = "model_card.json"

    _write_json(root / "metrics.json", metrics)
    files["metrics"] = "metrics.json"

    provenance = provenance_rows(papers)
    _write_jsonl(root / "provenance.jsonl", provenance)
    files["provenance"] = "provenance.jsonl"

    planned = dict(files)
    planned["report"] = "report.md"
    planned["readme"] = "README.md"
    planned["manifest"] = "manifest.json"

    report_text = build_report(
        run_id=run_id,
        reference_year=reference_year,
        horizon=horizon,
        cohort_summary=_cohort_summary(snapshot, papers, subject_ids),
        metrics=metrics,
        model_card=card.to_dict() if card is not None else None,
        importances=importances,
        explanations=explanations,
        bias_report=bias_report,
        provenance_summary=_provenance_summary(papers),
        notes=notes,
        files=planned,
        started_at=started_at,
        finished_at=finished_at or utc_now(),
        wall_seconds=wall_seconds,
        fingerprint=metrics.get("fingerprint", ""),
    )
    (root / "report.md").write_text(report_text, encoding="utf-8")

    (root / "README.md").write_text(
        _readme(run_id=run_id, files=planned, model=model is not None),
        encoding="utf-8",
    )

    counts = {
        "papers": len(papers),
        "predictions": len(predictions) if predictions is not None else 0,
        "explanations": len(explanations),
        "factors": len(factor_rows),
        "comparators": len(comparator_rows),
        "importance": len(importance_rows),
        "audits": len(bias_report.findings) if bias_report is not None else 0,
        "provenance": len(provenance),
    }
    digests: dict[str, str] = {}
    for role, name in planned.items():
        if role == "manifest":
            continue
        digests[role] = file_digest(root / name)

    manifest = {
        "schema": PACKAGE_SCHEMA,
        "run_id": run_id,
        "generated_at": utc_now(),
        "reference_year": reference_year,
        "horizon_years": horizon,
        "cohort_fingerprint": snapshot.fingerprint() if snapshot is not None else "",
        "counts": counts,
        "metrics": metrics,
        "model_fingerprint": card.fingerprint() if card is not None else "",
        "artifacts": [
            {"role": role, "path": planned[role], "sha256": digest}
            for role, digest in sorted(digests.items())
        ],
        "notes": list(notes),
        "files": planned,
    }
    _write_json(root / "manifest.json", manifest)
    digests["manifest"] = file_digest(root / "manifest.json")

    return PackageResult(
        directory=str(root),
        files=planned,
        digests=digests,
        counts=counts,
        report_path=str(root / "report.md"),
        manifest_path=str(root / "manifest.json"),
    )


def _cohort_summary(
    snapshot: CohortSnapshot | None,
    papers: Sequence[PaperRecord],
    subject_ids: Sequence[str],
) -> dict[str, Any]:
    summary = dict(snapshot.summary()) if snapshot is not None else {}
    labelled = 0
    censored = 0
    for paper in papers:
        if paper.cited_by_count is None:
            continue
        labelled += 1
        if _censored(paper, summary.get("reference_year")):
            censored += 1
    summary.setdefault("papers", len(papers))
    summary["labelled"] = labelled - censored
    summary["censored"] = censored
    summary["subjects"] = len(subject_ids)
    languages: dict[str, int] = {}
    for paper in papers:
        if paper.language:
            languages[paper.language] = languages.get(paper.language, 0) + 1
    summary["languages"] = languages
    return summary


def _censored(paper: PaperRecord, reference_year: Any) -> bool:
    from .cohort import is_right_censored

    year = reference_year if isinstance(reference_year, int) else None
    if not year:
        return False
    return is_right_censored(paper, reference_year=year, horizon=3)


def _percentile(sorted_values: Sequence[float], value: float) -> float:
    """Share of the reference population at or below ``value``, in percent."""
    if not sorted_values:
        return 0.0
    below = bisect.bisect_right(sorted_values, value)
    return round(100.0 * below / len(sorted_values), 1)


def _or_blank(value: Any) -> Any:
    return "" if value is None else value


def _window_status(paper: PaperRecord, reference_year: int, horizon: int) -> str:
    from .cohort import is_right_censored

    if paper.cited_by_count is None:
        return "unlabelled"
    if is_right_censored(paper, reference_year=reference_year, horizon=horizon):
        return "censored"
    return "closed"


def _provenance_summary(papers: Sequence[PaperRecord]) -> dict[str, Any]:
    routes: dict[str, int] = {}

    def count(predicate: Any) -> int:
        return sum(1 for paper in papers if predicate(paper))

    for paper in papers:
        route = str(paper.access_route)
        routes[route] = routes.get(route, 0) + 1
    coverage: dict[str, float] = {}
    for name in ("title", "abstract", "year", "doi", "venue", "authors", "references"):
        present = sum(1 for paper in papers if paper.provenance_for(name))
        coverage[name] = round(present / len(papers), 4) if papers else 0.0
    return {
        "with_title": count(lambda paper: bool(paper.title)),
        "with_abstract": count(lambda paper: bool(paper.abstract)),
        "with_year": count(lambda paper: bool(paper.year)),
        "with_authors": count(lambda paper: bool(paper.authors)),
        # An absent venue arrives as an empty VenueRecord, not as None, so
        # "is not None" counted every metadata-only paper as having a journal.
        "with_venue": count(lambda paper: bool(paper.venue and paper.venue.name)),
        "with_references": count(lambda paper: bool(paper.references)),
        "with_open_data": count(lambda paper: bool(paper.open_data)),
        "with_full_text": count(lambda paper: paper.has_full_text),
        "routes": routes,
        "field_coverage": coverage,
    }


def _readme(*, run_id: str, files: Mapping[str, str], model: bool) -> str:
    lines = [
        "# 科研影响力分析交付包",
        "",
        f"run_id: `{run_id}`",
        "",
        "本目录是一次运行的完整产物。所有数字都能从这些文件重新算出来，"
        "没有存在于报告文本里却没有出处的结论。",
        "",
        "## 文件",
        "",
    ]
    descriptions = {
        "papers": "每篇论文一行：来源可得性、获取路线、窗口内被引、缺失字段",
        "predictions": "预测区间（低/中/高）与区间宽度；`is_subject=1` 是本次要分析的目标论文",
        "factors": "每个因素一条：观测值、队列参考值、Δ（被引）、95% CI、方向、占比",
        "comparators": "特征空间最近邻对照论文",
        "importance": "置换重要性排名与置信区间",
        "bias_audit": "十类偏差审计的结论、证据与建议",
        "bias_summary": "偏差审计的人读版本",
        "model_card": "模型卡：特征、超参数、种子、指纹",
        "metrics": "指标（Spearman、Top-10% lift、区间覆盖率等）",
        "provenance": "字段级溯源：每行一条 (论文, 字段, 来源)",
        "report": "完整报告（中文，含解释读法与局限）",
        "manifest": "产物清单与每个文件的 sha256",
        "readme": "本文件",
    }
    for role in sorted(files):
        if role == "readme":
            continue
        lines.append(f"- `{files[role]}` — {descriptions.get(role, '')}")
    lines.append("")
    if model:
        lines.append(
            "预测是三年窗口内的被引次数区间（80%），不是单一分数；"
            "区间宽度是输出的一部分。"
        )
    else:
        lines.append(
            "本次运行未训练模型（带标签样本不足），因此没有 predictions/model_card；"
            "解析与可得性结果仍然完整。"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_csv(
    path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: _cell(row.get(name, "")) for name in columns})
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(_jsonable(dict(row)), ensure_ascii=False, sort_keys=True)
                + "\n"
            )


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return f"{value:.6g}"
    return str(value)


def _round(value: Any, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return f"{value:.{digits}f}"
    return str(value)


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {str(key): _jsonable(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_jsonable(value) for value in payload]
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    if hasattr(payload, "to_dict"):
        return _jsonable(payload.to_dict())
    if hasattr(payload, "value"):
        return _jsonable(payload.value)
    return str(payload)


def manifest_digest(directory: Path | str) -> str:
    """Digest over the manifest itself, for cross-run comparison."""
    return file_digest(Path(directory) / "manifest.json")


def package_fingerprint(result: PackageResult) -> str:
    """Deterministic hash over the artefact digests (manifest excluded)."""
    return content_hash(
        {
            role: digest
            for role, digest in sorted(result.digests.items())
            if role != "manifest"
        }
    )
