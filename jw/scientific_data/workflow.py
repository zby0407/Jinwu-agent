"""Workflow: one research request in, one auditable data package out.

Stages run in a fixed order and each one records a receipt in the ledger:
compile the request into a contract, catalogue the sources, acquire them,
extract tables, map columns, align records, detect problems, repair what is
provably safe, collect what a human must decide, and write the package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acquisition import AcquisitionReport, Fetcher, acquire
from .alignment import AlignmentResult, align_tables
from .contracts import (
    DataRequirementContract,
    EntityRef,
    ExtractedTable,
    IssueCode,
    IssueSeverity,
    ObservationField,
    ObservationRecord,
    QualityIssue,
    RepairMode,
    SourceRole,
    StageState,
    default_repair_mode,
)
from .domain import DomainPlugin, get_plugin
from .entities import AliasClaim, EntityResolver
from .figures import (
    FigureCalibration,
    axis_issue,
    calibration_from_payload,
    digitise_points,
    overlay_issue,
    overlay_report,
    series_from_payload,
)
from .human_loop import HumanQueue, apply_answers, build_queue
from .ledger_io import save_ledger, start_run, workspace_run_dir
from .package import FigurePackage, PackageResult, refresh_manifest, write_package
from .policy import AcquisitionPolicy
from .quality import DetectionReport, detect_all
from .repair import RepairOutcome, apply_auto_repairs
from .sources import SourceCatalog, register_local_sources

__all__ = [
    "FigureInput",
    "WorkflowOptions",
    "WorkflowResult",
    "build_figure_package",
    "contract_from_request",
    "run_integration",
]


@dataclass(frozen=True, slots=True)
class FigureInput:
    """A digitisation side-car: axis ticks plus plotted marker positions."""

    figure_id: str
    payload: Mapping[str, Any]
    source_id: str = ""
    path: str = ""


@dataclass(slots=True)
class WorkflowOptions:
    """Everything the caller may decide before a run starts."""

    allow_network: bool = False
    allowed_hosts: tuple[str, ...] = ()
    denied_hosts: tuple[str, ...] = ()
    fetcher: Fetcher | None = None
    figure_inputs: tuple[FigureInput, ...] = ()
    answers: Mapping[str, str] = field(default_factory=dict)
    max_rows: int | None = None
    max_gap_days: float | None = None
    extra_sources: tuple[Mapping[str, Any], ...] = ()
    write_package_output: bool = True
    package_name: str = "package"
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class WorkflowResult:
    """The ledger plus the package that the run produced."""

    ledger: Any
    directory: str
    package: PackageResult | None = None
    queue: HumanQueue | None = None
    figures: tuple[FigurePackage, ...] = ()
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def records(self) -> tuple[ObservationRecord, ...]:
        return tuple(self.ledger.records)

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        return tuple(self.ledger.issues.values())

    def summary(self) -> dict[str, Any]:
        counts = self.ledger.summary_counts()
        blocking = tuple(issue for issue in self.issues if issue.blocking)
        pending = self.queue.open_requests if self.queue is not None else ()
        return {
            "run_id": self.ledger.run_id,
            "directory": self.directory,
            "counts": counts,
            "blocking_issues": len(blocking),
            "blocking_codes": sorted({str(issue.code) for issue in blocking}),
            "open_questions": len(pending),
            "package": self.package.directory if self.package else "",
            "stages": {
                name: payload.get("state") for name, payload in self.stages.items()
            },
        }


def contract_from_request(payload: Mapping[str, Any]) -> DataRequirementContract:
    """Compile a research request into a data requirement contract.

    Only what the caller states is encoded; anything the request leaves open is
    recorded in ``ambiguities`` instead of being guessed.
    """
    goal = str(payload.get("research_goal") or payload.get("goal") or "").strip()
    domain = str(payload.get("domain") or payload.get("domain_plugin") or "generic")
    plugin = get_plugin(domain)
    ambiguities: list[str] = []
    if not goal:
        ambiguities.append("research_goal_missing")
    if domain not in {"generic", plugin.name}:
        ambiguities.append(f"unknown_domain_plugin:{domain}")
    requested = tuple(payload.get("observables") or ())
    observables, observable_ambiguities = _observables(requested, plugin)
    ambiguities.extend(observable_ambiguities)
    identifier_fields = tuple(
        str(item) for item in payload.get("identifier_fields") or ("object",)
    )
    time_field = str(payload.get("time_field") or "time")
    budget = dict(payload.get("budget") or {})
    coverage = payload.get("coverage_window") or ()
    if len(coverage) != 2:
        coverage = ()
        ambiguities.append("coverage_window_incomplete")
    entity_class = str(
        payload.get("entity_class")
        or plugin.title
        or (plugin.entity.key_hints[0] if plugin.entity.key_hints else "")
    )
    if not entity_class:
        ambiguities.append("entity_class_missing")
    contract = DataRequirementContract(
        contract_id=str(payload.get("contract_id") or _contract_id(goal, domain)),
        research_goal=goal,
        entity_class=entity_class,
        observables=observables,
        identifier_fields=identifier_fields,
        time_field=time_field,
        merge_keys=tuple(payload.get("merge_keys") or plugin.merge_keys),
        domain_plugin=plugin.name,
        accepted_roles=tuple(str(item) for item in payload.get("accepted_roles") or ())
        or DataRequirementContract.accepted_roles,
        target_entity_count=int(payload.get("target_entity_count") or 0),
        min_records_per_entity=int(payload.get("min_records_per_entity") or 1),
        unit_expectations=dict(payload.get("unit_expectations") or {}),
        value_ranges=_ranges(payload.get("value_ranges") or {}),
        coverage_window=tuple(coverage),  # type: ignore[arg-type]
        budget=budget
        or DataRequirementContract(contract_id="x", research_goal="").budget,
        repair_overrides=dict(payload.get("repair_overrides") or {}),
        ambiguities=(*ambiguities, *(payload.get("ambiguities") or ())),
        provenance={"source": "user_request"},
    )
    return contract


def _contract_id(goal: str, domain: str) -> str:
    from .domain import slug

    return f"contract:{domain}:{slug(goal)[:48] or 'goal'}"


def _observables(
    requested: Sequence[Any], plugin: DomainPlugin
) -> tuple[tuple[ObservationField, ...], tuple[str, ...]]:
    if not requested:
        return (), ()
    fields: list[ObservationField] = []
    ambiguities: list[str] = []
    for item in requested:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "")
            spec = plugin.observable(name)
            fields.append(
                ObservationField(
                    name=name,
                    kind=str(item.get("kind") or (spec.kind if spec else "quantity")),
                    unit=str(item.get("unit") or (spec.unit if spec else "")),
                    required=bool(item.get("required", False)),
                    description=str(item.get("description") or ""),
                    aliases=tuple(str(alias) for alias in item.get("aliases") or ()),
                )
            )
            continue
        name = str(item)
        spec = plugin.observable(name)
        if spec is None and plugin.name != "generic":
            ambiguities.append(f"unknown_observable:{name}")
        fields.append(
            ObservationField(
                name=name,
                kind=spec.kind if spec else "quantity",
                unit=spec.canonical_unit if spec else "",
            )
        )
    return tuple(fields), tuple(ambiguities)


def _ranges(raw: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    for name, bounds in raw.items():
        try:
            low, high = bounds
            ranges[str(name)] = (float(low), float(high))
        except (TypeError, ValueError):
            continue
    return ranges


def run_integration(
    *,
    workspace: str | Path,
    contract: DataRequirementContract,
    inputs: Sequence[str | Path] = (),
    options: WorkflowOptions | None = None,
    run_id: str | None = None,
) -> WorkflowResult:
    """Run every stage on the given inputs and write the data package."""
    settings = options or WorkflowOptions()
    plugin = get_plugin(contract.domain_plugin)
    ledger = start_run(workspace, contract, run_id=run_id)
    directory = workspace_run_dir(workspace, ledger.run_id)
    result = WorkflowResult(ledger=ledger, directory=str(directory))

    catalog, acquisition = _stage_discover_and_acquire(
        ledger, contract, plugin, inputs, settings, directory
    )
    _record_stage(ledger, result, "discover", sources=len(catalog.sources))
    _record_stage(
        ledger,
        result,
        "acquire",
        acquired=len(catalog.acquired()),
        denied=acquisition.stats["denied"] if acquisition else 0,
        failed=acquisition.stats["failed"] if acquisition else 0,
    )

    tables, extraction_stats = _stage_extract(ledger, catalog, settings)
    _record_stage(ledger, result, "extract", **extraction_stats)

    mapping_issues = _stage_map(ledger, tables, contract, plugin)
    _record_stage(
        ledger,
        result,
        "semantics",
        tables=len(tables),
        mappings=len(ledger.mappings),
        issues=len(mapping_issues),
    )

    alignment = align_tables(tables, contract=contract, plugin=plugin)
    _absorb_alignment(ledger, alignment)
    _record_stage(
        ledger,
        result,
        "align",
        tables=len(tables),
        records=len(alignment.records),
        conflicts=len(alignment.conflicts),
        issues=len(alignment.issues),
    )

    figure_packages, figure_issues, figure_records = _stage_figures(
        ledger, contract, plugin, settings, alignment.resolver
    )
    ledger.add_records(figure_records)
    for issue in figure_issues:
        ledger.add_issue(issue)

    detection = detect_all(
        records=ledger.records,
        tables=tables,
        entities=tuple(ledger.entities.values()),
        contract=contract,
        plugin=plugin,
        max_gap_days=settings.max_gap_days,
    )
    for issue in detection.issues:
        ledger.add_issue(issue)
    _record_stage(
        ledger,
        result,
        "quality",
        issues=len(detection.issues),
        records=detection.stats["records"],
        usable_records=detection.stats["usable_records"],
    )

    repair = _stage_repair(ledger, tables, contract, plugin)
    tables = list(repair.tables)
    _record_stage(
        ledger,
        result,
        "repair",
        applied=repair.stats["auto_applied"],
        pending=repair.stats["pending_human"],
        failed=repair.stats["failed"],
    )

    queue, rejected_answers = _stage_human(ledger, settings)
    _record_stage(
        ledger,
        result,
        "human",
        requests=len(queue.requests),
        answered=len(queue.answers),
        open=len(queue.open_requests),
        blocking=len(queue.blocking_open),
        rejected_answers=rejected_answers,
    )

    post = detect_all(
        records=ledger.records,
        tables=tuple(tables),
        entities=tuple(ledger.entities.values()),
        contract=contract,
        plugin=plugin,
        max_gap_days=settings.max_gap_days,
    )
    added = _merge_new_issues(ledger, post)
    _record_stage(ledger, result, "revalidate", new_issues=len(added))

    if settings.write_package_output:
        # The manifest reports this stage, so open it before the package is built.
        ledger.mark_stage("package", StageState.RUNNING)
        package = write_package(
            directory=directory / settings.package_name,
            ledger=ledger,
            plugin=plugin,
            queue=queue,
            figures=figure_packages,
            notes=settings.notes,
        )
        result.package = package
        _register_artifacts(ledger, package)
        _record_stage(
            ledger,
            result,
            "package",
            files=len(package.files),
            records=package.counts["records"],
        )
        refresh_manifest(
            directory=package.directory,
            ledger=ledger,
            result=package,
            notes=settings.notes,
        )
    result.queue = queue
    result.figures = figure_packages
    save_ledger(ledger, directory)
    return result


# -- stages --------------------------------------------------------------


def _stage_discover_and_acquire(
    ledger: Any,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    inputs: Sequence[str | Path],
    settings: WorkflowOptions,
    directory: Path,
) -> tuple[SourceCatalog, AcquisitionReport | None]:
    catalog = SourceCatalog()
    if inputs:
        register_local_sources(catalog, [str(Path(item)) for item in inputs])
    for extra in settings.extra_sources:
        catalog.add(
            url=str(extra.get("url") or ""),
            title=str(extra.get("title") or ""),
            role=str(extra.get("role") or SourceRole.UNKNOWN.value),
            provider=str(extra.get("provider") or "discovery"),
            identifier=str(extra.get("identifier") or ""),
            format_hint=str(extra.get("format_hint") or ""),
            license=str(extra.get("license") or ""),
            discovered_via=str(extra.get("discovered_via") or "agent_search"),
            relevance=float(extra.get("relevance", 0.5) or 0.5),
            trust=float(extra.get("trust", 0.5) or 0.5),
            notes=tuple(str(item) for item in extra.get("notes") or ()),
        )
    policy = AcquisitionPolicy.from_contract(
        contract,
        allow_network=settings.allow_network,
        allowed_hosts=tuple(settings.allowed_hosts),
        denied_hosts=tuple(settings.denied_hosts),
    )
    report = acquire(
        catalog,
        policy=policy,
        workdir=directory / "sources",
        fetcher=settings.fetcher,
        contract=contract,
    )
    for source in report.catalog.sources.values():
        ledger.add_source(source)
    for issue in report.issues:
        ledger.add_issue(issue)
    return report.catalog, report


_READER_SUFFIXES = {
    ".csv": "delimited",
    ".tsv": "delimited",
    ".txt": "delimited",
    ".dat": "delimited",
    ".json": "delimited",
    ".jsonl": "delimited",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
}


def _stage_extract(
    ledger: Any, catalog: SourceCatalog, settings: WorkflowOptions
) -> tuple[list[ExtractedTable], dict[str, int]]:
    from .html_tables import read_html_file
    from .markdown_tables import read_markdown_file
    from .tabular import read_table

    tables: list[ExtractedTable] = []
    skipped = 0
    for source in catalog.acquired():
        path = source.local_path
        if not path:
            skipped += 1
            continue
        suffix = Path(path).suffix.lower()
        reader = _READER_SUFFIXES.get(suffix)
        try:
            if reader == "html":
                produced = read_html_file(path, source_id=source.source_id)
            elif reader == "markdown":
                produced = read_markdown_file(path, source_id=source.source_id)
            elif reader == "delimited":
                produced = read_table(
                    path,
                    source_id=source.source_id,
                    max_rows=settings.max_rows,
                )
            else:
                produced = ()
        except (OSError, ValueError, UnicodeError) as error:
            skipped += 1
            ledger.add_issue(
                QualityIssue(
                    issue_id=f"issue:extract:unreadable_source:{source.source_id}",
                    code=IssueCode.UNREADABLE_SOURCE,
                    severity=IssueSeverity.ERROR,
                    stage="extract",
                    subject={"source_id": source.source_id},
                    detail=f"Could not read {path}: {error}",
                    evidence={"suffix": suffix},
                    proposed_action="Provide a readable copy of this source.",
                    repair_mode=default_repair_mode(IssueCode.UNREADABLE_SOURCE),
                )
            )
            continue
        for table in produced:
            ledger.add_table(table)
            tables.append(table)
    return tables, {
        "sources": len(catalog.sources),
        "tables": len(tables),
        "rows": sum(len(table.rows) for table in tables),
        "skipped_sources": skipped,
    }


def _stage_map(
    ledger: Any,
    tables: Sequence[ExtractedTable],
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[QualityIssue, ...]:
    from .semantics import map_table

    issues: list[QualityIssue] = []
    for table in tables:
        mappings, mapping_issues = map_table(
            table, contract=contract, plugin=plugin, stage="semantics"
        )
        ledger.mappings.extend(mappings)
        for issue in mapping_issues:
            ledger.add_issue(issue)
        issues.extend(mapping_issues)
    return tuple(issues)


def _absorb_alignment(ledger: Any, alignment: AlignmentResult) -> None:
    ledger.add_records(alignment.records)
    for entity in alignment.entities():
        ledger.add_entity(entity)
    for issue in alignment.issues:
        ledger.add_issue(issue)


def _stage_figures(
    ledger: Any,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    settings: WorkflowOptions,
    resolver: EntityResolver | None,
) -> tuple[
    tuple[FigurePackage, ...], tuple[QualityIssue, ...], tuple[ObservationRecord, ...]
]:
    if not settings.figure_inputs:
        return (), (), ()
    entities = resolver if resolver is not None else EntityResolver(plugin=plugin)
    observable = _figure_observable(contract, plugin)
    packages: list[FigurePackage] = []
    issues: list[QualityIssue] = []
    records: list[ObservationRecord] = []
    for item in settings.figure_inputs:
        calibration = calibration_from_payload(item.payload)
        if item.source_id and not calibration.source_id:
            calibration = FigureCalibration(
                x=calibration.x,
                y=calibration.y,
                pixel_noise=calibration.pixel_noise,
                figure_id=calibration.figure_id or item.figure_id,
                source_id=item.source_id,
            )
        issue = axis_issue(
            calibration,
            figure_id=item.figure_id,
            expected_log=item.payload.get("expected_y_log"),
        )
        if issue is not None:
            issues.append(issue)
        series_records: list[ObservationRecord] = []
        for series in series_from_payload(item.payload):
            label = series.entity_hint or str(item.payload.get("entity") or "")
            entity_id = _entity_id_for(entities, label, item.source_id)
            produced = digitise_points(
                series,
                calibration,
                entity_id=entity_id,
                observable=str(item.payload.get("observable") or observable),
                unit=str(item.payload.get("unit") or ""),
                time_system=str(item.payload.get("time_system") or "mjd"),
                time_unit=str(item.payload.get("time_unit") or "day"),
                path=item.path,
            )
            series_records.extend(produced)
        report = overlay_report(series_records, calibration)
        mismatch = overlay_issue(report, figure_id=item.figure_id)
        if mismatch is not None:
            issues.append(mismatch)
        records.extend(series_records)
        packages.append(
            FigurePackage(
                figure_id=item.figure_id,
                source_id=item.source_id or calibration.source_id,
                calibration=calibration,
                records=tuple(series_records),
                overlay=report,
            )
        )
    return tuple(packages), tuple(issues), tuple(records)


def _figure_observable(contract: DataRequirementContract, plugin: DomainPlugin) -> str:
    for item in contract.observables:
        if item.kind in {"quantity", "quality"}:
            return item.name
    for spec in plugin.observables:
        if spec.kind in {"quantity", "quality"}:
            return spec.name
    return "value"


def _entity_id_for(resolver: EntityResolver, label: str, source_id: str) -> str:
    if not label:
        return resolver.add(
            AliasClaim(label="unlabelled", source_id=source_id)
        ).entity_id
    known = resolver.entity_id_for(label)
    if known:
        return known
    return resolver.add(AliasClaim(label=label, source_id=source_id)).entity_id


def _stage_repair(
    ledger: Any,
    tables: Sequence[ExtractedTable],
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> RepairOutcome:
    outcome = apply_auto_repairs(
        records=tuple(ledger.records),
        issues=tuple(ledger.issues.values()),
        tables=tuple(tables),
        contract=contract,
        plugin=plugin,
    )
    ledger.records = list(outcome.records)
    for entry in outcome.repairs:
        ledger.add_repair(entry)
    for issue in outcome.issues:
        ledger.issues[issue.issue_id] = issue
    for issue in outcome.pending:
        ledger.issues[issue.issue_id] = issue
    return outcome


def _stage_human(ledger: Any, settings: WorkflowOptions) -> tuple[HumanQueue, int]:
    queue = build_queue(ledger.issues.values(), records=tuple(ledger.records))
    rejected = 0
    for request_id, choice in settings.answers.items():
        try:
            queue = queue.answer(request_id, str(choice), actor="user")
        except (KeyError, ValueError):
            # An answer that no longer matches a question is kept out of the data.
            rejected += 1
    if queue.answers:
        resolution = apply_answers(
            records=tuple(ledger.records),
            issues=tuple(ledger.issues.values()),
            queue=queue,
        )
        ledger.records = list(resolution.records)
        for entry in resolution.repairs:
            ledger.add_repair(entry)
        for issue in resolution.issues:
            ledger.issues[issue.issue_id] = issue
        queue = resolution.queue
    return queue, rejected


def _merge_new_issues(ledger: Any, report: DetectionReport) -> tuple[QualityIssue, ...]:
    added: list[QualityIssue] = []
    for issue in report.issues:
        if issue.issue_id in ledger.issues:
            continue
        ledger.add_issue(issue)
        added.append(issue)
    return tuple(added)


def _register_artifacts(ledger: Any, package: PackageResult) -> None:
    from .package import artifact_for

    for role, path in package.files.items():
        ledger.add_artifact(
            artifact_for(
                role,
                path,
                rows=package.counts.get("records", 0) if role == "observations" else 0,
            )
        )


def _record_stage(
    ledger: Any, result: WorkflowResult, name: str, **counts: Any
) -> None:
    numeric = {
        key: int(value) for key, value in counts.items() if isinstance(value, int)
    }
    receipt = ledger.mark_stage(name, StageState.COMPLETE, counts=numeric)
    result.stages[name] = {
        "state": str(receipt.state),
        "counts": numeric,
    }


def build_figure_package(
    figure_id: str,
    payload: Mapping[str, Any],
    *,
    source_id: str = "",
    path: str = "",
) -> FigureInput:
    """Convenience constructor matching the side-car schema."""
    return FigureInput(
        figure_id=figure_id, payload=payload, source_id=source_id, path=path
    )


def unresolved_entities(entities: Iterable[EntityRef]) -> tuple[EntityRef, ...]:
    """Entities whose identity could not be settled from the evidence."""
    return tuple(
        entity
        for entity in entities
        if entity.ambiguous_with or entity.link_confidence.value == "low"
    )


def auto_repair_modes() -> dict[str, str]:
    """Which issue codes the kernel may fix without asking (for documentation)."""
    return {
        code.value: default_repair_mode(code).value
        for code in IssueCode
        if default_repair_mode(code) is RepairMode.AUTO_SAFE
    }
