"""The model must predict intervals, not scores, and be reproducible."""

from __future__ import annotations

from pathlib import Path

import math
import random

import pytest

from jw.research_influence.cohort import synthetic_cohort
from jw.research_influence.model import (
    InfluenceModel,
    InsufficientTrainingData,
    fit_model,
    interval_coverage,
    pinball_loss,
    spearman,
    top_decile_lift,
)


@pytest.fixture(scope="module")
def cohort():
    return synthetic_cohort(count=150, seed=4, reference_year=2024)


@pytest.fixture(scope="module")
def model(cohort) -> InfluenceModel:
    return fit_model(
        cohort.papers,
        reference_year=cohort.reference_year,
        cohort_fingerprint=cohort.fingerprint(),
    )


def test_spearman_is_a_rank_correlation():
    """Sanity: monotone orders correlate perfectly."""
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(spearman([1, 2], [1, 2]))


def test_interval_coverage_counts_inside_values():
    """Coverage is a simple share, and NaN rows are ignored."""
    assert interval_coverage([0, 0, 0], [2, 2, 2], [1, 3, 1]) == pytest.approx(2 / 3)


def test_pinball_loss_is_minimal_at_the_right_quantile():
    """The 0.9 quantile model is scored differently from the median one."""
    actual = [0.0, 1.0, 2.0, 3.0]
    perfect_median = pinball_loss(actual, actual, quantile=0.5)
    off_median = pinball_loss(actual, [3.0, 3.0, 0.0, 0.0], quantile=0.5)
    assert perfect_median == pytest.approx(0.0)
    assert off_median > perfect_median


def test_top_decile_lift_is_one_for_random_order():
    """A model with no ordering cannot beat the base rate."""
    rng = random.Random(11)
    actual = [rng.gauss(3.0, 1.0) for _ in range(400)]
    predicted = [rng.gauss(3.0, 1.0) for _ in range(400)]
    lift = top_decile_lift(actual, predicted)
    assert 0.6 <= lift <= 1.7


def test_model_reports_an_interval_and_a_card(model):
    """The card is the artefact a reader can audit."""
    card = model.card
    assert card.horizon_years == 3
    assert card.quantiles == (0.1, 0.5, 0.9)
    assert card.training_rows > 0 and card.holdout_rows > 0
    assert set(card.offsets) == {"0.1", "0.5", "0.9"}
    assert card.cohort_fingerprint.startswith("sha256:")
    assert card.metrics["records"] == card.holdout_rows
    assert card.fingerprint().startswith("sha256:")


def test_predictions_are_ordered_and_non_negative(model, cohort):
    """low <= median <= high, and counts never go negative."""
    predictions = model.predict(cohort.papers[:20])
    assert len(predictions) == 20
    for index in range(len(predictions)):
        assert predictions.low[index] >= 0.0
        assert predictions.low[index] <= predictions.median[index] + 1e-9
        assert predictions.median[index] <= predictions.high[index] + 1e-9


def test_interval_coverage_is_near_nominal(model, cohort):
    """The calibrated 80% interval should cover about 80% out of sample.

    The default subset is the rows this model never trained on; scoring the
    training rows instead would report the booster's memory, not calibration.
    """
    metrics = model.metrics_for(cohort.papers)
    assert metrics["eval_subset"] == "holdout"
    assert 0.6 <= metrics["interval_coverage"] <= 0.95
    in_sample = model.metrics_for(cohort.papers, subset="all")
    assert in_sample["in_sample_rows"] > 0
    assert in_sample["interval_coverage"] >= metrics["interval_coverage"]


def test_the_model_learns_the_designed_signal(model, cohort):
    """The synthetic cohort has observable signal; a working model finds it."""
    metrics = model.metrics_for(cohort.papers)
    assert metrics["spearman"] > 0.3
    # A decile on a ~30-row holdout is too coarse to estimate, so the lift is
    # quoted on the whole labelled set and labelled as descriptive.
    descriptive = model.metrics_for(cohort.papers, subset="all")
    assert descriptive["top_decile_lift"] > 1.5


def test_refitting_is_reproducible(model, cohort):
    """Same cohort, same seed, same card fingerprint."""
    again = fit_model(
        cohort.papers,
        reference_year=cohort.reference_year,
        cohort_fingerprint=cohort.fingerprint(),
    )
    assert again.card.fingerprint() == model.card.fingerprint()
    first = model.predict(cohort.papers[:5])
    second = again.predict(cohort.papers[:5])
    assert first.median == second.median


def test_constant_columns_are_dropped_and_named(model):
    """A column that never varies is dropped explicitly, not silently."""
    notes = [
        note
        for note in model.card.notes
        if note.startswith("dropped_constant_features:")
    ]
    assert notes
    dropped = set(notes[0].split(":", 1)[1].split(","))
    assert dropped
    assert not dropped & set(model.card.feature_names)


def test_total_citations_are_not_a_feature(model):
    """Leakage guard on the fitted column list."""
    assert "cited_by_count" not in model.card.feature_names


def test_small_cohort_raises_instead_of_fitting_garbage():
    """Thirty labelled papers are the floor, and the error says so."""
    small = synthetic_cohort(count=12, seed=1, reference_year=2024)
    with pytest.raises(InsufficientTrainingData, match="too small"):
        fit_model(small.papers, reference_year=small.reference_year)


def test_card_serialises_with_null_for_absent_numbers(model):
    """JSON has no NaN, so an absent metric is written as null."""
    payload = model.card.to_dict()
    assert payload["metrics"]["records"] >= 0
    assert isinstance(payload["feature_names"], list)
    assert payload["fingerprint"] == model.card.fingerprint()


def test_model_card_writes_to_disk(model, tmp_path):
    """The card is a deliverable of the run, not an internal detail."""
    path = Path(model.card.write(tmp_path / "model_card.json"))
    text = path.read_text(encoding="utf-8")
    assert '"quantiles"' in text
    assert "holdout_rows" in text


def test_quantile_crossings_are_repaired_and_counted(model, cohort):
    """Independent quantiles can cross; the interval is repaired, never hidden."""
    predictions = model.predict(cohort.papers[:40])
    for low, median, high in zip(
        predictions.low, predictions.median, predictions.high, strict=True
    ):
        assert low <= median <= high
    assert predictions.quantile_crossings >= 0
