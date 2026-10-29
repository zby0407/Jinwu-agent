"""Influence prediction as a calibrated interval, never a bare score.

The model is deliberately small and boring: three quantile gradient-boosted
regressors on ``log1p`` citations.  What the module spends its effort on is the
part that usually goes missing:

* a deterministic split by hash, so train and holdout do not depend on row order;
* a calibration step whose offsets are fitted on held-out residuals, so the 80%
  interval actually covers about 80%;
* a model card that records features, rows, hyperparameters, metrics, and the
  cohort fingerprint, so the same number can be re-derived or refuted.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cohort import HORIZON_YEARS
from .contracts import PaperRecord, content_hash
from .features import FeatureMatrix, feature_matrix, targets

__all__ = [
    "CVReport",
    "InfluenceModel",
    "InsufficientTrainingData",
    "ModelCard",
    "PredictionSet",
    "cross_validate",
    "interval_coverage",
    "pinball_loss",
    "spearman",
    "top_decile_lift",
]

DEFAULT_QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)

DEFAULT_HYPERPARAMETERS: Mapping[str, Any] = {
    "max_iter": 180,
    "learning_rate": 0.06,
    "max_depth": 3,
    "min_samples_leaf": 8,
    "l2_regularization": 0.0,
    "early_stopping": False,
}

MIN_TRAINING_ROWS = 30
MIN_HOLDOUT_ROWS = 12


class InsufficientTrainingData(RuntimeError):
    """Raised when the labelled cohort is too small to fit anything honest."""


# --- small deterministic statistics -----------------------------------------


def spearman(left: Sequence[float], right: Sequence[float]) -> float:
    """Rank correlation, computed directly so the number is reproducible."""
    pairs = [
        (float(a), float(b))
        for a, b in zip(left, right, strict=True)
        if not (math.isnan(a) or math.isnan(b))
    ]
    if len(pairs) < 3:
        return math.nan
    ranks_a = _ranks([item[0] for item in pairs])
    ranks_b = _ranks([item[1] for item in pairs])
    return _pearson(ranks_a, ranks_b)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (
            end + 1 < len(order) and values[order[end + 1]] == values[order[position]]
        ):
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    count = len(left)
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True)
    )
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)
    denominator = math.sqrt(variance_left * variance_right)
    if denominator == 0:
        return math.nan
    return covariance / denominator


def interval_coverage(
    low: Sequence[float], high: Sequence[float], actual: Sequence[float]
) -> float:
    """Share of actual values that fall inside the interval."""
    pairs = [
        (lo, hi, value)
        for lo, hi, value in zip(low, high, actual, strict=True)
        if not any(math.isnan(item) for item in (lo, hi, value))
    ]
    if not pairs:
        return math.nan
    inside = sum(1 for lo, hi, value in pairs if lo <= value <= hi)
    return inside / len(pairs)


def pinball_loss(
    actual: Sequence[float], predicted: Sequence[float], *, quantile: float
) -> float:
    """Quantile (pinball) loss; lower is better, 0 is perfect."""
    pairs = [
        (float(a), float(p))
        for a, p in zip(actual, predicted, strict=True)
        if not (math.isnan(a) or math.isnan(p))
    ]
    if not pairs:
        return math.nan
    total = 0.0
    for value, prediction in pairs:
        error = value - prediction
        total += max(quantile * error, (quantile - 1.0) * error)
    return total / len(pairs)


def top_decile_lift(
    actual: Sequence[float], predicted: Sequence[float], *, share: float = 0.1
) -> float:
    """How many times more high-impact papers the top decile contains.

    ``1.0`` means no better than picking papers at random; the report quotes this
    against the base rate rather than against a vendor baseline.
    """
    pairs = [
        (float(a), float(p))
        for a, p in zip(actual, predicted, strict=True)
        if not (math.isnan(a) or math.isnan(p))
    ]
    if len(pairs) < 10:
        return math.nan
    ranked = sorted(pairs, key=lambda item: item[1], reverse=True)
    cutoff = max(1, round(len(ranked) * share))
    chosen = ranked[:cutoff]
    threshold = sorted((item[0] for item in ranked), reverse=True)[cutoff - 1]
    hits = sum(1 for value, _ in chosen if value >= threshold)
    base_rate = cutoff / len(ranked)
    return (hits / len(chosen)) / base_rate


def _decile_curve(
    actual: Sequence[float], predicted: Sequence[float], *, bins: int = 5
) -> tuple[dict[str, Any], ...]:
    """Observed outcome per predicted bin: the calibration table."""
    pairs = [
        (float(a), float(p))
        for a, p in zip(actual, predicted, strict=True)
        if not (math.isnan(a) or math.isnan(p))
    ]
    if len(pairs) < bins:
        return ()
    ordered = sorted(pairs, key=lambda item: item[1])
    size = max(1, len(ordered) // bins)
    curve: list[dict[str, Any]] = []
    for index in range(bins):
        start = index * size
        stop = len(ordered) if index == bins - 1 else start + size
        chunk = ordered[start:stop]
        if not chunk:
            continue
        curve.append(
            {
                "bin": index + 1,
                "records": len(chunk),
                "predicted_median": _median([item[1] for item in chunk]),
                "observed_median": _median([item[0] for item in chunk]),
                "observed_p90": _quantile([item[0] for item in chunk], 0.9),
            }
        )
    return tuple(curve)


def _median(values: Sequence[float]) -> float:
    return _quantile(values, 0.5)


def _quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = min(len(ordered) - 1, max(0, round(quantile * (len(ordered) - 1))))
    return float(ordered[position])


# --- model card and predictions ---------------------------------------------


@dataclass(slots=True)
class ModelCard:
    """What a reader needs in order to trust or refute the numbers."""

    model_id: str
    target: str
    horizon_years: int
    reference_year: int
    feature_names: tuple[str, ...]
    rows: int = 0
    labelled: int = 0
    training_rows: int = 0
    holdout_rows: int = 0
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES
    algorithm: str = "HistGradientBoostingRegressor(loss=quantile)"
    hyperparameters: Mapping[str, Any] = field(default_factory=dict)
    seed: int = 0
    offsets: Mapping[str, float] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    calibration_curve: tuple[Mapping[str, Any], ...] = ()
    missing_counts: Mapping[str, int] = field(default_factory=dict)
    text_level_missing_share: float = 0.0
    training_seconds: float = 0.0
    cohort_fingerprint: str = ""
    notes: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    def fingerprint(self) -> str:
        """Hash of the card's scientific content."""
        return content_hash(
            {
                "model_id": self.model_id,
                "target": self.target,
                "horizon_years": self.horizon_years,
                "reference_year": self.reference_year,
                "feature_names": list(self.feature_names),
                "labelled": self.labelled,
                "training_rows": self.training_rows,
                "holdout_rows": self.holdout_rows,
                "quantiles": list(self.quantiles),
                "algorithm": self.algorithm,
                "hyperparameters": dict(self.hyperparameters),
                "seed": self.seed,
                "offsets": dict(self.offsets),
                "metrics": _jsonable(dict(self.metrics)),
                "cohort_fingerprint": self.cohort_fingerprint,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        payload = {
            "model_id": self.model_id,
            "target": self.target,
            "horizon_years": self.horizon_years,
            "reference_year": self.reference_year,
            "feature_names": list(self.feature_names),
            "rows": self.rows,
            "labelled": self.labelled,
            "training_rows": self.training_rows,
            "holdout_rows": self.holdout_rows,
            "quantiles": list(self.quantiles),
            "algorithm": self.algorithm,
            "hyperparameters": _jsonable(dict(self.hyperparameters)),
            "seed": self.seed,
            "offsets": dict(self.offsets),
            "metrics": _jsonable(dict(self.metrics)),
            "calibration_curve": [
                _jsonable(dict(row)) for row in self.calibration_curve
            ],
            "missing_counts": dict(self.missing_counts),
            "text_level_missing_share": self.text_level_missing_share,
            "training_seconds": self.training_seconds,
            "cohort_fingerprint": self.cohort_fingerprint,
            "notes": list(self.notes),
            "created_at": self.created_at,
        }
        payload["fingerprint"] = self.fingerprint()
        return payload

    def write(self, path: str | Path) -> str:
        """Write the card as JSON."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return str(target)


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {str(key): _jsonable(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_jsonable(item) for item in payload]
    if isinstance(payload, float):
        return None if math.isnan(payload) else payload
    return payload


@dataclass(slots=True)
class PredictionSet:
    """Per-paper predictions in every unit a reader might want."""

    paper_ids: tuple[str, ...]
    log_low: tuple[float, ...]
    log_median: tuple[float, ...]
    log_high: tuple[float, ...]
    low: tuple[float, ...]
    median: tuple[float, ...]
    high: tuple[float, ...]
    rows: tuple[Mapping[str, float], ...] = ()
    quantile_crossings: int = 0

    def __len__(self) -> int:
        return len(self.paper_ids)

    def for_paper(self, paper_id: str) -> dict[str, Any]:
        """One paper's prediction as a plain mapping."""
        index = self.paper_ids.index(paper_id)
        return {
            "paper_id": paper_id,
            "predicted_citations_low": self.low[index],
            "predicted_citations_median": self.median[index],
            "predicted_citations_high": self.high[index],
            "log_median": self.log_median[index],
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "papers": [
                {
                    "paper_id": paper_id,
                    "low": self.low[index],
                    "median": self.median[index],
                    "high": self.high[index],
                }
                for index, paper_id in enumerate(self.paper_ids)
            ]
        }


# --- the model --------------------------------------------------------------


@dataclass(slots=True)
class InfluenceModel:
    """Three quantile regressors plus the offsets that make them honest."""

    estimators: Mapping[float, Any]
    card: ModelCard

    def predict(self, papers: Sequence[PaperRecord]) -> PredictionSet:
        """Predict for new papers with the model's own column order."""
        matrix = feature_matrix(
            papers,
            reference_year=self.card.reference_year,
            include_text_level=any(
                name in self.card.feature_names
                for name in _text_level_names(self.card.feature_names)
            ),
        )
        return self.predict_matrix(matrix)

    def predict_matrix(self, matrix: FeatureMatrix) -> PredictionSet:
        """Predict from a pre-built matrix (used to avoid recomputing features)."""
        import numpy as np

        rows = np.asarray(matrix.rows, dtype=float)
        ordered = [matrix.names.index(name) for name in self.card.feature_names]
        rows = rows[:, ordered]
        logs: dict[float, list[float]] = {}
        for quantile in self.card.quantiles:
            estimator = self.estimators[quantile]
            offset = float(self.card.offsets.get(str(quantile), 0.0))
            raw = estimator.predict(rows) + offset
            logs[quantile] = [float(value) for value in raw]
        raw_low = logs[min(self.card.quantiles)]
        raw_median = logs[0.5] if 0.5 in logs else logs[sorted(logs)[len(logs) // 2]]
        raw_high = logs[max(self.card.quantiles)]
        # Independently fitted quantiles can cross; a crossing is repaired upward and
        # counted, because a silent "low > median" interval would be a lie.
        low = list(raw_low)
        median = [
            max(value, floor) for value, floor in zip(raw_median, raw_low, strict=True)
        ]
        high = [
            max(value, floor) for value, floor in zip(raw_high, median, strict=True)
        ]
        crossings = sum(
            1
            for index, (lo, mid, hi) in enumerate(
                zip(raw_low, raw_median, raw_high, strict=True)
            )
            if not (lo <= mid <= hi)
        )
        return PredictionSet(
            paper_ids=matrix.paper_ids,
            log_low=tuple(low),
            log_median=tuple(median),
            log_high=tuple(high),
            low=tuple(_counts(value) for value in low),
            median=tuple(_counts(value) for value in median),
            high=tuple(_counts(value) for value in high),
            rows=matrix.rows,
            quantile_crossings=crossings,
        )

    def metrics_for(
        self, papers: Sequence[PaperRecord], *, horizon: int | None = None
    ) -> dict[str, Any]:
        """Out-of-sample metrics on any labelled set."""
        horizon = horizon or self.card.horizon_years
        truth = targets(
            papers, reference_year=self.card.reference_year, horizon=horizon
        )
        predictions = self.predict(papers)
        keep = [
            index
            for index, flag in enumerate(truth.available)
            if flag and not math.isnan(truth.values[index])
        ]
        actual = [truth.values[index] for index in keep]
        low = [predictions.log_low[index] for index in keep]
        median = [predictions.log_median[index] for index in keep]
        high = [predictions.log_high[index] for index in keep]
        return _metrics(actual, low, median, high, self.card.quantiles)


def _counts(log_value: float) -> float:
    """Convert a log1p prediction back to a citation count."""
    if math.isnan(log_value):
        return math.nan
    return max(0.0, math.expm1(max(-6.0, min(12.0, log_value))))


def _metrics(
    actual: Sequence[float],
    low: Sequence[float],
    median: Sequence[float],
    high: Sequence[float],
    quantiles: Sequence[float],
) -> dict[str, Any]:
    nominal = max(quantiles) - min(quantiles)
    return {
        "records": len(actual),
        "spearman": spearman(actual, median),
        "top_decile_lift": top_decile_lift(actual, median),
        "interval_nominal": nominal,
        "interval_coverage": interval_coverage(low, high, actual),
        "median_absolute_error_log": _mean_absolute_error(actual, median),
        "pinball": {
            str(quantile): pinball_loss(
                actual,
                median
                if quantile == 0.5
                else (low if quantile == min(quantiles) else high),
                quantile=quantile,
            )
            for quantile in quantiles
        },
        "calibration_curve": _decile_curve(actual, median),
    }


def _mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = [
        (a, p)
        for a, p in zip(actual, predicted, strict=True)
        if not (math.isnan(a) or math.isnan(p))
    ]
    if not pairs:
        return math.nan
    return sum(abs(a - p) for a, p in pairs) / len(pairs)


def _text_level_names(names: Sequence[str]) -> tuple[str, ...]:
    from .features import FEATURES

    text_level = {spec.name for spec in FEATURES if spec.requires_full_text}
    return tuple(name for name in names if name in text_level)


def _stable_key(paper_id: str, seed: int) -> float:
    """A uniform number per paper that does not depend on row order."""
    digest = hashlib.sha256(f"{seed}:{paper_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _split_ids(
    paper_ids: Sequence[str], *, seed: int, holdout_share: float
) -> tuple[list[int], list[int]]:
    keyed = sorted(
        range(len(paper_ids)), key=lambda index: _stable_key(paper_ids[index], seed)
    )
    cutoff = max(0, round(len(keyed) * (1.0 - holdout_share)))
    return sorted(keyed[:cutoff]), sorted(keyed[cutoff:])


def _fit_model(
    papers: Sequence[PaperRecord],
    *,
    reference_year: int,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    horizon: int = HORIZON_YEARS,
    seed: int = 0,
    holdout_share: float = 0.3,
    include_text_level: bool = True,
    hyperparameters: Mapping[str, Any] | None = None,
    cohort_fingerprint: str = "",
    model_id: str = "",
) -> InfluenceModel:
    from sklearn.ensemble import HistGradientBoostingRegressor

    parameters = dict(DEFAULT_HYPERPARAMETERS)
    parameters.update(dict(hyperparameters or {}))
    started = time.perf_counter()

    matrix = feature_matrix(
        papers, reference_year=reference_year, include_text_level=include_text_level
    )
    truth = targets(papers, reference_year=reference_year, horizon=horizon)
    labelled = list(truth.usable)
    if len(labelled) < MIN_TRAINING_ROWS:
        raise InsufficientTrainingData(
            f"{len(labelled)} labelled papers, {MIN_TRAINING_ROWS} needed: "
            "the cohort is too small for a calibrated model"
        )

    holdout_note = ""
    train_rows, holdout_rows = _split_ids(
        [matrix.paper_ids[index] for index in labelled],
        seed=seed,
        holdout_share=holdout_share,
    )
    if len(holdout_rows) < MIN_HOLDOUT_ROWS:
        train_rows = list(range(len(labelled)))
        holdout_rows = []
        holdout_note = (
            "holdout too small; calibration offsets were fitted on the training "
            "rows and the coverage claim is in-sample"
        )

    train_index = [labelled[index] for index in train_rows]
    holdout_index = [labelled[index] for index in holdout_rows]
    _x = _matrix_rows(matrix, train_index)
    _y = [truth.values[index] for index in train_index]

    # A column that never varies carries no information and breaks the
    # histogram binning, so it is dropped -- and named, not silently forgotten.
    keep = [
        index for index in range(len(matrix.names)) if _is_informative(_x[:, index])
    ]
    dropped = tuple(
        name for index, name in enumerate(matrix.names) if index not in set(keep)
    )
    _x = _x[:, keep]
    feature_names = tuple(matrix.names[index] for index in keep)

    estimators: dict[float, Any] = {}
    offsets: dict[str, float] = {}
    if holdout_index:
        holdout_x = _matrix_rows(matrix, holdout_index)[:, keep]
        holdout_y = [truth.values[index] for index in holdout_index]
    for quantile in quantiles:
        estimator = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=quantile,
            random_state=seed,
            **parameters,
        )
        estimator.fit(_x, _y)
        estimators[quantile] = estimator
        offsets[str(quantile)] = 0.0
    if holdout_index:
        # Fit the offsets so that the empirical coverage matches the nominal one.
        for quantile in quantiles:
            estimator = estimators[quantile]
            residuals = [
                actual - float(prediction)
                for actual, prediction in zip(
                    holdout_y, estimator.predict(holdout_x), strict=True
                )
            ]
            offsets[str(quantile)] = _quantile(residuals, quantile)

    card = ModelCard(
        model_id=model_id or f"model:{reference_year}:h{horizon}:s{seed}",
        target=f"citations within {horizon} years (log1p)",
        horizon_years=horizon,
        reference_year=reference_year,
        feature_names=feature_names,
        rows=len(matrix),
        labelled=len(labelled),
        training_rows=len(train_index),
        holdout_rows=len(holdout_index),
        quantiles=tuple(quantiles),
        hyperparameters=parameters,
        seed=seed,
        offsets=offsets,
        missing_counts=dict(matrix.missing_counts),
        text_level_missing_share=matrix.text_level_missing_share(),
        training_seconds=round(time.perf_counter() - started, 3),
        cohort_fingerprint=cohort_fingerprint,
        notes=tuple(
            note
            for note in (
                holdout_note,
                f"dropped_constant_features:{','.join(dropped)}" if dropped else "",
            )
            if note
        ),
    )
    model = InfluenceModel(estimators=estimators, card=card)
    if holdout_index:
        metrics = _evaluate_indices(model, matrix, truth, holdout_index)
        card.metrics = metrics
        card.calibration_curve = tuple(metrics.get("calibration_curve", ()))
    else:
        metrics = _evaluate_indices(model, matrix, truth, train_index)
        card.metrics = metrics
        card.calibration_curve = tuple(metrics.get("calibration_curve", ()))
    return model


def _is_informative(column: Any) -> bool:
    """Whether a column has at least two distinct observed values."""
    import numpy as np

    observed = column[~np.isnan(column)]
    return bool(len(np.unique(observed)) >= 2)


def _matrix_rows(matrix: FeatureMatrix, indices: Sequence[int]) -> Any:
    import numpy as np

    rows = np.asarray(matrix.rows, dtype=float)
    return rows[list(indices)]


def _evaluate_indices(
    model: InfluenceModel,
    matrix: FeatureMatrix,
    truth: Any,
    indices: Sequence[int],
) -> dict[str, Any]:
    subset = FeatureMatrix(
        names=matrix.names,
        paper_ids=tuple(matrix.paper_ids[index] for index in indices),
        rows=tuple(matrix.rows[index] for index in indices),
        missing_counts=matrix.missing_counts,
        full_text_level=matrix.full_text_level,
    )
    predictions = model.predict_matrix(subset)
    actual = [truth.values[index] for index in indices]
    return _metrics(
        actual,
        predictions.log_low,
        predictions.log_median,
        predictions.log_high,
        model.card.quantiles,
    )


def fit_model(
    papers: Iterable[PaperRecord],
    *,
    reference_year: int,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    horizon: int = HORIZON_YEARS,
    seed: int = 0,
    holdout_share: float = 0.3,
    include_text_level: bool = True,
    hyperparameters: Mapping[str, Any] | None = None,
    cohort_fingerprint: str = "",
    model_id: str = "",
) -> InfluenceModel:
    """Fit the interval model on a cohort of papers."""
    return _fit_model(
        list(papers),
        reference_year=reference_year,
        quantiles=quantiles,
        horizon=horizon,
        seed=seed,
        holdout_share=holdout_share,
        include_text_level=include_text_level,
        hyperparameters=hyperparameters,
        cohort_fingerprint=cohort_fingerprint,
        model_id=model_id,
    )


@dataclass(slots=True)
class CVReport:
    """Repeated cross-validation results, so a single split cannot flatter us."""

    folds: tuple[Mapping[str, Any], ...]
    aggregate: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "folds": [_jsonable(dict(fold)) for fold in self.folds],
            "aggregate": _jsonable(dict(self.aggregate)),
            "notes": list(self.notes),
        }


def cross_validate(
    papers: Sequence[PaperRecord],
    *,
    reference_year: int,
    horizon: int = HORIZON_YEARS,
    folds: int = 4,
    repeats: int = 3,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    include_text_level: bool = True,
) -> CVReport:
    """Repeated held-out evaluation; each repeat refits from scratch."""
    results: list[dict[str, Any]] = []
    for repeat in range(repeats):
        fold_of = {
            paper.paper_id: _stable_key(paper.paper_id, 1000 + repeat)
            for paper in papers
        }
        for fold in range(folds):
            holdout = {
                paper_id
                for paper_id, position in fold_of.items()
                if int(position * folds) == fold
            }
            if not holdout:
                continue
            train = [paper for paper in papers if paper.paper_id not in holdout]
            test = [paper for paper in papers if paper.paper_id in holdout]
            try:
                model = fit_model(
                    train,
                    reference_year=reference_year,
                    horizon=horizon,
                    quantiles=quantiles,
                    seed=repeat,
                    include_text_level=include_text_level,
                )
            except InsufficientTrainingData as error:
                results.append({"repeat": repeat, "fold": fold, "error": str(error)})
                continue
            metrics = model.metrics_for(test, horizon=horizon)
            results.append(
                {
                    "repeat": repeat,
                    "fold": fold,
                    "records": metrics["records"],
                    "spearman": metrics["spearman"],
                    "interval_coverage": metrics["interval_coverage"],
                    "top_decile_lift": metrics["top_decile_lift"],
                }
            )
    scored = [row for row in results if "error" not in row]
    aggregate = {
        "folds": len(results),
        "scored_folds": len(scored),
        "spearman_mean": _mean([row["spearman"] for row in scored]),
        "spearman_sd": _sd([row["spearman"] for row in scored]),
        "interval_coverage_mean": _mean([row["interval_coverage"] for row in scored]),
        "top_decile_lift_mean": _mean([row["top_decile_lift"] for row in scored]),
        "top_decile_lift_sd": _sd([row["top_decile_lift"] for row in scored]),
    }
    notes = (
        "Coverage spread across folds is the honest uncertainty of the interval, "
        "not a property of a single split.",
    )
    return CVReport(folds=tuple(results), aggregate=aggregate, notes=notes)


def _mean(values: Sequence[float]) -> float:
    clean = [value for value in values if not math.isnan(value)]
    return sum(clean) / len(clean) if clean else math.nan


def _sd(values: Sequence[float]) -> float:
    clean = [value for value in values if not math.isnan(value)]
    if len(clean) < 2:
        return math.nan
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / (len(clean) - 1)
    return math.sqrt(variance)
