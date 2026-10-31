"""Column semantics: profile a raw column, then map it onto a canonical field.

Mapping is a scored, explainable decision, never a guess: every proposal keeps
the reasons that produced it, the unit it read from the header or the values,
and — when two canonical fields fit almost equally well — the runner-up, so the
human loop can ask a precise question instead of "which column is which?".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .contracts import (
    ConfidenceBand,
    DataRequirementContract,
    ExtractedTable,
    FieldMapping,
    IssueCode,
    IssueSeverity,
    QualityIssue,
    default_repair_mode,
)
from .domain import (
    DomainPlugin,
    ObservableSpec,
    confidence_from_score,
    detect_unit_in_values,
    normalise_identifier,
    slug,
    strip_unit_from_header,
    unit_from_header,
)
from .timescales import detect_time_system
from .units import dimension_of, resolve_unit

__all__ = [
    "ColumnProfile",
    "MappingProposal",
    "column_profiles",
    "map_table",
    "profile_column",
    "propose_mappings",
    "select_overrides",
]

MAPPING_MIN_SCORE = 0.5
AMBIGUITY_MARGIN = 0.04
_NUMERIC_KINDS = frozenset({"quantity", "number", "float", "int"})
_UNCERTAINTY_TOKENS = frozenset(
    {"e", "err", "error", "unc", "uncert", "uncertainty", "sigma", "sd", "std", "d"}
)
# ``e_mag``/``err_mag``/``sigma_mag``/``d_mag``/``e1`` mark an uncertainty column;
# a bare "date" column must not match, so a separator or digit is required.
_UNCERTAINTY_PREFIX = re.compile(r"^(?:e|err|sigma|d)(?:[-_]|[0-9]|[a-z]{0,0}$)")


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """What one raw column looks like, without deciding what it means."""

    column: str
    present: int = 0
    missing: int = 0
    numeric: int = 0
    numeric_share: float = 0.0
    distinct: int = 0
    samples: tuple[str, ...] = ()
    inferred_unit: str = ""
    unit_source: str = "none"  # header | values | none
    time_system: str = ""
    value_shape: str = "empty"  # number | date | text | mixed | empty
    has_uncertainty_marker: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "present": self.present,
            "missing": self.missing,
            "numeric": self.numeric,
            "numeric_share": round(self.numeric_share, 6),
            "distinct": self.distinct,
            "samples": list(self.samples),
            "inferred_unit": self.inferred_unit,
            "unit_source": self.unit_source,
            "time_system": self.time_system,
            "value_shape": self.value_shape,
            "has_uncertainty_marker": self.has_uncertainty_marker,
        }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_number(text: str) -> bool:
    if not text:
        return False
    cleaned = text.replace("\u2212", "-").replace(" ", "").replace("\u00a0", "")
    cleaned = (
        cleaned.replace(",", "")
        if re.match(r"^[-+]?[\d,]+(\.\d+)?$", cleaned)
        else cleaned
    )
    stripped = cleaned.rstrip("%").lstrip("<>~")
    try:
        float(stripped)
    except ValueError:
        return False
    return True


_DATE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{4}-\d{2}"  # monthly stamps: 2020-01
    r"|\d{4}M\d{2}"  # SILSO style: 2020M01
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}/\d{2}/\d{2}$)"
)


def profile_column(
    column: str, values: Sequence[Any], *, limit: int = 8
) -> ColumnProfile:
    """Describe one raw column: coverage, numeric share, unit, time system."""
    cells = [_cell(value) for value in values]
    present = [cell for cell in cells if cell]
    missing = len(cells) - len(present)
    numeric = sum(1 for cell in present if _is_number(cell))
    dates = sum(1 for cell in present if _DATE_RE.match(cell.replace("\u2212", "-")))
    if not present:
        shape = "empty"
    elif numeric == len(present):
        shape = "number"
    elif dates == len(present):
        shape = "date"
    elif numeric == 0:
        shape = "text"
    else:
        shape = "mixed"
    header_unit = unit_from_header(column)
    value_unit = detect_unit_in_values(present, column) if shape != "number" else ""
    if header_unit:
        inferred_unit, unit_source = header_unit, "header"
    elif value_unit:
        inferred_unit, unit_source = value_unit, "values"
    else:
        inferred_unit, unit_source = "", "none"
    time_system = detect_time_system(column)
    if not time_system:
        for sample in present[:3]:
            time_system = detect_time_system(sample)
            if time_system:
                break
    tokens = set(_header_tokens(column))
    marker = bool(tokens & _UNCERTAINTY_TOKENS) or bool(
        _UNCERTAINTY_PREFIX.match(slug(column))
    )
    return ColumnProfile(
        column=column,
        present=len(present),
        missing=missing,
        numeric=numeric,
        numeric_share=numeric / len(present) if present else 0.0,
        distinct=len(set(present)),
        samples=tuple(present[:limit]),
        inferred_unit=inferred_unit,
        unit_source=unit_source,
        time_system=time_system,
        value_shape=shape,
        has_uncertainty_marker=marker,
    )


def column_profiles(table: ExtractedTable) -> tuple[ColumnProfile, ...]:
    """Profile every column of one extracted table."""
    return tuple(
        profile_column(column, [row.get(column, "") for row in table.rows])
        for column in table.columns
    )


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", text.lower())
        if token
    )


# Vocabulary used only to split concatenated published headers such as
# ``SNID``, ``OBSMJD`` and ``MAGERR``.  It is deliberately small: a header is
# split only when *both* halves are known words, so ``FILTER`` and ``MAGNITUDE``
# stay whole.
_HEADER_SPLIT_VOCAB = frozenset(
    {
        "sn",
        "id",
        "obs",
        "mjd",
        "jd",
        "bjd",
        "date",
        "time",
        "mag",
        "magnitude",
        "err",
        "error",
        "unc",
        "uncert",
        "uncertainty",
        "sigma",
        "flux",
        "fluxdensity",
        "f",
        "fnu",
        "filter",
        "band",
        "flt",
        "filt",
        "name",
        "object",
        "obj",
        "ra",
        "dec",
        "redshift",
        "z",
        "type",
        "class",
        "apparent",
        "peak",
        "e",
        "d",
        "value",
        "val",
        "measure",
        "measurement",
        "jy",
        "mjy",
        "ujy",
        "njy",
        "sfu",
        "kev",
        "mev",
        "gev",
        "ghz",
        "mhz",
        "hz",
        "nm",
        "um",
        "cm",
        "mm",
        "km",
        "kg",
        "deg",
        "count",
        "counts",
        "cts",
        "rate",
        "unit",
        "units",
        "单位",
    }
)


def _split_header_token(token: str) -> tuple[str, ...]:
    if token in _HEADER_SPLIT_VOCAB or len(token) < 4:
        return (token,)
    for index in range(len(token) - 1, 1, -1):
        left, right = token[:index], token[index:]
        if left in _HEADER_SPLIT_VOCAB and right in _HEADER_SPLIT_VOCAB:
            return (left, right)
    return (token,)


def _header_tokens(text: str) -> tuple[str, ...]:
    """Words in a raw header, splitting the common concatenated spellings."""
    return tuple(
        piece
        for token in _tokens(strip_unit_from_header(str(text or "")))
        for piece in _split_header_token(token)
    )


def _quantities(
    plugin: DomainPlugin, contract: DataRequirementContract
) -> tuple[ObservableSpec, ...]:
    """Canonical fields the contract asked for, falling back to the plugin."""
    if contract.observables:
        specs: list[ObservableSpec] = []
        for field in contract.observables:
            known = plugin.observable(field.name)
            specs.append(
                ObservableSpec(
                    name=field.name,
                    kind=field.kind,
                    canonical_unit=field.unit
                    or (known.canonical_unit if known else ""),
                    header_hints=(known.header_hints if known else ())
                    + tuple(field.aliases),
                    required=field.required,
                    value_range=(known.value_range if known else None),
                    strict_unit=(known.strict_unit if known else False),
                )
            )
        return tuple(specs)
    return plugin.observables


def score_column(
    profile: ColumnProfile,
    spec: ObservableSpec,
    *,
    plugin: DomainPlugin,
) -> tuple[float, tuple[str, ...]]:
    """Score how well one column matches one canonical field, with reasons."""
    reasons: list[str] = []
    base_text = strip_unit_from_header(profile.column).strip().lower()
    base_slug = slug(base_text)
    raw_hints = spec.header_hints or plugin.hints_for(spec.name)
    # slug() drops script characters, so a Chinese header collapses to "". An
    # empty hint would then match every such header, so keep only real hints.
    hints = [hint for hint in (slug(item) for item in raw_hints) if hint]
    spelled = {str(item).strip().lower() for item in raw_hints if str(item).strip()}
    spec_slug = slug(spec.name)
    score = 0.0
    score_source = "none"

    if base_slug and base_slug == spec_slug:
        score, score_source = 1.0, "exact_head"
    elif base_text and base_text in spelled:
        # A hint written as-is (including Chinese headers) is a strong signal.
        score, score_source = 0.93, "listed_hint"
    elif base_slug and base_slug in hints:
        score, score_source = 0.93, "listed_hint"
    elif normalise_identifier(spec.name) == normalise_identifier(base_slug):
        score, score_source = 0.9, "name_alias"
    else:
        header_tokens = set(_header_tokens(profile.column))
        spec_tokens = set(_tokens(spec.name))
        hint_tokens = {token for hint in raw_hints for token in _tokens(hint)}
        if spec_tokens and spec_tokens <= header_tokens:
            score, score_source = 0.8, "header_contains_field"
        elif header_tokens and header_tokens <= (spec_tokens | hint_tokens):
            score, score_source = 0.72, "header_subset_of_field"
        elif hint_tokens & header_tokens:
            overlap = hint_tokens & header_tokens
            strongest = max(len(token) for token in overlap)
            score, score_source = (
                0.55 + min(0.15, strongest / 40.0),
                "header_hint_token",
            )
    if score == 0.0:
        return 0.0, ()

    # -- context adjustments (never large enough to invent a mapping) ------
    if spec.kind == "time":
        if profile.time_system or profile.value_shape == "date":
            score += 0.06
            reasons.append(f"time_like:{profile.time_system or 'date'}")
        elif profile.value_shape == "text":
            score -= 0.3
            reasons.append("time_system_unreadable")
    elif spec.kind in {"identifier", "label"}:
        if profile.value_shape == "text":
            score += 0.05
            reasons.append("categorical_values")
        elif profile.value_shape == "number":
            score -= 0.1
            reasons.append("identifier_looks_numeric")
    elif profile.value_shape == "number" and profile.numeric_share >= 0.95:
        score += 0.05
        reasons.append("numeric_column")
    elif profile.value_shape == "empty":
        score -= 0.2

    uncertainty_field = "uncertainty" in spec_slug or spec_slug.startswith(
        ("e_", "err")
    )
    if (
        profile.has_uncertainty_marker
        and spec.kind not in {"quality"}
        and not uncertainty_field
    ):
        score -= 0.35
        reasons.append("uncertainty_column_not_value")
    if "uncertainty" in spec_slug and not profile.has_uncertainty_marker:
        header_tokens = _tokens(profile.column)
        if not any("unc" in token or "err" in token for token in header_tokens):
            score -= 0.25
            reasons.append("value_column_not_uncertainty")

    if spec.canonical_unit and profile.inferred_unit:
        dimension = dimension_of(spec.canonical_unit)
        source_dimension = dimension_of(profile.inferred_unit)
        if dimension and source_dimension and dimension == source_dimension:
            score += 0.08
            reasons.append(f"unit_dimension_agrees:{profile.inferred_unit}")
        elif dimension and source_dimension and dimension != source_dimension:
            score -= 0.25
            reasons.append(
                f"unit_dimension_mismatch:{profile.inferred_unit}!={spec.canonical_unit}"
            )
    reasons.append(f"match:{score_source}")
    return max(0.0, min(1.0, score)), tuple(reasons)


def propose_mappings(
    table: ExtractedTable,
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[MappingProposal, ...]:
    """Best canonical field for each column, with the runner-up recorded."""
    specs = _quantities(plugin, contract)
    proposals: list[MappingProposal] = []
    for profile in column_profiles(table):
        if profile.value_shape == "empty":
            continue
        ranked: list[tuple[float, ObservableSpec, tuple[str, ...]]] = []
        for spec in specs:
            score, reasons = score_column(profile, spec, plugin=plugin)
            if score > 0:
                ranked.append((score, spec, reasons))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        if not ranked:
            continue
        best_score, best_spec, best_reasons = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        ambiguous = runner is not None and (best_score - runner[0]) <= AMBIGUITY_MARGIN
        target_unit = best_spec.canonical_unit
        source_unit = profile.inferred_unit
        reasons = tuple(best_reasons)
        if (
            not source_unit
            and target_unit == "mag"
            and {"mag", "magnitude", "星等"} & set(_header_tokens(profile.column))
        ):
            # ``MAG`` / ``Magnitude`` name the observable itself; unlike a bare
            # ``flux`` column, assuming the canonical magnitude unit is not a
            # hidden scale change (all magnitude systems still stay separate).
            source_unit = "mag"
            reasons = (*reasons, "unit_inferred_from_header:mag")
        transformation = ""
        if source_unit and target_unit and source_unit != target_unit:
            resolution = resolve_unit(
                source_unit, quantity=best_spec.name, plugin=plugin
            )
            if resolution.exact and resolution.canonical == target_unit:
                transformation = f"unit_scale:{source_unit}->{target_unit}"
            elif resolution.note:
                transformation = f"unit_unresolved:{resolution.note}"
        proposals.append(
            MappingProposal(
                column=profile.column,
                observable=best_spec.name,
                score=best_score,
                reasons=reasons,
                target_unit=target_unit,
                source_unit=source_unit,
                transformation=transformation,
                alternative=runner[1].name if ambiguous and runner else "",
                alternative_score=runner[0] if ambiguous and runner else 0.0,
                ambiguous=ambiguous,
                column_profile=profile,
            )
        )
    return tuple(proposals)


@dataclass(frozen=True, slots=True)
class MappingProposal:
    """One candidate column → field mapping with its evidence."""

    column: str
    observable: str
    score: float
    reasons: tuple[str, ...] = ()
    target_unit: str = ""
    source_unit: str = ""
    transformation: str = ""
    alternative: str = ""
    alternative_score: float = 0.0
    ambiguous: bool = False
    column_profile: ColumnProfile | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "observable": self.observable,
            "score": round(self.score, 6),
            "reasons": list(self.reasons),
            "target_unit": self.target_unit,
            "source_unit": self.source_unit,
            "transformation": self.transformation,
            "alternative": self.alternative,
            "alternative_score": round(self.alternative_score, 6),
            "ambiguous": self.ambiguous,
            "profile": self.column_profile.to_dict() if self.column_profile else {},
        }


def _is_unit_header(column: str) -> bool:
    return bool(set(_header_tokens(column)) & {"unit", "units", "单位"}) or slug(
        column
    ) in {"unit", "units"}


def _unit_column(table: ExtractedTable) -> tuple[str, tuple[str, ...]]:
    """A column that carries units, plus the distinct spellings it uses."""
    for column in table.columns:
        if not _is_unit_header(column):
            continue
        present = [
            str(row.get(column, "") or "").strip()
            for row in table.rows
            if str(row.get(column, "") or "").strip()
        ]
        if present and all(dimension_of(value) for value in present):
            return column, tuple(sorted(set(present)))
    return "", ()


def _unit_transformation(
    source: str, target: str, observable: str, plugin: DomainPlugin
) -> str:
    if not (source and target and source != target):
        return ""
    resolution = resolve_unit(source, quantity=observable, plugin=plugin)
    if resolution.exact and resolution.canonical == target:
        return f"unit_scale:{source}->{target}"
    return f"unit_unresolved:{resolution.note}" if resolution.note else ""


def _unit_column_issue(
    *,
    table: ExtractedTable,
    column: str,
    detail: str,
    evidence: Mapping[str, Any],
    code: IssueCode = IssueCode.UNIT_INCONSISTENCY,
) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:map:{table.table_id}:{slug(column)}:unit-column",
        code=code,
        severity=IssueSeverity.WARNING,
        stage="semantics",
        subject={
            "table_id": table.table_id,
            "source_id": table.source_id,
            "field": column,
        },
        detail=detail,
        evidence=dict(evidence),
        proposed_action=(
            "Keep the original unit strings, or split this table so each value "
            "column has one unambiguous unit."
        ),
        repair_mode=default_repair_mode(code),
    )


def select_overrides(
    table: ExtractedTable,
    overrides: Sequence[Mapping[str, Any]],
    *,
    source_titles: Mapping[str, str] | None = None,
) -> list[Mapping[str, Any]]:
    """Overrides that target this table, by source id or file basename."""
    titles = source_titles or {}
    names = {table.source_id, titles.get(table.source_id, "")}
    return [
        item
        for item in overrides
        if str(item.get("source") or item.get("source_id") or "") in names
    ]


def _override_issue(*, table: ExtractedTable, column: str, detail: str) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:map:{table.table_id}:{slug(column)}:override",
        code=IssueCode.FIELD_UNMAPPED,
        severity=IssueSeverity.WARNING,
        stage="semantics",
        subject={
            "table_id": table.table_id,
            "source_id": table.source_id,
            "field": column,
        },
        detail=detail,
        proposed_action="Correct the column override or leave the column untouched.",
        repair_mode=default_repair_mode(IssueCode.FIELD_UNMAPPED),
    )


def map_table(
    table: ExtractedTable,
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    stage: str = "semantics",
    overrides: Sequence[Mapping[str, Any]] = (),
) -> tuple[tuple[FieldMapping, ...], tuple[QualityIssue, ...]]:
    """Map a table's columns; return accepted mappings plus the issues raised.

    A low-scoring column is reported as unmapped instead of being forced onto
    the closest field name, and a near-tie between two fields is escalated to a
    human decision rather than resolved by argument order.
    """
    mappings: list[FieldMapping] = []
    issues: list[QualityIssue] = []
    proposals = list(propose_mappings(table, contract=contract, plugin=plugin))
    overrides_by_column = {
        str(item.get("column") or ""): item
        for item in overrides
        if str(item.get("column") or "")
    }
    for column, item in overrides_by_column.items():
        if column not in table.columns:
            issues.append(
                _override_issue(
                    table=table,
                    column=column,
                    detail=(
                        f"Column override names '{column}', which is not in this "
                        "table; it was ignored."
                    ),
                )
            )
            continue
        observable = str(item.get("observable") or "")
        spec = plugin.observable(observable)
        if spec is None:
            issues.append(
                _override_issue(
                    table=table,
                    column=column,
                    detail=(
                        f"Column override for '{column}' names unknown observable "
                        f"'{observable}'; it was ignored."
                    ),
                )
            )
            continue
        index = next(
            (
                index
                for index, proposal in enumerate(proposals)
                if proposal.column == column
            ),
            None,
        )
        source_unit = str(item.get("unit") or "")
        if index is None:
            proposals.append(
                MappingProposal(
                    column=column,
                    observable=observable,
                    score=1.0,
                    reasons=("user_override",),
                    target_unit=spec.canonical_unit,
                    source_unit=source_unit,
                    transformation=_unit_transformation(
                        source_unit, spec.canonical_unit, observable, plugin
                    ),
                )
            )
        else:
            existing = proposals[index]
            proposals[index] = replace(
                existing,
                observable=observable,
                score=1.0,
                reasons=(*existing.reasons, "user_override"),
                target_unit=spec.canonical_unit,
                source_unit=source_unit or existing.source_unit,
                transformation=_unit_transformation(
                    source_unit or existing.source_unit,
                    spec.canonical_unit,
                    observable,
                    plugin,
                ),
                alternative="",
                alternative_score=0.0,
                ambiguous=False,
            )
    unit_column, unit_values = _unit_column(table)
    if unit_column:
        value_proposals = [
            proposal
            for proposal in proposals
            if (
                plugin.observable(proposal.observable) is not None
                and plugin.observable(proposal.observable).kind
                in {"quantity", "quality"}
            )
        ]
        if value_proposals and len(unit_values) == 1:
            source_unit = unit_values[0]
            targets = {proposal.target_unit for proposal in value_proposals}
            if len(targets) <= 1:
                for index, proposal in enumerate(proposals):
                    if proposal not in value_proposals:
                        continue
                    if proposal.source_unit and proposal.source_unit != source_unit:
                        issues.append(
                            _unit_column_issue(
                                table=table,
                                column=unit_column,
                                detail=(
                                    f"Column '{proposal.column}' declares unit "
                                    f"'{proposal.source_unit}' but the unit column "
                                    f"says '{source_unit}'."
                                ),
                                evidence={
                                    "column": proposal.column,
                                    "header_unit": proposal.source_unit,
                                    "unit_column_value": source_unit,
                                },
                            )
                        )
                        continue
                    proposals[index] = replace(
                        proposal,
                        source_unit=source_unit,
                        transformation=_unit_transformation(
                            source_unit,
                            proposal.target_unit,
                            proposal.observable,
                            plugin,
                        ),
                        reasons=(
                            *proposal.reasons,
                            f"unit_from_column:{source_unit}",
                        ),
                    )
            else:
                issues.append(
                    _unit_column_issue(
                        table=table,
                        column=unit_column,
                        detail=(
                            "One unit column serves value columns with different "
                            "target units; the unit cannot be assigned without "
                            "guessing."
                        ),
                        evidence={
                            "columns": [p.column for p in value_proposals],
                            "target_units": sorted(targets),
                            "unit_column_value": source_unit,
                        },
                        code=IssueCode.UNIT_UNKNOWN,
                    )
                )
        elif value_proposals:
            issues.append(
                _unit_column_issue(
                    table=table,
                    column=unit_column,
                    detail=(
                        "The unit column uses several spellings "
                        f"({', '.join(unit_values)}); the values are kept but no "
                        "single unit is assumed."
                    ),
                    evidence={
                        "unit_values": list(unit_values),
                        "columns": [p.column for p in value_proposals],
                    },
                )
            )
    covered = {proposal.column for proposal in proposals}
    if unit_column:
        covered.add(unit_column)

    for proposal in proposals:
        accepted = proposal.score >= MAPPING_MIN_SCORE and not proposal.ambiguous
        confidence = confidence_from_score(proposal.score)
        if proposal.ambiguous:
            confidence = ConfidenceBand.LOW
        mapping_id = f"map:{table.table_id}:{slug(proposal.column)}"
        mappings.append(
            FieldMapping(
                mapping_id=mapping_id,
                source_id=table.source_id,
                table_id=table.table_id,
                source_column=proposal.column,
                observable=proposal.observable,
                target_unit=proposal.target_unit,
                source_unit=proposal.source_unit,
                transformation=proposal.transformation,
                confidence=confidence,
                evidence="; ".join(proposal.reasons),
                applied=accepted,
            )
        )
        if proposal.ambiguous:
            issues.append(
                QualityIssue(
                    issue_id=f"issue:{mapping_id}:ambiguous",
                    code=IssueCode.FIELD_MAPPING_AMBIGUOUS,
                    severity=IssueSeverity.ERROR,
                    stage=stage,
                    subject={
                        "table_id": table.table_id,
                        "source_id": table.source_id,
                        "field": proposal.column,
                        "observable": proposal.observable,
                    },
                    detail=(
                        f"Column '{proposal.column}' fits '{proposal.observable}' "
                        f"({proposal.score:.2f}) and '{proposal.alternative}' "
                        f"({proposal.alternative_score:.2f}) almost equally."
                    ),
                    evidence={
                        "candidates": [proposal.observable, proposal.alternative],
                        "scores": [proposal.score, proposal.alternative_score],
                        "reasons": list(proposal.reasons),
                    },
                    proposed_action=(
                        f"Choose between '{proposal.observable}' and "
                        f"'{proposal.alternative}' for column '{proposal.column}'."
                    ),
                    repair_mode=default_repair_mode(IssueCode.FIELD_MAPPING_AMBIGUOUS),
                )
            )
        elif proposal.score < MAPPING_MIN_SCORE:
            issues.append(
                QualityIssue(
                    issue_id=f"issue:{mapping_id}:weak",
                    code=IssueCode.FIELD_UNMAPPED,
                    severity=IssueSeverity.WARNING,
                    stage=stage,
                    subject={
                        "table_id": table.table_id,
                        "source_id": table.source_id,
                        "field": proposal.column,
                    },
                    detail=(
                        f"Column '{proposal.column}' only weakly matches "
                        f"'{proposal.observable}' ({proposal.score:.2f}); it is kept "
                        "out of the integrated table."
                    ),
                    evidence={"reasons": list(proposal.reasons)},
                    proposed_action="Confirm or reject this column mapping.",
                    repair_mode=default_repair_mode(IssueCode.FIELD_UNMAPPED),
                )
            )
    for column in table.columns:
        if column in covered or column.startswith("_") or column.startswith("extra_"):
            continue
        issues.append(
            QualityIssue(
                issue_id=f"issue:map:{table.table_id}:{slug(column)}:unmapped",
                code=IssueCode.FIELD_UNMAPPED,
                severity=IssueSeverity.INFO,
                stage=stage,
                subject={
                    "table_id": table.table_id,
                    "source_id": table.source_id,
                    "field": column,
                },
                detail=f"Column '{column}' matches no canonical field; it is preserved but unused.",
                proposed_action="Add the field to the contract if this column matters.",
                repair_mode=default_repair_mode(IssueCode.FIELD_UNMAPPED),
            )
        )
    return tuple(mappings), tuple(issues)


def described_mapping(mapping: FieldMapping) -> Mapping[str, Any]:
    """Compact mapping row for reports and the mapping CSV."""
    return {
        "source": mapping.source_id,
        "table": mapping.table_id,
        "column": mapping.source_column,
        "observable": mapping.observable,
        "source_unit": mapping.source_unit,
        "target_unit": mapping.target_unit,
        "transformation": mapping.transformation,
        "confidence": str(mapping.confidence),
        "applied": mapping.applied,
        "evidence": mapping.evidence,
    }
