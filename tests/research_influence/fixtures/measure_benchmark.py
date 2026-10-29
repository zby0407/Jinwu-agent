"""Print the influence scoreboard as JSON, so the report cannot drift.

Run it from the repository root:

    MPLCONFIGDIR=/tmp/mpl .venv/bin/python tests/research_influence/fixtures/measure_benchmark.py

The numbers this prints are the ones quoted in
``docs/architecture/2026-10-29-research-influence-benchmark-report.md``.  They are
measured on a *synthetic* cohort whose generative structure is known, so they
validate the pipeline; they say nothing about real astrophysics.  The extraction
half does use a real PDF with a text layer, built the same way the test builds it.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jw.research_influence.bias import audit_bias
from jw.research_influence.cohort import synthetic_cohort
from jw.research_influence.explain import (
    permutation_importance,
    rank_stability,
)
from jw.research_influence.features import venue_tier
from jw.research_influence.model import cross_validate, fit_model
from jw.research_influence.workflow import (
    RunOptions,
    paper_from_upload,
    run_influence,
)

COUNT = 300
SEED = 5
REFERENCE_YEAR = 2024
HORIZON = 3


def _boost(paper, factor: float):
    history = tuple((year, int(count * factor)) for year, count in paper.counts_by_year)
    return replace(
        paper,
        counts_by_year=history,
        cited_by_count=int((paper.cited_by_count or 0) * factor),
    )


def _hide(paper):
    from jw.research_influence.contracts import AccessRoute

    return replace(
        paper,
        sections={},
        access_route=AccessRoute.METADATA_ONLY,
        fulltext_path="",
        fulltext_pages=0,
    )


def _pdf(directory: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Embed TrueType outlines rather than matplotlib's default Type-3 glyph
    # soup: real publishers do the former, and only the former has a text layer
    # that extracts cleanly enough to score against.
    matplotlib.rcParams["pdf.fonttype"] = 42
    target = directory / "benchmark.pdf"
    figure = plt.figure(figsize=(8.5, 11))
    figure.text(
        0.1,
        0.96,
        "Photometric calibration of nearby supernovae light curves",
        fontsize=13,
    )
    figure.text(0.1, 0.92, "Abstract")
    figure.text(
        0.1,
        0.88,
        "We present a homogeneous photometric calibration of 42 nearby Type Ia "
        "supernovae observed between 2018 and 2022.",
    )
    offset = 0.80
    for heading, body in (
        ("1. Introduction", "Type Ia supernovae remain the workhorse indicator."),
        ("2. Data and Methods", "Photometry was reduced with one zeropoint."),
        ("3. Results", "The Hubble diagram scatter falls to 0.11 mag."),
        ("4. Data availability", "All light curves are released on Zenodo."),
    ):
        figure.text(0.1, offset, heading)
        figure.text(0.1, offset - 0.035, body)
        offset -= 0.08
    figure.text(0.1, 0.40, "Photometry and scripts: https://zenodo.org/record/7654321")
    figure.text(0.1, 0.34, "References")
    offset = 0.30
    for entry in (
        "1. Riess, A. G. et al. 1998, AJ, 116, 1009, doi:10.1086/300499",
        "2. Perlmutter, S. et al. 1999, ApJ, 517, 565, doi:10.1086/307221",
        "3. Scolnic, D. et al. 2018, ApJ, 859, 101, doi:10.3847/1538-4357/aab9bb",
    ):
        figure.text(0.1, offset, entry)
        offset -= 0.03
    figure.savefig(target)
    plt.close(figure)
    return target


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="influence-bench-"))
    papers = list(
        synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR).papers
    )
    board: dict[str, object] = {
        "cohort": {
            "papers": len(papers),
            "seed": SEED,
            "reference_year": REFERENCE_YEAR,
            "horizon_years": HORIZON,
            "synthetic": True,
            "with_full_text": sum(1 for paper in papers if paper.has_full_text),
        }
    }

    started = time.perf_counter()
    model = fit_model(
        papers, reference_year=REFERENCE_YEAR, cohort_fingerprint="sha256:benchmark"
    )
    board["fit_seconds"] = round(time.perf_counter() - started, 3)
    board["model"] = {
        "training_rows": model.card.training_rows,
        "holdout_rows": model.card.holdout_rows,
        "features": len(model.card.feature_names),
        "offsets": dict(model.card.offsets),
    }
    board["holdout"] = _metrics_subset(model, papers, "holdout")
    board["descriptive"] = _metrics_subset(model, papers, "all")
    board["cross_validation"] = cross_validate(
        papers, reference_year=REFERENCE_YEAR, folds=4, repeats=2
    ).aggregate

    pdf = _pdf(workdir)
    uploaded = paper_from_upload(pdf, directory=workdir / "store")
    board["extraction"] = {
        "title": uploaded.title,
        "sections": sorted(uploaded.sections),
        "references": len(uploaded.references),
        "references_with_doi": sum(1 for item in uploaded.references if item.doi),
        "open_data": [record.url for record in uploaded.open_data],
        "provenance_fields": sorted(uploaded.provenance),
        "missing_fields": {key: value.value for key, value in uploaded.missing.items()},
        "cited_by_count": uploaded.cited_by_count,
    }

    importances = permutation_importance(model, papers, seeds=(0, 1, 2), repeats=2)
    board["importance_top"] = [
        {
            "feature": item.feature,
            "mean_drop": round(item.mean_drop, 4),
            "supported": item.supported,
        }
        for item in importances[:6]
    ]
    board["importance_supported"] = sum(1 for item in importances if item.supported)
    board["rank_stability"] = round(
        rank_stability([[item.feature for item in importances] for _ in range(2)]), 4
    )

    baseline = audit_bias(model, papers, importances=importances)
    board["bias_baseline"] = baseline.counts()

    injections = {
        "language_geography": [
            replace(_boost(paper, 4.0), language="zh") if index % 6 == 0 else paper
            for index, paper in enumerate(papers)
        ],
        "topic_heat": [
            replace(_boost(paper, 5.0), keywords=("hot topic",))
            if index % 5 == 0
            else paper
            for index, paper in enumerate(papers)
        ],
        "venue_prestige": [
            _boost(paper, 3.0)
            if (venue_tier(paper.venue) or 0) >= 3 and paper.venue
            else paper
            for paper in papers
        ],
        "affiliation_hierarchy": [
            replace(
                _boost(paper, 3.0),
                authors=tuple(
                    replace(author, institution="", country="")
                    for author in paper.authors
                ),
            )
            if index % 4 == 0
            else paper
            for index, paper in enumerate(papers)
        ],
        "open_access_visibility": [
            _boost(paper, 3.0) if paper.has_full_text else paper for paper in papers
        ],
        "missing_mechanism": [
            _hide(paper)
            if paper.paper_id
            in {
                item.paper_id
                for item in sorted(
                    papers, key=lambda p: p.cited_by_count or 0, reverse=True
                )[:120]
            }
            else paper
            for paper in papers
        ],
    }
    detected: dict[str, object] = {}
    for name, injected in injections.items():
        other = fit_model(injected, reference_year=REFERENCE_YEAR)
        found = audit_bias(other, injected)
        finding = next(item for item in found.findings if item.name == name)
        detected[name] = {
            "status": finding.status,
            "severity": finding.severity,
            "delta": (finding.evidence.get("difference") or {}).get("delta")
            if isinstance(finding.evidence.get("difference"), dict)
            else None,
            "absorbed": finding.evidence.get("absorbed_into_features"),
        }
    board["bias_injections"] = detected

    started = time.perf_counter()
    run = run_influence(
        workspace=workdir / "run",
        snapshot=synthetic_cohort(
            count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR
        ),
        uploads=(pdf,),
        options=RunOptions(
            reference_year=REFERENCE_YEAR,
            horizon=HORIZON,
            seed=3,
            max_subjects=3,
            importance_repeats=2,
            importance_seeds=(0, 1),
            bootstrap=40,
        ),
    )
    board["pipeline_seconds"] = round(time.perf_counter() - started, 3)
    board["pipeline"] = {
        "stages": sorted(run.stages),
        "explanations": len(run.explanations),
        "factors_per_explanation": [len(item.factors) for item in run.explanations],
        "package_files": sorted(Path(path).name for path in run.package.files),
        "determinism": run.metrics.get("determinism"),
        "fingerprint": run.metrics.get("fingerprint"),
        "package_fingerprint": run.metrics.get("package_fingerprint"),
    }
    print(json.dumps(board, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def _metrics_subset(model, papers, subset: str) -> dict[str, object]:
    metrics = model.metrics_for(papers, subset=subset)
    return {
        "records": metrics["records"],
        "in_sample_rows": metrics["in_sample_rows"],
        "spearman": round(metrics["spearman"], 4),
        "top_decile_lift": round(metrics["top_decile_lift"], 4),
        "interval_coverage": round(metrics["interval_coverage"], 4),
        "interval_nominal": metrics["interval_nominal"],
        "median_absolute_error_log": round(metrics["median_absolute_error_log"], 4),
    }


if __name__ == "__main__":
    raise SystemExit(main())
