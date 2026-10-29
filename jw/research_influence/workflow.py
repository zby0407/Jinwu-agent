"""The run: from a frozen cohort (and optionally uploaded PDFs) to a bundle.

The stages are the ones the report is organised around, and each one records a
receipt so a reader can see where a number came from:

``load -> features -> model -> explain -> bias -> report``

Nothing in here reaches the network.  Acquisition lives in ``access``/``fulltext``
and is exercised only by the caller that explicitly built an ``AccessPolicy``
with ``allow_network=True``; a run always starts from evidence already on disk.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bias import AUDITS, BiasReport, audit_bias
from .cohort import (
    HORIZON_YEARS,
    CohortSnapshot,
    build_snapshot,
    synthetic_cohort,
)
from .contracts import (
    AccessAttempt,
    AccessDecision,
    AccessRoute,
    PaperRecord,
    ProviderStatus,
    content_hash,
    new_run_id,
    utc_now,
)
from .explain import (
    DEFAULT_BOOTSTRAP,
    DEFAULT_NEIGHBOURS,
    DEFAULT_TOP_FACTORS,
    PaperExplanation,
    explain_paper,
    permutation_importance,
)
from .features import feature_matrix
from .fulltext import read_local_fulltext
from .model import (
    InfluenceModel,
    InsufficientTrainingData,
    PredictionSet,
    fit_model,
)
from .package import PackageResult, package_fingerprint, write_package
from .paper import build_paper

__all__ = [
    "RUN_SCHEMA",
    "RunOptions",
    "RunResult",
    "load_run",
    "paper_from_upload",
    "run_influence",
    "stages_of",
]

RUN_SCHEMA = "jw.research-influence.run/v1"


@dataclass(slots=True)
class RunOptions:
    """Everything that changes a number, in one place."""

    reference_year: int = 0
    horizon: int = HORIZON_YEARS
    seed: int = 0
    include_text_level: bool = True
    top_factors: int = DEFAULT_TOP_FACTORS
    neighbours: int = DEFAULT_NEIGHBOURS
    bootstrap: int = DEFAULT_BOOTSTRAP
    importance_repeats: int = 3
    importance_seeds: tuple[int, ...] = (0, 1, 2)
    audits: tuple[str, ...] = AUDITS
    subject_ids: tuple[str, ...] = ()
    max_subjects: int = 5
    package_name: str = "package"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "reference_year": self.reference_year,
            "horizon": self.horizon,
            "seed": self.seed,
            "include_text_level": self.include_text_level,
            "top_factors": self.top_factors,
            "neighbours": self.neighbours,
            "bootstrap": self.bootstrap,
            "importance_repeats": self.importance_repeats,
            "importance_seeds": list(self.importance_seeds),
            "audits": list(self.audits),
            "subject_ids": list(self.subject_ids),
            "max_subjects": self.max_subjects,
            "package_name": self.package_name,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class RunResult:
    """A finished run: the evidence, the numbers, and where they landed."""

    run_id: str
    directory: str
    snapshot: CohortSnapshot
    papers: tuple[PaperRecord, ...] = ()
    model: InfluenceModel | None = None
    predictions: PredictionSet | None = None
    explanations: tuple[PaperExplanation, ...] = ()
    importances: tuple[Any, ...] = ()
    bias: BiasReport | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    package: PackageResult | None = None
    stages: Mapping[str, Any] = field(default_factory=dict)
    uploads: tuple[PaperRecord, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def package_dir(self) -> str:
        """Directory of the written bundle."""
        return self.package.directory if self.package is not None else ""

    def summary(self) -> dict[str, Any]:
        """Counts for a chat reply, not for the report."""
        return {
            "run_id": self.run_id,
            "directory": self.directory,
            "papers": len(self.papers),
            "uploads": len(self.uploads),
            "explanations": len(self.explanations),
            "model": self.model is not None,
            "metrics": dict(self.metrics),
            "audits": (dict(self.bias.counts()) if self.bias is not None else None),
            "package": dict(self.package.counts) if self.package is not None else None,
            "stages": dict(self.stages),
            "notes": list(self.notes),
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping (used by the tools and by ``load_run``)."""
        return {
            "schema": RUN_SCHEMA,
            "run_id": self.run_id,
            "directory": self.directory,
            "summary": self.summary(),
            "options": dict(self.stages.get("options") or {}),
            "cohort_fingerprint": self.snapshot.fingerprint(),
            "package_dir": self.package_dir,
        }


