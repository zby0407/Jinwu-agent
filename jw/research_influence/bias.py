"""Bias audits: name the group, the direction, the size and the evidence.

Nine audits are implemented here. Each one answers the same four questions:
which papers are grouped together, what is measured, how large the difference is
(with an interval), and what cannot be concluded. Two of them (self-citation,
author demographics) deliberately answer "cannot assess" - a report that claims
to measure what the snapshot does not contain is worse than one that says so.

Residual convention: ``residual = observed - predicted`` in ``log1p`` space, so a
positive residual means the model *under-predicts* that group.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import pairwise
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import PaperRecord, canonical_json, content_hash
from .explain import FeatureImportance
from .features import FEATURES, citations_within, targets, venue_tier
from .model import InfluenceModel, spearman

__all__ = [
    "AUDITS",
    "AUDIT_LABELS",
    "BiasFinding",
    "BiasReport",
    "audit_bias",
]

DETECTION_THRESHOLD = 0.15
#: Predicted gap (log units) above which an access route is already priced into
#: the model; the residual test then cannot clear it.
ABSORBED_GAP_THRESHOLD = 0.30
HIGH_SEVERITY = 0.60
MEDIUM_SEVERITY = 0.30
MIN_GROUP_SIZE = 6
TEXT_LEVEL = tuple(spec.name for spec in FEATURES if spec.requires_full_text)

AUDITS: tuple[str, ...] = (
    "venue_prestige",
    "affiliation_hierarchy",
    "language_geography",
    "self_citation",
    "topic_heat",
    "time_censoring",
    "open_access_visibility",
    "open_data_bonus",
    "missing_mechanism",
    "author_demographics",
)

AUDIT_LABELS: Mapping[str, str] = {
    "venue_prestige": "Journal prestige",
    "affiliation_hierarchy": "Institution hierarchy",
    "language_geography": "Language and geography",
    "self_citation": "Self-citation inflation",
    "topic_heat": "Topic popularity",
    "time_censoring": "Right-censored recency",
    "open_access_visibility": "Open-access visibility",
    "open_data_bonus": "Open-data bonus",
    "missing_mechanism": "Missingness (MNAR)",
    "author_demographics": "Author demographics (not attempted)",
}


@dataclass(slots=True)
class BiasFinding:
    """One audited pathway, with its numbers and its limits."""

    name: str
    label: str
    status: str
    severity: str
    method: str
    evidence: dict[str, Any] = field(default_factory=dict)
    conclusion: str = ""
    recommendation: str = ""

    @property
    def detected(self) -> bool:
        """Whether the audit found a systematic difference it will defend."""
        return self.status == "detected"

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the report and the JSON artefacts."""
        return {
            "audit": self.name,
            "label": self.label,
            "status": self.status,
            "severity": self.severity,
            "method": self.method,
            "evidence": self.evidence,
            "conclusion": self.conclusion,
            "recommendation": self.recommendation,
        }

    def sentence(self) -> str:
        """One report line: what, how big, how sure."""
        return f"{self.label} [{self.status}/{self.severity}]: {self.conclusion}"


