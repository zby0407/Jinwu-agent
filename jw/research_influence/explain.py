"""Explanations a reader can audit: deltas, intervals and comparators.

The rule for this module is that every sentence it produces must be checkable
against numbers it also produces. No SHAP, no black-box attribution: the model
input is ablated, the cohort is resampled, and both the point estimate and the
wobble of that estimate are reported.

Three kinds of explanation live here:

* ``FactorEffect`` - what changes if this paper's value for a feature is set to
  the cohort reference value, with a bootstrap CI over *which reference value*
  the cohort implies.
* ``permutation_importance`` - what the model as a whole relies on, measured as
  the out-of-sample rank correlation lost when a column is shuffled.
* ``Comparator`` - which cohort papers are closest in feature space, so a reader
  can see whether the prediction is consistent with visible neighbours.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import PaperRecord
from .features import (
    FEATURES,
    FeatureMatrix,
    citations_within,
    feature_matrix,
    targets,
)
from .model import InfluenceModel, _median, spearman

__all__ = [
    "Comparator",
    "FactorEffect",
    "FeatureImportance",
    "PaperExplanation",
    "counterfactual",
    "counts_from_log",
    "explain_paper",
    "feature_labels",
    "importance_rank",
    "partial_dependence",
    "permutation_importance",
    "rank_stability",
    "summarise_importance",
]

DELTA_THRESHOLD = 0.05
DEFAULT_BOOTSTRAP = 100
DEFAULT_NEIGHBOURS = 5
DEFAULT_TOP_FACTORS = 6
DEFAULT_SEEDS = (0, 1, 2, 3, 4)
DEFAULT_REPEATS = 5


def counts_from_log(log_value: float) -> float:
    """Inverse of the training ``log1p`` transform, clamped like the model does."""
    if log_value is None or math.isnan(log_value):
        return math.nan
    return max(0.0, math.expm1(max(-6.0, min(12.0, log_value))))


_SPECS = {spec.name: spec for spec in FEATURES}


def feature_labels() -> dict[str, str]:
    """Human-readable label per model column."""
    return {spec.name: spec.description for spec in FEATURES}


def _label(name: str) -> str:
    spec = _SPECS.get(name)
    return spec.description if spec else name


def _route(name: str) -> str:
    spec = _SPECS.get(name)
    return spec.bias_route if spec else ""


def _matrix(
    names: tuple[str, ...], rows: list[list[float]], paper_ids: list[str]
) -> FeatureMatrix:
    missing = dict.fromkeys(names, 0)
    for row in rows:
        for index, value in enumerate(row):
            if value is None or math.isnan(value):
                missing[names[index]] += 1
    cleaned = [
        [0.0 if value is None or math.isnan(value) else float(value) for value in row]
        for row in rows
    ]
    return FeatureMatrix(
        names=names,
        paper_ids=tuple(paper_ids),
        rows=tuple(tuple(row) for row in cleaned),
        missing_counts=missing,
        full_text_level=tuple(
            name
            for name in names
            if _SPECS.get(name) and _SPECS[name].requires_full_text
        ),
    )


@dataclass(slots=True)
class FactorEffect:
    """One feature's ablation on one paper, with the cohort's own uncertainty."""

    feature: str
    label: str
    route: str
    observed: float | None
    reference: float
    delta_log: float
    delta_counts: float
    ci_low_log: float
    ci_high_log: float
    ci_low: float
    ci_high: float
    direction: str = "neutral"
    share: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the report and the JSON artefacts."""
        return {
            "feature": self.feature,
            "label": self.label,
            "bias_route": self.route,
            "observed": _number(self.observed),
            "reference": _number(self.reference),
            "delta_log": round(self.delta_log, 6),
            "delta_citations": round(self.delta_counts, 3),
            "ci_low_log": round(self.ci_low_log, 6),
            "ci_high_log": round(self.ci_high_log, 6),
            "ci_low_citations": round(self.ci_low, 3),
            "ci_high_citations": round(self.ci_high, 3),
            "direction": self.direction,
            "share_of_explained": round(self.share, 4),
            "note": self.note,
        }

    def sentence(self) -> str:
        """One line a reader can check against ``to_dict``."""
        observed = "unknown" if self.observed is None else f"{self.observed:.3g}"
        direction = {
            "raises": "raises",
            "lowers": "lowers",
            "neutral": "barely changes",
        }[self.direction]
        return (
            f"{self.label}: observed {observed}, cohort reference "
            f"{self.reference:.3g} - setting it to the reference {direction} the "
            f"3-year citation estimate by {abs(self.delta_counts):.1f} "
            f"(95% CI {self.ci_low:+.1f} to {self.ci_high:+.1f} citations)"
        )