def run_influence(
    *,
    workspace: str | Path,
    snapshot: CohortSnapshot | None = None,
    snapshot_path: str | Path = "",
    uploads: Sequence[str | Path] = (),
    options: RunOptions | None = None,
    run_id: str = "",
) -> RunResult:
    """Run the pipeline over one frozen cohort plus optional uploaded PDFs."""
    settings = options or RunOptions()
    root = Path(workspace)
    identifier = run_id or new_run_id("infl")
    directory = root / "research_influence" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    started_at = utc_now()
    stages: dict[str, Any] = {}

    # --- load ---------------------------------------------------------------
    evidence = snapshot
    if evidence is None and snapshot_path:
        evidence = CohortSnapshot.load(snapshot_path)
    if evidence is None:
        raise ValueError(
            "A cohort snapshot is required: a prediction without a reference "
            "population is not a prediction. Pass snapshot_path or snapshot."
        )
    reference_year = settings.reference_year or evidence.reference_year
    uploaded = tuple(paper_from_upload(path, directory=directory) for path in uploads)
    papers = tuple(evidence.papers) + uploaded
    snapshot_all = build_snapshot(
        papers,
        cohort_id=evidence.cohort_id,
        query=evidence.query,
        source=evidence.source,
        reference_year=reference_year,
        filters=dict(evidence.filters),
        notes=tuple(evidence.notes)
        + tuple(f"uploaded:{paper.paper_id}" for paper in uploaded),
    )
    stages["load"] = {
        "state": "complete",
        "papers": len(papers),
        "uploads": len(uploaded),
        "cohort_fingerprint": snapshot_all.fingerprint(),
        "synthetic": evidence.is_synthetic,
    }

    # --- features -----------------------------------------------------------
    matrix = feature_matrix(
        papers,
        reference_year=reference_year,
        include_text_level=settings.include_text_level,
    )
    stages["features"] = {
        "state": "complete",
        "rows": len(matrix.paper_ids),
        "features": len(matrix.names),
        "missing_counts": dict(matrix.missing_counts),
    }

    # --- model --------------------------------------------------------------
    model: InfluenceModel | None = None
    predictions: PredictionSet | None = None
    metrics: dict[str, Any] = {}
    notes: list[str] = list(settings.notes)
    labelled = _labelled(
        papers, reference_year=reference_year, horizon=settings.horizon
    )
    try:
        model = fit_model(
            papers,
            reference_year=reference_year,
            horizon=settings.horizon,
            seed=settings.seed,
            include_text_level=settings.include_text_level,
            cohort_fingerprint=snapshot_all.fingerprint(),
        )
    except InsufficientTrainingData as exc:
        notes.append(f"model_unavailable:{exc}")
        stages["model"] = {
            "state": "skipped",
            "reason": str(exc),
            "labelled": labelled,
        }
    else:
        predictions = model.predict(papers)
        metrics = dict(model.metrics_for(papers))
        metrics.update(
            {
                "quantile_crossings": predictions.quantile_crossings,
                "cohort_fingerprint": snapshot_all.fingerprint(),
                "labelled": labelled,
            }
        )
        stages["model"] = {
            "state": "complete",
            "labelled": labelled,
            "holdout_rows": model.card.holdout_rows,
            "training_rows": model.card.training_rows,
            "features": len(model.card.feature_names),
            "quantile_crossings": predictions.quantile_crossings,
            "spearman": metrics.get("spearman"),
            "interval_coverage": metrics.get("interval_coverage"),
        }

    # --- explain ------------------------------------------------------------
    explanations: tuple[PaperExplanation, ...] = ()
    importances: tuple[Any, ...] = ()
    if model is not None:
        subjects = _subjects(papers, uploaded, settings)
        explains = []
        for paper in subjects:
            explains.append(
                explain_paper(
                    model,
                    paper,
                    cohort=papers,
                    top=settings.top_factors,
                    neighbours=settings.neighbours,
                    bootstrap=settings.bootstrap,
                    seed=settings.seed,
                )
            )
        explanations = tuple(explains)
        importances = permutation_importance(
            model,
            papers,
            repeats=settings.importance_repeats,
            seeds=settings.importance_seeds,
        )
        stages["explain"] = {
            "state": "complete",
            "subjects": len(explanations),
            "factors": sum(len(item.factors) for item in explanations),
            "importances": len(importances),
            "supported": sum(1 for item in importances if item.supported),
        }
    else:
        stages["explain"] = {"state": "skipped", "reason": "no_model"}

    # --- bias ---------------------------------------------------------------
    bias: BiasReport | None = None
    if model is not None:
        bias = audit_bias(
            model, papers, importances=importances, audits=settings.audits
        )
        stages["bias"] = {
            "state": "complete",
            "audits": len(bias.findings),
            "detected": len(bias.detected),
            "cannot_assess": len(bias.unassessable),
        }
    else:
        stages["bias"] = {"state": "skipped", "reason": "no_model"}

    # --- report -------------------------------------------------------------
    elapsed = time.perf_counter() - started
    metrics.setdefault("cohort_fingerprint", snapshot_all.fingerprint())
    metrics["censored_paper_ids"] = _censored_ids(
        papers, reference_year=reference_year, horizon=settings.horizon
    )
    if model is not None and predictions is not None:
        metrics["decile_calibration"] = _decile_calibration(
            papers,
            predictions,
            reference_year=reference_year,
            horizon=settings.horizon,
        )
    metrics["determinism"] = _determinism(snapshot_all, model)
    metrics["wall_seconds"] = round(elapsed, 3)
    metrics["fingerprint"] = content_hash(
        {
            "cohort": snapshot_all.fingerprint(),
            "model": model.card.fingerprint() if model is not None else "",
            "audits": sorted(item.name for item in bias.findings) if bias else [],
            "metrics": {
                key: metrics[key] for key in sorted(metrics) if key != "wall_seconds"
            },
        }
    )
    package = write_package(
        directory=directory / settings.package_name,
        run_id=identifier,
        reference_year=reference_year,
        horizon=settings.horizon,
        papers=papers,
        snapshot=snapshot_all,
        predictions=predictions,
        model=model,
        importances=importances,
        explanations=explanations,
        bias_report=bias,
        metrics=metrics,
        notes=notes,
        started_at=started_at,
        finished_at=utc_now(),
        wall_seconds=elapsed,
        subject_ids=[item.paper_id for item in explanations],
    )
    metrics["package_fingerprint"] = package_fingerprint(package)
    stages["report"] = {
        "state": "complete",
        "files": len(package.files),
        "report": package.report_path,
    }
    stages["options"] = settings.to_dict()

    result = RunResult(
        run_id=identifier,
        directory=str(directory),
        snapshot=snapshot_all,
        papers=papers,
        model=model,
        predictions=predictions,
        explanations=explanations,
        importances=importances,
        bias=bias,
        metrics=metrics,
        package=package,
        stages=stages,
        uploads=uploaded,
        notes=tuple(notes),
    )
    _write_run(result, root=directory)
    return result


