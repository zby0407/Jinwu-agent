"""Every explanatory sentence must be backed by a number in the same object."""

from __future__ import annotations

import math

import pytest

from jw.research_influence.cohort import synthetic_cohort
from jw.research_influence.explain import (
    counterfactual,
    explain_paper,
    feature_labels,
    importance_rank,
    partial_dependence,
    permutation_importance,
    rank_stability,
    summarise_importance,
)
from jw.research_influence.features import FEATURES
from jw.research_influence.model import fit_model


@pytest.fixture(scope="module")
def cohort():
    return synthetic_cohort(count=150, seed=4, reference_year=2024)


@pytest.fixture(scope="module")
def model(cohort):
    return fit_model(
        cohort.papers,
        reference_year=cohort.reference_year,
        cohort_fingerprint=cohort.fingerprint(),
    )


@pytest.fixture(scope="module")
def explained(model, cohort):
    return explain_paper(model, cohort.papers[0], cohort=cohort.papers, top=5)


def test_explanation_has_an_interval_and_a_number_for_each_factor(explained):
    """No factor may be mentioned without a delta and a CI."""
    assert explained.low <= explained.median <= explained.high
    assert explained.factors
    for effect in explained.factors:
        assert math.isfinite(effect.delta_log)
        assert math.isfinite(effect.ci_low_log) and math.isfinite(effect.ci_high_log)
        assert effect.ci_low_log <= effect.delta_log <= effect.ci_high_log
        assert effect.ci_low <= effect.ci_high


def test_factor_sentences_avoid_double_negatives(explained):
    """A delta of -16 citations must read as "lowers by 16", not "by -16"."""
    for effect in explained.factors:
        sentence = effect.sentence()
        assert "by -" not in sentence
        assert "citations" in sentence


def test_factors_are_ranked_by_their_share(explained):
    """The ordering is the claim the report makes, so it must be monotone."""
    shares = [effect.share for effect in explained.factors]
    assert shares == sorted(shares, reverse=True)
    assert sum(shares) <= 1.0 + 1e-9


def test_explanation_is_reproducible(model, cohort, explained):
    """Same paper, same cohort, same seed: same numbers."""
    again = explain_paper(model, cohort.papers[0], cohort=cohort.papers, top=5)
    assert again.median == explained.median
    assert [effect.delta_log for effect in again.factors] == [
        effect.delta_log for effect in explained.factors
    ]


def test_summary_lines_state_the_interval_and_the_comparators(explained):
    """The reader-facing text mentions both, because both are load-bearing."""
    text = "\n".join(explained.summary_lines())
    assert "80% interval" in text
    assert "observed to date" in text
    if explained.comparators:
        assert "Closest cohort papers" in text


def test_comparators_are_the_nearest_papers(model, cohort):
    """Comparators exclude the paper itself and carry observed counts."""
    explained = explain_paper(
        model, cohort.papers[3], cohort=cohort.papers, neighbours=4
    )
    assert len(explained.comparators) == 4
    assert all(
        item.paper_id != cohort.papers[3].paper_id for item in explained.comparators
    )
    distances = [item.distance for item in explained.comparators]
    assert distances == sorted(distances)
    for item in explained.comparators:
        assert item.observed_citations >= 0
        assert item.predicted_low <= item.predicted_high


def test_metadata_only_paper_keeps_its_interval_but_says_why(model, cohort):
    """A paper without a PDF has text-level features absent, and that is stated."""
    thin = next(paper for paper in cohort.papers if not paper.has_full_text)
    explained = explain_paper(model, thin, cohort=cohort.papers, top=30)
    text_level = tuple(spec.name for spec in FEATURES if spec.requires_full_text)
    assert not [effect for effect in explained.factors if effect.feature in text_level]
    assert any("Full text was unavailable" in note for note in explained.notes)


def test_feature_labels_cover_the_model_columns(model):
    """A label table that misses a column would print a raw snake_case name."""
    labels = feature_labels()
    for name in model.card.feature_names:
        assert name in labels


def test_permutation_importance_is_ranked_and_reproducible(model, cohort):
    """Importance is stable when the seeds are, and loss is non-negative on top."""
    first = permutation_importance(model, cohort.papers)
    second = permutation_importance(model, cohort.papers)
    assert first
    assert [item.rank for item in first] == list(range(1, len(first) + 1))
    assert [item.feature for item in first] == [item.feature for item in second]
    top = first[0]
    assert top.mean_drop > 0
    assert top.ci_low <= top.mean_drop <= top.ci_high
    assert any(item.supported for item in first)


def test_importance_lines_admit_what_is_noise(model, cohort):
    """The report must be able to say "not distinguishable from noise"."""
    importances = permutation_importance(model, cohort.papers)
    lines = summarise_importance(importances, top=3)
    assert "of" in lines[0] and "features carry ranking signal" in lines[0]
    assert len(lines) >= 2


def test_rank_stability_of_identical_and_reversed_rankings():
    """The stability metric is a real correlation, so it must hit both ends."""
    order = ["a", "b", "c", "d"]
    assert rank_stability([order, list(order)]) == pytest.approx(1.0)
    assert rank_stability([order, ["d", "c", "b", "a"]]) == pytest.approx(-1.0)


def test_importance_rank_returns_the_feature_order(model, cohort):
    """Ranking is exposed as names so it can be compared across seeds."""
    importances = permutation_importance(model, cohort.papers, seeds=(0, 1, 2))
    names = importance_rank(importances)
    assert names == [item.feature for item in importances]
    assert len(set(names)) == len(names)


def test_partial_dependence_sweeps_the_column(model, cohort):
    """A PDP is a grid of values with predictions, not a single number."""
    curve = partial_dependence(model, cohort.papers, "author_count", steps=4)
    assert len(curve) == 4
    values = [point[0] for point in curve]
    assert values == sorted(values)
    assert len({round(point[1], 9) for point in curve}) > 1


def test_partial_dependence_ignores_unknown_feature(model, cohort):
    """An unknown column returns nothing rather than a fabricated curve."""
    assert partial_dependence(model, cohort.papers, "not_a_feature") == ()


def test_counterfactual_moves_only_the_named_feature(model, cohort):
    """Raising author count must not change the rest of the row."""
    paper = cohort.papers[2]
    curve = counterfactual(model, paper, feature="author_count", values=[1.0, 40.0])
    assert len(curve) == 2
    assert curve[0][0] == 1.0 and curve[1][0] == 40.0
    assert curve[0][2] != curve[1][2]