@dataclass(slots=True)
class BiasReport:
    """All audits for one cohort and one model, plus the cohort's own shape."""

    findings: tuple[BiasFinding, ...]
    cohort: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def by_status(self, status: str) -> tuple[BiasFinding, ...]:
        """Findings in a given status bucket."""
        return tuple(item for item in self.findings if item.status == status)

    @property
    def detected(self) -> tuple[BiasFinding, ...]:
        """Findings the report is willing to call systematic."""
        return self.by_status("detected")

    @property
    def unassessable(self) -> tuple[BiasFinding, ...]:
        """Findings that cannot be evaluated with this snapshot."""
        return self.by_status("cannot_assess")

    def counts(self) -> dict[str, Any]:
        """Status, severity and audit counts for the summary table."""
        return {
            "audits": len(self.findings),
            "by_status": dict(Counter(item.status for item in self.findings)),
            "by_severity": dict(Counter(item.severity for item in self.findings)),
            "detected": len(self.detected),
            "cannot_assess": len(self.unassessable),
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the package and the JSON artefacts."""
        return {
            "cohort": self.cohort,
            "counts": self.counts(),
            "findings": [item.to_dict() for item in self.findings],
            "notes": list(self.notes),
        }

    def fingerprint(self) -> str:
        """Content hash so a report can be compared across runs."""
        return content_hash(self.to_dict())

    def summary_lines(self) -> tuple[str, ...]:
        """The audit section of the human report."""
        counts = self.counts()
        lines = [
            f"{counts['audits']} audits: {counts['detected']} detected, "
            f"{counts['cannot_assess']} cannot be assessed with this snapshot",
        ]
        lines.extend(item.sentence() for item in self.findings)
        lines.extend(self.notes)
        return tuple(lines)

    def to_markdown(self) -> str:
        """A table plus the method and the recommendation for each audit."""
        counts = self.counts()
        lines = [
            "## Bias audit",
            "",
            f"- audits: {counts['audits']}",
            f"- detected: {counts['detected']}",
            f"- cannot be assessed: {counts['cannot_assess']}",
            "",
            "| audit | status | severity | conclusion |",
            "| --- | --- | --- | --- |",
        ]
        for item in self.findings:
            conclusion = item.conclusion.replace("|", "/")
            lines.append(
                f"| {item.label} | {item.status} | {item.severity} | {conclusion} |"
            )
        lines.append("")
        for item in self.findings:
            lines.append(f"### {item.label}")
            lines.append("")
            lines.append(f"- status: {item.status} (severity {item.severity})")
            lines.append(f"- method: {item.method}")
            if item.evidence:
                lines.append(f"- evidence: `{canonical_json(item.evidence)}`")
            lines.append(f"- conclusion: {item.conclusion}")
            if item.recommendation:
                lines.append(f"- recommendation: {item.recommendation}")
            lines.append("")
        return "\n".join(lines)


@dataclass(slots=True)
class _Context:
    papers: list[PaperRecord]
    predicted: dict[str, float]
    actual: dict[str, float]
    available: set[str]
    censored: set[str]
    importances: tuple[FeatureImportance, ...]
    reference_year: int
    horizon: int

    def residuals(self) -> list[tuple[PaperRecord, float]]:
        return [
            (paper, self.actual[paper.paper_id] - self.predicted[paper.paper_id])
            for paper in self.papers
            if paper.paper_id in self.available and paper.paper_id in self.predicted
        ]

    def route_share(self, routes: Iterable[str]) -> float:
        wanted = set(routes)
        total = sum(max(0.0, item.mean_drop) for item in self.importances)
        if total <= 0:
            return 0.0
        chosen = sum(
            max(0.0, item.mean_drop)
            for item in self.importances
            if item.route in wanted
        )
        return chosen / total

    def feature_importance(self, feature: str) -> float | None:
        for item in self.importances:
            if item.feature == feature:
                return item.mean_drop
        return None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def _sd(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    centre = _mean(values)
    return math.sqrt(sum((value - centre) ** 2 for value in values) / (len(values) - 1))


def _group_stats(
    pairs: Sequence[tuple[PaperRecord, float]],
    key: Callable[[PaperRecord], str],
) -> dict[str, dict[str, float]]:
    buckets: dict[str, list[float]] = {}
    for paper, residual in pairs:
        buckets.setdefault(key(paper) or "unknown", []).append(residual)
    stats: dict[str, dict[str, float]] = {}
    for name, values in buckets.items():
        centre = _mean(values)
        spread = _sd(values)
        margin = 1.96 * spread / math.sqrt(len(values)) if len(values) > 1 else 0.0
        stats[name] = {
            "n": len(values),
            "mean": centre,
            "ci_low": centre - margin,
            "ci_high": centre + margin,
            "sd": spread,
        }
    return stats


def _difference(
    left: Mapping[str, float], right: Mapping[str, float]
) -> dict[str, float]:
    delta = left["mean"] - right["mean"]
    se = math.sqrt(
        (left.get("sd", 0.0) ** 2) / max(1.0, left["n"])
        + (right.get("sd", 0.0) ** 2) / max(1.0, right["n"])
    )
    margin = 1.96 * se
    return {"delta": delta, "ci_low": delta - margin, "ci_high": delta + margin}


def _verdict(
    difference: Mapping[str, float] | None,
    *,
    small: int,
    threshold: float = DETECTION_THRESHOLD,
) -> str:
    if difference is None:
        return "insufficient_evidence"
    if small < MIN_GROUP_SIZE:
        return "insufficient_evidence"
    delta = difference["delta"]
    excludes_zero = difference["ci_low"] > 0 or difference["ci_high"] < 0
    if abs(delta) >= threshold and excludes_zero:
        return "detected"
    if abs(delta) >= threshold:
        return "insufficient_evidence"
    return "not_detected"


def _severity(status: str, difference: Mapping[str, float] | None) -> str:
    if status != "detected" or difference is None:
        return "none"
    magnitude = abs(difference["delta"])
    if magnitude >= HIGH_SEVERITY:
        return "high"
    if magnitude >= MEDIUM_SEVERITY:
        return "medium"
    return "low"


def _verb(delta: float) -> str:
    return "under-predicts" if delta > 0 else "over-predicts"


def _participle(delta: float) -> str:
    return "under-predicted" if delta > 0 else "over-predicted"


def _round(mapping: Mapping[str, float], digits: int = 3) -> dict[str, float]:
    return {key: round(float(value), digits) for key, value in mapping.items()}


def _tier_label(paper: PaperRecord) -> str:
    tier = venue_tier(paper.venue) if paper.venue else None
    return f"tier_{tier}" if tier is not None else "tier_unknown"


def _audit_venue_prestige(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    stats = _group_stats(pairs, _tier_label)
    lower = [name for name in stats if name in {"tier_0", "tier_1", "tier_2"}]
    higher = [name for name in stats if name in {"tier_3", "tier_4"}]
    if not lower or not higher:
        return BiasFinding(
            name="venue_prestige",
            label=AUDIT_LABELS["venue_prestige"],
            status="insufficient_evidence",
            severity="none",
            method="Grouped residual means by documented venue tier",
            evidence={"groups": _round_keys(stats)},
            conclusion=(
                "The cohort does not contain both a lower-tier and a higher-tier "
                "group large enough to compare."
            ),
            recommendation="Add papers from more venue tiers before claiming anything.",
        )
    combined_low = {
        "n": sum(stats[name]["n"] for name in lower),
        "mean": sum(stats[name]["mean"] * stats[name]["n"] for name in lower)
        / max(1.0, sum(stats[name]["n"] for name in lower)),
        "sd": max(stats[name]["sd"] for name in lower),
    }
    combined_high = {
        "n": sum(stats[name]["n"] for name in higher),
        "mean": sum(stats[name]["mean"] * stats[name]["n"] for name in higher)
        / max(1.0, sum(stats[name]["n"] for name in higher)),
        "sd": max(stats[name]["sd"] for name in higher),
    }
    difference = _difference(combined_high, combined_low)
    status = _verdict(difference, small=int(min(combined_low["n"], combined_high["n"])))
    share = context.route_share({"venue_prestige"})
    return BiasFinding(
        name="venue_prestige",
        label=AUDIT_LABELS["venue_prestige"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Mean residual (observed - predicted, log1p) for higher-tier venues minus "
            "lower-tier venues, with a normal-approximation 95% CI; model reliance "
            "measured as the share of permutation-importance mass on venue features"
        ),
        evidence={
            "groups": _round_keys(stats),
            "difference": _round(difference),
            "venue_route_importance_share": round(share, 4),
        },
        conclusion=(
            f"Across {combined_low['n'] + combined_high['n']} labelled papers the model "
            f"{_verb(difference['delta'])} higher-tier venues by "
            f"{abs(difference['delta']):.2f} log units "
            f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}); "
            f"venue features carry {share:.0%} of the model's importance."
            if status in {"detected", "not_detected"}
            else (
                f"The point estimate is {difference['delta']:.2f} log units but the "
                f"95% CI ({difference['ci_low']:.2f} to {difference['ci_high']:.2f}) "
                "does not separate the groups."
            )
        ),
        recommendation=(
            "Report prestige-stratified metrics next to the global ones, and never "
            "present a single ranking across venues without naming the tier rule."
        ),
    )


def _round_keys(stats: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    return {name: _round(values) for name, values in stats.items()}


def _audit_affiliation_hierarchy(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    stats = _group_stats(
        pairs,
        lambda paper: (
            "institution_known"
            if paper.authors and paper.authors[0].institution
            else "institution_unknown"
        ),
    )
    if "institution_known" not in stats or "institution_unknown" not in stats:
        return BiasFinding(
            name="affiliation_hierarchy",
            label=AUDIT_LABELS["affiliation_hierarchy"],
            status="cannot_assess",
            severity="none",
            method="Residual comparison between papers with and without known affiliations",
            evidence={"groups": _round_keys(stats)},
            conclusion=(
                "Every paper in this snapshot falls on the same side of affiliation "
                "coverage, so no comparison is possible."
            ),
            recommendation=(
                "Harvest affiliation data from OpenAlex or Crossref before claiming "
                "anything about institutional hierarchy."
            ),
        )
    difference = _difference(stats["institution_known"], stats["institution_unknown"])
    status = _verdict(
        difference,
        small=int(
            min(stats["institution_known"]["n"], stats["institution_unknown"]["n"])
        ),
    )
    share = context.route_share({"affiliation"})
    return BiasFinding(
        name="affiliation_hierarchy",
        label=AUDIT_LABELS["affiliation_hierarchy"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Mean residual for papers with a known first-author institution minus "
            "papers without, plus the importance share of affiliation features"
        ),
        evidence={
            "groups": _round_keys(stats),
            "difference": _round(difference),
            "affiliation_route_importance_share": round(share, 4),
            "institution_count_importance": _maybe(
                context.feature_importance("institution_count")
            ),
        },
        conclusion=(
            f"Papers with a known first-author institution are {_participle(difference['delta'])} "
            f"by {abs(difference['delta']):.2f} log units "
            f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}); "
            f"affiliation features carry {share:.0%} of importance."
        ),
        recommendation=(
            "Prestigious-institution effects are confounded with data coverage: a paper "
            "with missing affiliation is easier to under-predict, so impute the coverage "
            "flag as an explicit feature rather than filling institutions."
        ),
    )


def _maybe(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _audit_language_geography(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    language_stats = _group_stats(
        pairs, lambda paper: (paper.language or "unknown").lower()
    )
    countries = Counter(
        paper.authors[0].country or "unknown"
        for paper in context.papers
        if paper.authors
    )
    first_author_stats = _group_stats(
        pairs,
        lambda paper: (
            (paper.authors[0].country or "unknown") if paper.authors else "unknown"
        ),
    )
    english = language_stats.get("en")
    others = [
        values
        for name, values in language_stats.items()
        if name not in {"en", "unknown"}
    ]
    non_english_papers = sum(values["n"] for values in others)
    difference: dict[str, float] | None = None
    if english and non_english_papers >= MIN_GROUP_SIZE:
        combined = {
            "n": non_english_papers,
            "mean": sum(values["mean"] * values["n"] for values in others)
            / non_english_papers,
            "sd": max(values["sd"] for values in others),
        }
        difference = _difference(combined, english)
    if difference is None:
        status = "insufficient_evidence"
    else:
        status = _verdict(difference, small=int(min(english["n"], non_english_papers)))
    top_countries = countries.most_common(5)
    underrepresented = [
        name
        for name, count in countries.items()
        if count <= max(2, len(context.papers) // 50)
    ]
    return BiasFinding(
        name="language_geography",
        label=AUDIT_LABELS["language_geography"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Residual means by document language and by first-author country; coverage "
            "reported as counts per country so a thin cohort is visible"
        ),
        evidence={
            "languages": _round_keys(language_stats),
            "first_author_countries": _round_keys(first_author_stats),
            "top_countries": top_countries,
            "countries_with_two_or_fewer_papers": len(underrepresented),
            "non_english_papers": non_english_papers,
            "difference": _round(difference) if difference else None,
        },
        conclusion=(
            (
                f"Non-English papers are {_participle(difference['delta'])} by "
                f"{abs(difference['delta']):.2f} log units "
                f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}) "
                f"across {non_english_papers} papers; "
            )
            if difference
            else "The snapshot has too few non-English papers to compare groups; "
        )
        + (
            f"{len(underrepresented)} first-author countries contribute two papers or "
            "fewer, so any country-level statement would rest on single papers."
        ),
        recommendation=(
            "Because the indexed literature is English-heavy by construction, treat "
            "language and country results as coverage statements, not as measures of "
            "scientific quality, and never rank countries from this cohort."
        ),
    )


def _audit_self_citation(context: _Context) -> BiasFinding:
    return BiasFinding(
        name="self_citation",
        label=AUDIT_LABELS["self_citation"],
        status="cannot_assess",
        severity="none",
        method=(
            "Requires per-reference author identities and their citation counts; the "
            "snapshot only carries aggregate cited_by_count per paper"
        ),
        evidence={
            "cited_by_count_available": True,
            "referenced_works_available": any(
                paper.reference_count for paper in context.papers
            ),
            "self_citation_identifiable": False,
        },
        conclusion=(
            "Self-citation inflation cannot be measured here: separating an author's "
            "own citations from external ones needs author-level reference matching, "
            "which this snapshot does not contain."
        ),
        recommendation=(
            "Add OpenAlex referenced_works plus author disambiguation, then compare "
            "self-citation share between groups before and after removing it."
        ),
    )


def _audit_topic_heat(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    topic_counts = Counter(
        paper.keywords[0].lower() for paper in context.papers if paper.keywords
    )
    if not topic_counts:
        return BiasFinding(
            name="topic_heat",
            label=AUDIT_LABELS["topic_heat"],
            status="cannot_assess",
            severity="none",
            method="Residual comparison between hot and cold topics",
            evidence={"topics": 0},
            conclusion="No topic keywords are indexed for this cohort; topic heat cannot be audited.",
            recommendation="Enable the topics field in the provider select before rerunning.",
        )
    frequencies = sorted(topic_counts.values())
    marker = frequencies[int(0.75 * (len(frequencies) - 1))]
    hot = {name for name, count in topic_counts.items() if count >= max(3, marker)}
    if len(hot) >= len(topic_counts):
        return BiasFinding(
            name="topic_heat",
            label=AUDIT_LABELS["topic_heat"],
            status="cannot_assess",
            severity="none",
            method="Residual comparison between hot and cold topics",
            evidence={
                "topics": len(topic_counts),
                "frequencies": frequencies,
                "hot_topics": sorted(hot),
            },
            conclusion=(
                "Every topic in this cohort occurs at a similar rate, so there is no hot "
                "group to compare against a cold one."
            ),
            recommendation=(
                "Enlarge or diversify the cohort before drawing topic-level conclusions."
            ),
        )
    stats = _group_stats(
        pairs,
        lambda paper: (
            "hot" if (paper.keywords and paper.keywords[0].lower() in hot) else "rest"
        ),
    )
    difference = (
        _difference(stats["hot"], stats["rest"])
        if {"hot", "rest"} <= set(stats)
        else None
    )
    status = (
        _verdict(difference, small=int(min(stats["hot"]["n"], stats["rest"]["n"])))
        if difference
        else "insufficient_evidence"
    )
    share = context.route_share({"topic"})
    return BiasFinding(
        name="topic_heat",
        label=AUDIT_LABELS["topic_heat"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Topics appearing at least twice the median frequency are labelled hot; "
            "residual difference between hot-topic and other papers is reported with "
            "the importance share of topic features"
        ),
        evidence={
            "hot_topics": sorted(hot),
            "groups": _round_keys(stats),
            "difference": _round(difference) if difference else None,
            "topic_route_importance_share": round(share, 4),
            "hot_threshold": max(3, marker),
        },
        conclusion=(
            (
                f"Papers on hot topics are {_participle(difference['delta'])} by "
                f"{abs(difference['delta']):.2f} log units "
                f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}); "
            )
            if difference
            else "The cohort does not separate hot and cold topics cleanly; "
        )
        + (
            "citation counts therefore track audience size as well as quality, and the "
            "topic distribution of this cohort is not the field's topic distribution."
        ),
        recommendation=(
            "Report topic-stratified percentiles alongside global ones; otherwise a "
            "crowded subfield will look uniformly excellent."
        ),
    )


def _audit_time_censoring(context: _Context) -> BiasFinding:
    total = len(context.papers)
    censored = len(context.censored)
    recent_year = context.reference_year - context.horizon
    labelled_recent = [
        paper
        for paper in context.papers
        if paper.paper_id in context.available and (paper.year or 0) >= recent_year
    ]
    labelled_older = [
        paper
        for paper in context.papers
        if paper.paper_id in context.available and (paper.year or 0) < recent_year
    ]
    pairs = {paper.paper_id: residual for paper, residual in context.residuals()}
    recent_stats = {
        "n": len(labelled_recent),
        "mean": _mean([pairs[paper.paper_id] for paper in labelled_recent]),
        "sd": _sd([pairs[paper.paper_id] for paper in labelled_recent]),
    }
    older_stats = {
        "n": len(labelled_older),
        "mean": _mean([pairs[paper.paper_id] for paper in labelled_older]),
        "sd": _sd([pairs[paper.paper_id] for paper in labelled_older]),
    }
    difference = (
        _difference(recent_stats, older_stats)
        if recent_stats["n"] and older_stats["n"]
        else None
    )
    share = censored / total if total else 0.0
    status = _verdict(difference, small=int(min(recent_stats["n"], older_stats["n"])))
    if share >= 0.20 and status == "not_detected":
        status = "insufficient_evidence"
    return BiasFinding(
        name="time_censoring",
        label=AUDIT_LABELS["time_censoring"],
        status=status if censored else "not_detected",
        severity=_severity(status, difference),
        method=(
            "Papers inside the three-year horizon cannot have their full citation "
            "window observed; they are excluded from training and the residual "
            "difference between recent and older labelled papers is reported"
        ),
        evidence={
            "papers": total,
            "censored": censored,
            "censored_share": round(share, 4),
            "recent": _round(recent_stats),
            "older": _round(older_stats),
            "difference": _round(difference) if difference else None,
            "horizon_years": context.horizon,
        },
        conclusion=(
            f"{censored} of {total} papers ({share:.0%}) are right-censored and were "
            "excluded from training rather than treated as low-impact"
            + (
                f"; among labelled papers the recent group is "
                f"{_participle(difference['delta'])} by {abs(difference['delta']):.2f} "
                f"log units (95% CI {difference['ci_low']:.2f} to "
                f"{difference['ci_high']:.2f})"
                if difference
                else "; the labelled groups are too small to compare"
            )
            + "."
        ),
        recommendation=(
            "Never read a low prediction for a recent paper as low impact: the window "
            "has not elapsed. Report the censoring share next to every recent result."
        ),
    )


def _audit_open_access_visibility(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    stats = _group_stats(
        pairs, lambda paper: "full_text" if paper.has_full_text else "abstract_only"
    )
    difference = (
        _difference(stats["full_text"], stats["abstract_only"])
        if {"full_text", "abstract_only"} <= set(stats)
        else None
    )
    status = (
        _verdict(
            difference,
            small=int(min(stats["full_text"]["n"], stats["abstract_only"]["n"])),
        )
        if difference
        else "insufficient_evidence"
    )
    predictions = {
        "full_text": _mean(
            [
                context.predicted[paper.paper_id]
                for paper in context.papers
                if paper.has_full_text
            ]
        ),
        "abstract_only": _mean(
            [
                context.predicted[paper.paper_id]
                for paper in context.papers
                if not paper.has_full_text
            ]
        ),
    }
    share = context.route_share({"open_access"})
    predicted_gap = (
        predictions["full_text"] - predictions["abstract_only"]
        if predictions["full_text"] is not None
        and predictions["abstract_only"] is not None
        else math.nan
    )
    # A visibility advantage can be *absorbed*: if the model can already tell the
    # two groups apart, it learns the gap and the residuals look clean.  That is
    # not evidence of fairness, so the audit refuses to call it "not detected"
    # when the model itself predicts a large gap between the two groups.
    absorbed = (
        status == "not_detected"
        and not math.isnan(predicted_gap)
        and abs(predicted_gap) >= ABSORBED_GAP_THRESHOLD
    )
    if absorbed:
        status = "insufficient_evidence"
    return BiasFinding(
        name="open_access_visibility",
        label=AUDIT_LABELS["open_access_visibility"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Papers with a parsed full text are compared with abstract-only papers on "
            "residual and on predicted level; the importance share of open-access and "
            "text-level features quantifies model reliance"
        ),
        evidence={
            "groups": _round_keys(stats),
            "difference": _round(difference) if difference else None,
            "mean_predicted_log_full_text": _maybe(predictions["full_text"]),
            "mean_predicted_log_abstract_only": _maybe(predictions["abstract_only"]),
            "open_access_route_importance_share": round(share, 4),
            "predicted_gap_log": round(predicted_gap, 4)
            if not math.isnan(predicted_gap)
            else None,
            "absorbed_into_features": absorbed,
        },
        conclusion=(
            (
                f"Abstract-only papers are {_participle(difference['delta'])} by "
                f"{abs(difference['delta']):.2f} log units "
                f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}); "
            )
            if difference
            else "Both access groups are present but too small to compare; "
        )
        + (
            f"open-access and text-level features carry {share:.0%} of importance, so "
            "paywalled papers are scored from fewer signals."
        ),
        recommendation=(
            "Treat full-text availability as a coverage property of the pipeline, not of "
            "the paper, and report intervals conditional on access route."
        ),
    )


def _open_data_group(paper: PaperRecord) -> str:
    if not paper.has_full_text:
        return "no_full_text"
    shared = any(record.kind.value in {"data", "code"} for record in paper.open_data)
    return "open_data" if shared else "closed_data"


def _audit_open_data_bonus(context: _Context) -> BiasFinding:
    pairs = context.residuals()
    with_text = [paper for paper in context.papers if paper.has_full_text]
    if len(with_text) < MIN_GROUP_SIZE * 2:
        return BiasFinding(
            name="open_data_bonus",
            label=AUDIT_LABELS["open_data_bonus"],
            status="cannot_assess",
            severity="none",
            method="Residual comparison between papers with and without open data",
            evidence={"papers_with_full_text": len(with_text)},
            conclusion=(
                "Open-data statements are only observable in full text, and this "
                "snapshot has too few parsed full texts to compare groups."
            ),
            recommendation="Parse more PDFs before auditing the open-data bonus.",
        )
    stats = _group_stats(pairs, _open_data_group)
    usable = {name: values for name, values in stats.items() if name != "no_full_text"}
    difference = (
        _difference(usable["open_data"], usable["closed_data"])
        if {"open_data", "closed_data"} <= set(stats)
        else None
    )
    status = (
        _verdict(
            difference,
            small=int(min(usable["open_data"]["n"], usable["closed_data"]["n"])),
        )
        if difference
        else "insufficient_evidence"
    )
    share = context.route_share({"open_data"})
    return BiasFinding(
        name="open_data_bonus",
        label=AUDIT_LABELS["open_data_bonus"],
        status=status,
        severity=_severity(status, difference),
        method=(
            "Among full-text papers, those whose data availability statement links a "
            "repository are compared with those without; the importance share of "
            "open-data features shows whether the model pays for the statement"
        ),
        evidence={
            "groups": _round_keys(stats),
            "difference": _round(difference) if difference else None,
            "open_data_route_importance_share": round(share, 4),
        },
        conclusion=(
            (
                f"Papers that publish data are {_participle(difference['delta'])} by "
                f"{abs(difference['delta']):.2f} log units "
                f"(95% CI {difference['ci_low']:.2f} to {difference['ci_high']:.2f}); "
            )
            if difference
            else "The full-text subset does not separate data-sharing papers cleanly; "
        )
        + (
            f"open-data features carry {share:.0%} of importance, so the bonus (if any) "
            "is mostly a statement-detection effect rather than a measurable citation gain."
        ),
        recommendation=(
            "Do not reward the presence of a statement without checking that the "
            "repository actually resolves; broken links are common and would otherwise "
            "inflate the score."
        ),
    )


def _audit_missing_mechanism(context: _Context) -> BiasFinding:
    ordered = sorted(
        (paper for paper in context.papers if paper.paper_id in context.predicted),
        key=lambda paper: context.predicted[paper.paper_id],
    )
    if len(ordered) < 20:
        return BiasFinding(
            name="missing_mechanism",
            label=AUDIT_LABELS["missing_mechanism"],
            status="insufficient_evidence",
            severity="none",
            method="Missing-share of text-level features by predicted decile",
            evidence={"papers": len(ordered)},
            conclusion="Too few papers to bucket by predicted decile.",
            recommendation="Enlarge the cohort before testing the missingness mechanism.",
        )
    buckets: list[dict[str, float]] = []
    size = max(1, len(ordered) // 5)
    for start in range(0, len(ordered), size):
        chunk = ordered[start : start + size]
        if not chunk:
            continue
        missing = sum(1 for paper in chunk if not paper.has_full_text) / len(chunk)
        buckets.append(
            {
                "positions": len(chunk),
                "missing_share": round(missing, 4),
                "mean_predicted_log": round(
                    _mean([context.predicted[paper.paper_id] for paper in chunk]), 4
                ),
            }
        )
    spread = buckets[-1]["missing_share"] - buckets[0]["missing_share"]
    monotone = all(
        later["missing_share"] >= earlier["missing_share"] - 0.05
        for earlier, later in pairwise(buckets)
    )
    # Five buckets of ~60 papers each: a single dip is sampling noise, so the
    # verdict uses the rank trend as well as strict monotonicity rather than
    # discarding a large, obviously increasing profile as "insufficient".
    trend = spearman(
        [float(index) for index in range(len(buckets))],
        [bucket["missing_share"] for bucket in buckets],
    )
    if not math.isnan(trend) and math.isnan(spread):
        trend = math.nan
    status = (
        "detected"
        if spread >= 0.20 and (monotone or (not math.isnan(trend) and trend >= 0.80))
        else "not_detected"
        if abs(spread) < 0.10
        else "insufficient_evidence"
    )
    severity = (
        ("high" if spread >= 0.40 else "medium" if spread >= 0.20 else "none")
        if status == "detected"
        else "none"
    )
    return BiasFinding(
        name="missing_mechanism",
        label=AUDIT_LABELS["missing_mechanism"],
        status=status,
        severity=severity,
        method=(
            "Papers are ordered by predicted impact and split into five buckets; the "
            "share without a parsed full text is reported per bucket, which tests "
            "whether data are missing at random (MAR) or tied to the outcome (MNAR)"
        ),
        evidence={
            "buckets": buckets,
            "missing_trend": None if math.isnan(trend) else round(trend, 4),
            "strictly_monotone": monotone,
            "spread_bottom_to_top": round(spread, 4),
            "monotone": monotone,
            "text_level_features": list(TEXT_LEVEL),
        },
        conclusion=(
            f"Full-text coverage differs by {abs(spread):.0%} between the lowest and "
            f"highest predicted bucket ({'increasing' if spread > 0 else 'decreasing'} "
            f"with predicted impact), so the text-level features are "
            f"{'not' if status == 'detected' else 'roughly'} missing at random."
        ),
        recommendation=(
            "When a whole feature family is missing for exactly the papers the model "
            "already scores low, the interval - not the point estimate - is the honest "
            "output; report per-bucket coverage next to every aggregate metric."
        ),
    )


def _audit_author_demographics(context: _Context) -> BiasFinding:
    return BiasFinding(
        name="author_demographics",
        label=AUDIT_LABELS["author_demographics"],
        status="cannot_assess",
        severity="none",
        method=(
            "Deliberately not attempted: gender, ethnicity or career age would have to "
            "be inferred from names or affiliations"
        ),
        evidence={
            "attempted": False,
            "reason": "name-based demographic inference is unreliable and would create the "
            "bias it claims to measure",
        },
        conclusion=(
            "No demographic inference is performed. Name-based gender or ethnicity "
            "classifiers have error rates far larger than the effects being audited, and "
            "publishing such a number would launder an unreliable estimate as evidence."
        ),
        recommendation=(
            "If a funder requires demographic analysis, collect self-reported or "
            "authority-provided categories under an ethics protocol; do not infer them "
            "from names."
        ),
    )


_AUDIT_FUNCTIONS: Mapping[str, Callable[[_Context], BiasFinding]] = {
    "venue_prestige": _audit_venue_prestige,
    "affiliation_hierarchy": _audit_affiliation_hierarchy,
    "language_geography": _audit_language_geography,
    "self_citation": _audit_self_citation,
    "topic_heat": _audit_topic_heat,
    "time_censoring": _audit_time_censoring,
    "open_access_visibility": _audit_open_access_visibility,
    "open_data_bonus": _audit_open_data_bonus,
    "missing_mechanism": _audit_missing_mechanism,
    "author_demographics": _audit_author_demographics,
}


def audit_bias(
    model: InfluenceModel,
    papers: Sequence[PaperRecord],
    *,
    importances: Sequence[FeatureImportance] = (),
    audits: Sequence[str] = AUDITS,
) -> BiasReport:
    """Run the requested audits against one model and one cohort."""
    space = list(papers)
    prediction = model.predict(space)
    predicted = {
        paper_id: prediction.log_median[index]
        for index, paper_id in enumerate(prediction.paper_ids)
    }
    truth = targets(
        space,
        reference_year=model.card.reference_year,
        horizon=model.card.horizon_years,
    )
    actual = {
        paper.paper_id: truth.values[index]
        for index, paper in enumerate(space)
        if truth.available[index]
    }
    available = set(actual)
    censored = {
        paper.paper_id for index, paper in enumerate(space) if truth.censored[index]
    }
    context = _Context(
        papers=space,
        predicted=predicted,
        actual=actual,
        available=available,
        censored=censored,
        importances=tuple(importances),
        reference_year=model.card.reference_year,
        horizon=model.card.horizon_years,
    )
    findings = tuple(
        _AUDIT_FUNCTIONS[name](context) for name in audits if name in _AUDIT_FUNCTIONS
    )
    cohort = {
        "papers": len(space),
        "labelled": len(available),
        "censored": len(censored),
        "with_full_text": sum(1 for paper in space if paper.has_full_text),
        "languages": dict(
            Counter((paper.language or "unknown").lower() for paper in space)
        ),
        "reference_year": model.card.reference_year,
        "horizon_years": model.card.horizon_years,
        "observed_citations": sum(
            citations_within(paper, horizon=model.card.horizon_years) or 0
            for paper in space
        ),
    }
    notes = (
        "Severity is a function of effect size only (high >= 0.60, medium >= 0.30, "
        "low >= 0.15 log units); it says nothing about how many papers are affected.",
        "Every audit reports an interval; a point estimate whose interval spans zero is "
        "reported as insufficient evidence rather than as a finding.",
        f"Right-censored papers ({len(censored)}) are excluded from training and from "
        "residual audits, and are counted in the cohort table instead.",
    )
    return BiasReport(findings=findings, cohort=cohort, notes=notes)