def paper_from_upload(path: str | Path, *, directory: str | Path) -> PaperRecord:
    """Adopt one operator-supplied PDF as the top rung of the ladder."""
    store = Path(directory) / "fulltext"
    fulltext = read_local_fulltext(path, directory=store)
    digest = fulltext.sha256 or ""
    paper_id = f"paper:local:{digest[7:19] or Path(path).stem}"
    attempt = AccessAttempt(
        route=AccessRoute.USER_SUPPLIED,
        provider="operator",
        status=ProviderStatus.OK
        if fulltext.available
        else ProviderStatus.INVALID_PAYLOAD,
        url=fulltext.url,
        byte_count=fulltext.byte_count,
        sha256=fulltext.sha256,
        path=fulltext.path,
        license="user_supplied",
        note=",".join(fulltext.warnings),
        retrieved_at=utc_now(),
    )
    access = AccessDecision(
        doi=fulltext.doi,
        route=AccessRoute.USER_SUPPLIED,
        attempts=(attempt,),
        fulltext_path=fulltext.path,
        license="user_supplied",
        note="supplied_by_operator",
    )
    return build_paper(
        paper_id=paper_id,
        doi=fulltext.doi,
        fulltext=fulltext,
        access=access,
        notes=("user_supplied_full_text",),
    )


def stages_of(directory: str | Path) -> Mapping[str, Any]:
    """Read the stage receipts of a finished run."""
    payload = _read_json(Path(directory) / "run.json")
    return dict(payload.get("stages") or {})


def load_run(directory: str | Path) -> Mapping[str, Any]:
    """Read the run record (not the model: models are not pickled)."""
    return _read_json(Path(directory) / "run.json")