@dataclass(slots=True)
class Comparator:
    """A nearby cohort paper, so the prediction can be sanity-checked by eye."""

    paper_id: str
    title: str
    year: int
    venue: str
    observed_citations: int
    predicted_low: float
    predicted_median: float
    predicted_high: float
    distance: float

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the report."""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "year": self.year,
            "venue": self.venue,
            "observed_citations": self.observed_citations,
            "predicted_low": round(self.predicted_low, 2),
            "predicted_median": round(self.predicted_median, 2),
            "predicted_high": round(self.predicted_high, 2),
            "feature_distance": round(self.distance, 4),
        }


@dataclass(slots=True)
class PaperExplanation:
    """Everything the report needs to say about one paper."""

    paper_id: str
    title: str
    year: int
    venue: str
    observed_citations: int
    low: float
    median: float
    high: float
    factors: tuple[FactorEffect, ...] = ()
    comparators: tuple[Comparator, ...] = ()
    notes: tuple[str, ...] = ()
    extras: dict[str, Any] = field(default_factory=dict)

    def summary_lines(self) -> tuple[str, ...]:
        """The explanation as short sentences, in reading order."""
        lines = [
            f"{self.title or self.paper_id} ({self.year or 'year unknown'}, "
            f"{self.venue or 'venue unknown'})",
            f"Predicted citations in three years: {self.median:.0f} "
            f"(80% interval {self.low:.0f}-{self.high:.0f}); "
            f"observed to date: {self.observed_citations}",
        ]
        lines.extend(effect.sentence() for effect in self.factors)
        if self.comparators:
            lines.append(
                "Closest cohort papers: "
                + "; ".join(
                    f"{item.title[:48] or item.paper_id} "
                    f"(observed {item.observed_citations}, "
                    f"predicted {item.predicted_median:.0f})"
                    for item in self.comparators
                )
            )
        lines.extend(self.notes)
        return tuple(lines)

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the package and the JSON artefacts."""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "year": self.year,
            "venue": self.venue,
            "observed_citations": self.observed_citations,
            "predicted_low": round(self.low, 2),
            "predicted_median": round(self.median, 2),
            "predicted_high": round(self.high, 2),
            "factors": [effect.to_dict() for effect in self.factors],
            "comparators": [item.to_dict() for item in self.comparators],
            "notes": list(self.notes),
            "summary": list(self.summary_lines()),
            "extras": dict(self.extras),
        }


@dataclass(slots=True)
class FeatureImportance:
    """A column's contribution to out-of-sample ranking, and its stability."""

    feature: str
    label: str
    route: str
    metric: str
    mean_drop: float
    sd_drop: float
    ci_low: float
    ci_high: float
    repeats: int
    supported: bool
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the report and the JSON artefacts."""
        return {
            "feature": self.feature,
            "label": self.label,
            "bias_route": self.route,
            "metric": self.metric,
            "mean_drop": round(self.mean_drop, 6),
            "sd_drop": round(self.sd_drop, 6),
            "ci_low": round(self.ci_low, 6),
            "ci_high": round(self.ci_high, 6),
            "repeats": self.repeats,
            "supported": self.supported,
            "rank": self.rank,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _base_rows(
    model: InfluenceModel, papers: list[PaperRecord]
) -> tuple[list[list[float]], list[str]]:
    names = model.card.feature_names
    include_text = any(
        _SPECS.get(name) and _SPECS[name].requires_full_text for name in names
    )
    matrix = feature_matrix(
        papers,
        reference_year=model.card.reference_year,
        include_text_level=include_text,
    )
    index = {name: position for position, name in enumerate(matrix.names)}
    rows: list[list[float]] = []
    for row in matrix.rows:
        rows.append([row[index[name]] if name in index else 0.0 for name in names])
    return rows, list(matrix.paper_ids)


def _predict_logs(model: InfluenceModel, rows: list[list[float]]) -> list[float]:
    matrix = _matrix(model.card.feature_names, rows, [str(i) for i in range(len(rows))])
    return list(model.predict_matrix(matrix).log_median)


def _column_reference(rows: list[list[float]], index: int) -> float:
    values = [
        row[index] for row in rows if index < len(row) and not math.isnan(row[index])
    ]
    return _median(values) if values else 0.0


def _bootstrap_references(
    rows: list[list[float]], index: int, *, bootstrap: int, seed: int
) -> list[float]:
    values = [
        row[index] for row in rows if index < len(row) and not math.isnan(row[index])
    ]
    if not values:
        return [0.0] * bootstrap
    rng = random.Random(seed)
    draws = len(values)
    references: list[float] = []
    for _ in range(bootstrap):
        sample = [values[rng.randrange(draws)] for _ in range(draws)]
        references.append(_median(sample))
    return references


def _distance(left: list[float], right: list[float], scales: list[float]) -> float:
    total = 0.0
    for index, (first, second) in enumerate(zip(left, right, strict=True)):
        if math.isnan(first) or math.isnan(second):
            continue
        scale = scales[index] or 1.0
        total += ((first - second) / scale) ** 2
    return math.sqrt(total)


def _scales(rows: list[list[float]], width: int) -> list[float]:
    scales: list[float] = []
    for index in range(width):
        column = [row[index] for row in rows if not math.isnan(row[index])]
        if not column:
            scales.append(1.0)
            continue
        low = _percentile(column, 0.25)
        high = _percentile(column, 0.75)
        spread = abs(high - low)
        scales.append(spread if spread > 0 else 1.0)
    return scales


def _comparators(
    model: InfluenceModel,
    paper: PaperRecord,
    cohort: list[PaperRecord],
    *,
    base_rows: list[list[float]],
    base_row: list[float],
    limit: int,
) -> tuple[Comparator, ...]:
    others = [item for item in cohort if item.paper_id != paper.paper_id]
    if not others or limit <= 0:
        return ()
    other_rows, _ = _base_rows(model, others)
    scales = _scales([*other_rows, base_row], len(base_row))
    scored = sorted(
        (
            (_distance(base_row, row, scales), index)
            for index, row in enumerate(other_rows)
        ),
        key=lambda item: (item[0], item[1]),
    )[:limit]
    if not scored:
        return ()
    predictions = model.predict_matrix(
        _matrix(
            model.card.feature_names,
            [other_rows[index] for _distance_value, index in scored],
            [str(index) for _distance_value, index in scored],
        )
    )
    comparators: list[Comparator] = []
    for position, (distance, index) in enumerate(scored):
        item = others[index]
        comparators.append(
            Comparator(
                paper_id=item.paper_id,
                title=item.title or "",
                year=item.year or 0,
                venue=item.venue.name if item.venue else "",
                observed_citations=item.cited_by_count or 0,
                predicted_low=predictions.low[position],
                predicted_median=predictions.median[position],
                predicted_high=predictions.high[position],
                distance=distance,
            )
        )
    return tuple(comparators)


def explain_paper(
    model: InfluenceModel,
    paper: PaperRecord,
    *,
    cohort: Sequence[PaperRecord],
    top: int = DEFAULT_TOP_FACTORS,
    neighbours: int = DEFAULT_NEIGHBOURS,
    bootstrap: int = DEFAULT_BOOTSTRAP,
    seed: int = 0,
) -> PaperExplanation:
    """Explain one paper against an explicit cohort."""
    papers = list(cohort)
    rows, paper_ids = _base_rows(model, papers)
    base_rows, _ = _base_rows(model, [paper])
    base_row = base_rows[0]
    base_log = _predict_logs(model, [base_row])[0]
    prediction = model.predict_matrix(
        _matrix(model.card.feature_names, [base_row], [paper.paper_id or "paper"])
    )
    names = model.card.feature_names

    observed_citations = citations_within(paper, horizon=model.card.horizon_years)
    effects: list[FactorEffect] = []
    for index, name in enumerate(names):
        observed_value = base_row[index]
        if math.isnan(observed_value):
            continue
        reference = _column_reference(rows, index)
        if abs(reference - observed_value) < 1e-12:
            continue
        references = _bootstrap_references(
            rows, index, bootstrap=bootstrap, seed=seed + index
        )
        ablated = []
        for value in references:
            row = list(base_row)
            row[index] = value
            ablated.append(row)
        deltas = [value - base_log for value in _predict_logs(model, ablated)]
        point_row = list(base_row)
        point_row[index] = reference
        point_delta = _predict_logs(model, [point_row])[0] - base_log
        notes: list[str] = []
        spec = _SPECS.get(name)
        if spec and spec.requires_full_text and math.isnan(observed_value):
            notes.append("full_text_unavailable")
        effects.append(
            FactorEffect(
                feature=name,
                label=_label(name),
                route=_route(name),
                observed=None if math.isnan(observed_value) else observed_value,
                reference=reference,
                delta_log=point_delta,
                delta_counts=counts_from_log(base_log + point_delta)
                - counts_from_log(base_log),
                ci_low_log=_percentile(deltas, 0.025),
                ci_high_log=_percentile(deltas, 0.975),
                ci_low=counts_from_log(base_log + _percentile(deltas, 0.025))
                - counts_from_log(base_log),
                ci_high=counts_from_log(base_log + _percentile(deltas, 0.975))
                - counts_from_log(base_log),
                note=";".join(notes),
            )
        )

    total = sum(abs(effect.delta_log) for effect in effects) or 1.0
    for effect in effects:
        effect.share = abs(effect.delta_log) / total
        if effect.delta_log > DELTA_THRESHOLD:
            effect.direction = "raises"
        elif effect.delta_log < -DELTA_THRESHOLD:
            effect.direction = "lowers"
        else:
            effect.direction = "neutral"
    effects.sort(key=lambda effect: (effect.share, effect.feature), reverse=True)
    selected = tuple(effects[:top])

    comparators = _comparators(
        model,
        paper,
        papers,
        base_rows=rows,
        base_row=base_row,
        limit=neighbours,
    )
    notes: list[str] = []
    if not paper.has_full_text:
        missing_text = [
            _label(name)
            for name in names
            if _SPECS.get(name) and _SPECS[name].requires_full_text
        ]
        if missing_text:
            notes.append(
                "Full text was unavailable, so these features are absent and the "
                "interval above is wider than it would be with the PDF: "
                + ", ".join(missing_text)
            )
    if paper_ids and len(papers) < 30:
        notes.append(
            f"Cohort has only {len(papers)} papers; the reference values behind every "
            "delta move by more than a bootstrap CI can express."
        )
    return PaperExplanation(
        paper_id=paper.paper_id,
        title=paper.title or "",
        year=paper.year or 0,
        venue=paper.venue.name if paper.venue else "",
        observed_citations=observed_citations or 0,
        low=prediction.low[0],
        median=prediction.median[0],
        high=prediction.high[0],
        factors=selected,
        comparators=comparators,
        notes=tuple(notes),
        extras={
            "horizon_years": model.card.horizon_years,
            "reference_year": model.card.reference_year,
            "cohort_size": len(papers),
            "bootstrap": bootstrap,
            "quantile_crossings": prediction.quantile_crossings,
            "log_median": round(base_log, 6),
        },
    )


def permutation_importance(
    model: InfluenceModel,
    papers: Sequence[PaperRecord],
    *,
    repeats: int = DEFAULT_REPEATS,
    seeds: Sequence[int] = DEFAULT_SEEDS,
) -> tuple[FeatureImportance, ...]:
    """Rank-correlation lost when one column is shuffled, averaged over seeds."""
    usable = list(papers)
    matrix = feature_matrix(
        usable,
        reference_year=model.card.reference_year,
        include_text_level=True,
    )
    truth = targets(
        usable,
        reference_year=model.card.reference_year,
        horizon=model.card.horizon_years,
    )
    keep = [index for index, flag in enumerate(truth.available) if flag]
    if len(keep) < 10:
        return ()
    index = {name: position for position, name in enumerate(matrix.names)}
    names = model.card.feature_names
    rows = [
        [matrix.rows[position][index[name]] if name in index else 0.0 for name in names]
        for position in keep
    ]
    actual = [truth.values[position] for position in keep]
    base_prediction = _predict_logs(model, rows)
    base_metric = spearman(actual, base_prediction)
    if math.isnan(base_metric):
        return ()
    drops: dict[str, list[float]] = {name: [] for name in names}
    for seed in seeds:
        rng = random.Random(seed)
        for position, name in enumerate(names):
            shuffled = [list(row) for row in rows]
            column = [row[position] for row in shuffled]
            rng.shuffle(column)
            for row_index, value in enumerate(column):
                shuffled[row_index][position] = value
            metric = spearman(actual, _predict_logs(model, shuffled))
            if math.isnan(metric):
                continue
            drops[name].append(base_metric - metric)
    importances: list[FeatureImportance] = []
    for name in names:
        values = drops[name]
        if not values:
            continue
        mean_drop = sum(values) / len(values)
        variance = (
            sum((value - mean_drop) ** 2 for value in values) / (len(values) - 1)
            if len(values) > 1
            else 0.0
        )
        low = _percentile(values, 0.025)
        high = _percentile(values, 0.975)
        importances.append(
            FeatureImportance(
                feature=name,
                label=_label(name),
                route=_route(name),
                metric="spearman_drop",
                mean_drop=mean_drop,
                sd_drop=math.sqrt(variance),
                ci_low=low,
                ci_high=high,
                repeats=len(values),
                supported=low > 0.0,
            )
        )
    importances.sort(key=lambda item: (-item.mean_drop, item.feature))
    for rank, item in enumerate(importances, start=1):
        item.rank = rank
    return tuple(importances)


def summarise_importance(
    importances: Sequence[FeatureImportance], *, top: int = 10
) -> tuple[str, ...]:
    """Report lines for the model section."""
    supported = [item for item in importances if item.supported]
    lines = [
        f"{len(supported)} of {len(importances)} features carry ranking signal "
        f"whose 95% CI excludes zero (metric: {importances[0].metric})"
        if importances
        else "no feature importance could be estimated"
    ]
    for item in importances[:top]:
        marker = "supported" if item.supported else "not distinguishable from noise"
        lines.append(
            f"{item.rank}. {item.label}: ranking loss {item.mean_drop:+.3f} "
            f"(CI {item.ci_low:+.3f} to {item.ci_high:+.3f}, {marker})"
        )
    return tuple(lines)


def importance_rank(importances: Sequence[FeatureImportance]) -> list[str]:
    """Feature names in importance order."""
    return [item.feature for item in importances]


def rank_stability(rankings: Sequence[Sequence[str]]) -> float:
    """Mean pairwise Spearman between rankings of the same features."""
    vectors = [list(ranking) for ranking in rankings if ranking]
    if len(vectors) < 2:
        return math.nan
    scores: list[float] = []
    for first in range(len(vectors)):
        for second in range(first + 1, len(vectors)):
            left = vectors[first]
            right = vectors[second]
            shared = [name for name in left if name in right]
            if len(shared) < 3:
                continue
            left_scores = [left.index(name) for name in shared]
            right_scores = [right.index(name) for name in shared]
            value = spearman(
                [float(-score) for score in left_scores],
                [float(-score) for score in right_scores],
            )
            if not math.isnan(value):
                scores.append(value)
    return sum(scores) / len(scores) if scores else math.nan


def partial_dependence(
    model: InfluenceModel,
    papers: Sequence[PaperRecord],
    feature: str,
    *,
    steps: int = 5,
) -> tuple[tuple[float, float, float], ...]:
    """Average prediction as one feature is swept, holding the rest fixed."""
    if feature not in model.card.feature_names:
        return ()
    rows, _ = _base_rows(model, list(papers))
    index = model.card.feature_names.index(feature)
    column = [row[index] for row in rows if not math.isnan(row[index])]
    if not column:
        return ()
    grid = sorted(
        {
            round(_percentile(column, position / max(1, steps - 1)), 6)
            for position in range(steps)
        }
    )
    swept: list[list[float]] = []
    for value in grid:
        for row in rows:
            candidate = list(row)
            candidate[index] = value
            swept.append(candidate)
    predictions = _predict_logs(model, swept)
    size = len(rows)
    return tuple(
        (
            value,
            sum(predictions[position * size : (position + 1) * size]) / size,
            counts_from_log(
                sum(predictions[position * size : (position + 1) * size]) / size
            ),
        )
        for position, value in enumerate(grid)
    )


def counterfactual(
    model: InfluenceModel,
    paper: PaperRecord,
    *,
    feature: str,
    values: Sequence[float],
) -> tuple[tuple[float, float, float], ...]:
    """What the prediction would be if this paper's feature took other values."""
    if feature not in model.card.feature_names:
        return ()
    index = model.card.feature_names.index(feature)
    rows, _ = _base_rows(model, [paper])
    base_row = rows[0]
    candidates: list[list[float]] = []
    for value in values:
        candidate = list(base_row)
        candidate[index] = float(value)
        candidates.append(candidate)
    predictions = _predict_logs(model, candidates)
    return tuple(
        (
            float(value),
            prediction,
            counts_from_log(prediction),
        )
        for value, prediction in zip(values, predictions, strict=True)
    )