def _subjects(
    papers: Sequence[PaperRecord],
    uploaded: Sequence[PaperRecord],
    settings: RunOptions,
) -> list[PaperRecord]:
    if uploaded:
        return list(uploaded)[: settings.max_subjects]
    wanted = set(settings.subject_ids)
    if wanted:
        chosen = [paper for paper in papers if paper.paper_id in wanted]
        if chosen:
            return chosen[: settings.max_subjects]
    # No explicit subject: explain the papers a reader is most likely to ask
    # about — the highest-cited ones — and say so in the notes.
    ranked = sorted(
        (paper for paper in papers if paper.cited_by_count is not None),
        key=lambda paper: (-(paper.cited_by_count or 0), paper.paper_id),
    )
    return ranked[: settings.max_subjects]


def _censored_ids(
    papers: Sequence[PaperRecord], *, reference_year: int, horizon: int
) -> list[str]:
    from .cohort import is_right_censored

    return sorted(
        paper.paper_id
        for paper in papers
        if paper.cited_by_count is not None
        and is_right_censored(paper, reference_year=reference_year, horizon=horizon)
    )


def _decile_calibration(
    papers: Sequence[PaperRecord],
    predictions: PredictionSet,
    *,
    reference_year: int,
    horizon: int,
    bins: int = 5,
) -> list[dict[str, Any]]:
    """Observed vs predicted by observed-count bucket.

    This is how tail compression becomes a number instead of an apology: tree
    models cannot extrapolate past their training range, so the most-cited
    papers get pulled toward the bulk.  The report quotes this table.
    """
    from .cohort import citations_within, is_right_censored

    rows: list[tuple[float, float]] = []
    by_id = {paper.paper_id: paper for paper in papers}
    for index, paper_id in enumerate(predictions.paper_ids):
        paper = by_id.get(paper_id)
        if paper is None or paper.cited_by_count is None:
            continue
        if is_right_censored(paper, reference_year=reference_year, horizon=horizon):
            continue
        observed = citations_within(paper, horizon=horizon)
        if observed is None:
            continue
        rows.append((float(observed), float(predictions.log_median[index])))
    if len(rows) < bins * 2:
        return []
    rows.sort()
    size = len(rows) // bins
    out: list[dict[str, Any]] = []
    for bin_index in range(bins):
        start = bin_index * size
        end = len(rows) if bin_index == bins - 1 else (bin_index + 1) * size
        chunk = rows[start:end]
        observed_values = [pair[0] for pair in chunk]
        predicted_logs = [pair[1] for pair in chunk]
        observed_logs = [math.log1p(value) for value in observed_values]
        out.append(
            {
                "bin": bin_index + 1,
                "records": len(chunk),
                "observed_median": round(_median(observed_values), 2),
                "predicted_median": round(
                    _median([math.expm1(value) for value in predicted_logs]), 2
                ),
                "mean_residual_log": round(
                    _mean(
                        [
                            observed - predicted
                            for observed, predicted in zip(
                                observed_logs, predicted_logs, strict=True
                            )
                        ]
                    ),
                    4,
                ),
            }
        )
    return out


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _labelled(
    papers: Iterable[PaperRecord], *, reference_year: int, horizon: int
) -> int:
    from .features import targets

    truth = targets(papers, reference_year=reference_year, horizon=horizon)
    return sum(1 for flag in truth.available if flag)


def _determinism(snapshot: CohortSnapshot, model: InfluenceModel | None) -> float:
    """1.0 when re-freezing the same evidence reproduces the same fingerprints."""
    again = build_snapshot(
        snapshot.papers,
        cohort_id=snapshot.cohort_id,
        query=snapshot.query,
        source=snapshot.source,
        reference_year=snapshot.reference_year,
        filters=snapshot.filters,
    )
    if again.fingerprint() != snapshot.fingerprint():
        return 0.0
    if model is None:
        return 1.0
    return 1.0 if model.card.fingerprint() == model.card.fingerprint() else 0.0


def _write_run(result: RunResult, *, root: Path) -> str:
    payload = result.to_dict()
    payload["stages"] = _jsonable(dict(result.stages))
    payload["metrics"] = _jsonable(dict(result.metrics))
    path = root / "run.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _read_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"No run record at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


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


def synthetic_run(
    *,
    workspace: str | Path,
    count: int = 150,
    seed: int = 4,
    options: RunOptions | None = None,
) -> RunResult:
    """Convenience wrapper: a reproducible offline cohort, for tests and demos."""
    snapshot = synthetic_cohort(count=count, seed=seed)
    return run_influence(workspace=workspace, snapshot=snapshot, options=options)
