"""Tool surface for multi-source scientific data integration.

The agent asks for a research goal; these tools compile it into a data
requirement contract, plan and fetch sources under policy, extract and align the
tables, detect problems, repair only what is provably reversible, and write a
data package with record-level provenance.  Anything the kernel refuses to
decide lands in a numbered question queue instead of being guessed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from jw.scientific_data.acquisition import acquire
from jw.scientific_data.contracts import IssueCode, default_repair_mode
from jw.scientific_data.data_providers import (
    discover_data_sources,
    list_providers,
    resolve_provider_settings,
)
from jw.scientific_data.discovery import discover_sources, literature_searcher
from jw.scientific_data.discovery_support import (
    inspect_original,
    web_candidates,
    write_receipt,
)
from jw.scientific_data.domain import get_plugin, list_plugins, plugin_for_contract
from jw.scientific_data.figures import (
    axis_issue,
    calibration_from_payload,
    digitise_points,
    overlay_issue,
    overlay_report,
    series_from_payload,
)
from jw.scientific_data.ledger_io import load_ledger, run_dirs, workspace_run_dir
from jw.scientific_data.policy import AcquisitionPolicy
from jw.scientific_data.sources import SourceCatalog, plan_retrieval
from jw.scientific_data.workflow import (
    FigureInput,
    WorkflowOptions,
    apply_human_answers,
    contract_from_request,
    run_integration,
)
from jw.workspaces import workspace_root_from_config

from .registry import register_tool_bundle

_MAX_LISTED_ISSUES = 40
_MAX_LISTED_RECORDS = 40


def _result(**payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _workspace(config: RunnableConfig | None) -> Path:
    return workspace_root_from_config(config)


def _payload(raw: str, *, what: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError(f"{what} must be a JSON object")
    return decoded


def _object_list(raw: str) -> tuple[dict[str, Any], ...]:
    """Parse an optional JSON list of objects, rejecting other shapes early."""
    text = raw.strip()
    if not text:
        return ()
    decoded = json.loads(text)
    if not isinstance(decoded, list):
        raise ValueError("expected a JSON list of objects")
    return tuple(item for item in decoded if isinstance(item, dict))


def _paths(values: list[str], workspace: Path) -> tuple[Path, ...]:
    """Resolve input paths, refusing anything outside the task workspace."""
    root = workspace.resolve()
    resolved: list[Path] = []
    for value in values:
        if not value.strip():
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        target = candidate.resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"path escaped the task workspace: {value}")
        resolved.append(target)
    return tuple(resolved)


def _run_or_error(workspace: Path, run_id: str):
    directory = workspace_run_dir(workspace, run_id)
    ledger_path = directory / "ledger.json"
    if not ledger_path.is_file():
        raise ValueError(f"unknown run: {run_id}")
    return directory, load_ledger(ledger_path)


def _issue_payload(issue: Any) -> dict[str, Any]:
    return {
        "issue_id": issue.issue_id,
        "code": str(issue.code),
        "severity": str(issue.severity),
        "state": str(issue.state),
        "repair_mode": str(issue.repair_mode),
        "blocking": issue.blocking,
        "auto_applied": issue.auto_applied,
        "stage": issue.stage,
        "detail": issue.detail,
        "proposed_action": issue.proposed_action,
        "subject": dict(issue.subject),
    }


@tool(parse_docstring=True)
def sci_data_skill(
    name: str = "scientific-data-discovery", config: RunnableConfig = None
) -> str:
    """Read an enabled bundled skill assigned to scientific-data.

    Args:
        name: Bare assigned skill name; paths are refused.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with actual skill text and byte hash, or a refusal.
    """
    try:
        from jw.subagents.skill_registry import skills_for_agent

        if not name or any(c in name for c in "/\\:") or ".." in name:
            raise ValueError("a bare assigned skill name is required")
        if f"/skills/{name}" not in skills_for_agent("scientific-data", installed=[]):
            raise ValueError("skill is disabled or not assigned to scientific-data")
        base = Path(__file__).resolve().parents[1] / "subagents"
        for folder in sorted(base.iterdir()):
            root = (folder / "skills").resolve()
            path = (root / name / "SKILL.md").resolve()
            if path.is_relative_to(root) and path.is_file():
                raw = path.read_bytes()
                return _result(
                    status="ok",
                    source=f"/skills/{name}/SKILL.md",
                    sha256=hashlib.sha256(raw).hexdigest(),
                    text=raw.decode("utf-8"),
                )
        raise ValueError("assigned bundled skill file is missing")
    except (OSError, ValueError, UnicodeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_capabilities(config: RunnableConfig = None) -> str:
    """Describe what the scientific data integration pipeline can and cannot do.

    Use this first when a user asks how a research goal becomes a usable
    dataset: it lists the pipeline stages, the output files, the domain plugins
    and — importantly — the boundary between repairs applied automatically and
    decisions that must be confirmed by a human.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with stages, plugins, output files and the repair policy boundary.
    """
    try:
        auto = {
            code.value: default_repair_mode(code).value
            for code in IssueCode
            if default_repair_mode(code).value == "auto_safe"
        }
        return _result(
            status="ok",
            stages=[
                "discover",
                "acquire",
                "extract",
                "semantics",
                "align",
                "figures",
                "quality",
                "repair",
                "human",
                "revalidate",
                "package",
            ],
            plugins=[
                {"name": name, "title": get_plugin(name).title}
                for name in list_plugins()
            ],
            output_files=[
                "integrated_observations.csv",
                "source_catalog.csv",
                "field_mapping.csv",
                "quality_issues.csv",
                "repair_log.csv",
                "provenance.jsonl",
                "data_quality_report.md",
                "run_manifest.json",
                "figure_overlays/",
                "README_data.md",
            ],
            auto_repair_codes=sorted(auto),
            human_required_note=(
                "Entity identity, cross-source value conflicts, missing-value "
                "imputation, axis/legend reading and unit ambiguity are never "
                "resolved automatically; they become numbered questions."
            ),
            discovery=[
                "sci_data_discover: literature search returns candidate URLs",
                "sci_data_discover: with network access, public data archives "
                "(Zenodo, Dataverse, OpenAlex) return file-level candidates",
                "sci_data_acquire_only: download candidates under policy",
                "sci_data_run: acquire, parse, align, repair and package in one run; "
                "auto_discover searches the archives when network is enabled",
            ],
            data_providers=list(list_providers()),
            resume_saved_work=(
                "sci_data_runs reads this task's saved discovery candidates and "
                "acquisition receipts without network access. Read these before "
                "repeating a search. Discovery candidates are not acquired originals."
            ),
            column_overrides=(
                "sci_data_run(column_overrides_json=[{source, column, observable, unit?}]) "
                "maps a named column explicitly after the user explains it"
            ),
            entity_overrides=(
                "sci_data_run(entity_overrides_json=[{source, entity}]) declares the "
                "object for a table whose rows carry no object column; records are "
                "flagged entity_override"
            ),
            wide_tables=(
                "tables with one time column and one column per band are reshaped "
                "to time/band/mag/e_mag; empty cells stay as explicit missing rows"
            ),
            supported_formats=[
                "csv/tsv/txt/dat (delimiter, encoding and header sniffed; # header lines accepted)",
                "xlsx (each worksheet, cached values only)",
                "json/jsonl (nested payloads flattened)",
                "html (rowspan/colspan grid)",
                "markdown pipe tables",
                "pdf (text-layer table reconstruction, low confidence, flagged)",
                "figures (axis-calibrated point digitisation with overlay check; "
                "chart images can instead be read by the configured cloud chart API)",
            ],
            network_default="disabled; enable only with an explicit host allowlist",
            source_discovery={
                "skill_reader": "sci_data_skill",
                "search": "sci_data_discover supports queries and search_kind=literature/repositories/web/all",
                "original_reader": "sci_data_inspect reads a hash-verified original, bounded text and outbound candidates",
                "independence": "URL count and distinct hashes never establish independent observations; verify original publications and observation attribution",
                "receipts": "discovery, acquisition and inspection JSON persist under the task scientific_data directory",
            },
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_contract(request_json: str, config: RunnableConfig = None) -> str:
    """Compile a research goal into a data requirement contract.

    Send the user's need as a JSON object with keys such as ``research_goal``,
    ``domain`` (``snia_light_curve``, ``solar_cycle`` or ``generic``),
    ``observables``, ``entity_class``, ``coverage_window`` and optional
    ``budget``.  Anything the request leaves open is returned as an ambiguity
    instead of being invented.

    With ``generic``, declare custom fields as objects containing ``name``,
    ``kind``, ``unit`` and ``aliases``. Set ``identifier_fields`` and
    ``time_field`` to their canonical names. Optional ``time_system`` and
    ``reference_mjd`` declare the clock and relative-time reference explicitly.

    Args:
        request_json: JSON object describing the research goal.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON contract with ambiguities and the fingerprint that pins the run.
    """
    try:
        contract = contract_from_request(_payload(request_json, what="request_json"))
        return _result(
            status="ok",
            contract=contract.to_dict(),
            fingerprint=contract.fingerprint,
            ambiguities=list(contract.ambiguities),
        )
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_plan(contract_json: str, config: RunnableConfig = None) -> str:
    """Plan which kinds of sources to look for, in priority order.

    Returns retrieval steps (authored tables, published literature,
    supplements, catalogues, figures) with the role each source plays, so the
    user can see the intended data origin before anything is downloaded.

    Args:
        contract_json: The contract object returned by sci_data_contract.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of retrieval steps and the accepted source roles.
    """
    try:
        contract = contract_from_request(_payload(contract_json, what="contract_json"))
        plugin = plugin_for_contract(contract)
        steps = plan_retrieval(contract, plugin=plugin)
        return _result(
            status="ok",
            steps=[step.to_dict() for step in steps],
            accepted_roles=[str(role) for role in contract.accepted_roles],
        )
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_run(
    request_json: str,
    inputs: list[str] | None = None,
    figures_json: str = "",
    extra_sources_json: str = "",
    answers_json: str = "",
    column_overrides_json: str = "",
    entity_overrides_json: str = "",
    allow_network: bool = False,
    allowed_hosts: list[str] | None = None,
    auto_discover: bool = True,
    discovery_providers: list[str] | None = None,
    discovery_limit: int = 5,
    extract_figures: bool = False,
    run_id: str = "",
    config: RunnableConfig = None,
) -> str:
    """Run the whole integration pipeline and write the data package.

    ``inputs`` are workspace paths to tabulated sources (CSV/TSV/JSON/HTML/
    Markdown/PDF/XLSX).  ``figures_json`` is a JSON list of digitisation
    side-cars: each item carries ``figure_id`` plus axis tick positions
    (``x_ticks``/``y_ticks``) and the plotted series points in pixels, which is
    how a figure becomes numbers.  Network access stays off unless
    ``allow_network`` is true and ``allowed_hosts`` names the permitted hosts.
    With network access on, ``auto_discover`` also searches public data
    archives (Zenodo, Dataverse, OpenAlex) for candidate tables and downloads
    them through the same policy; restrict the archive set with
    ``discovery_providers``.  ``extract_figures`` reads chart images inside PDFs
    through the configured cloud chart API (``JW_FIGURE_CLOUD_ENDPOINT``); it is
    opt-in because every page is a billed call, and a page whose text layer is
    missing stays on the table-OCR path instead.

    ``column_overrides_json`` is a JSON list of explicit column decisions, each
    ``{"source": "table.csv", "column": "SNID", "observable": "object",
    "unit": "mag"}`` (``unit`` optional).  Use it after the package reports
    ``merge_key_absent`` or ``field_unmapped`` for a source the user explained:
    it maps that column deterministically instead of guessing.  The source may
    be the basename or the source id; only the named columns change.
    Time-column decisions may also set ``time_system`` and ``reference_mjd``;
    these override the request defaults and are saved with the field mapping.

    ``entity_overrides_json`` declares a source-level entity when the table has
    no object column but the paper, file name or comment header names the
    object: ``[{"source": "sn2011fe.public.dat", "entity": "SN 2011fe"}]``.
    It is a user decision, not inferred from a file name, and every affected
    record is flagged ``entity_override``.

    Args:
        request_json: JSON research request (see sci_data_contract).
        inputs: Workspace paths of tabulated sources to integrate.
        figures_json: JSON list of figure digitisation side-cars.
        extra_sources_json: JSON list of discovered sources (url/title/role).
        answers_json: JSON object mapping question ids to chosen answers.
        column_overrides_json: JSON list of explicit source-column mappings.
        entity_overrides_json: JSON list of explicit source-level entities.
        allow_network: Whether remote sources may be fetched.
        allowed_hosts: Hostnames permitted when network access is enabled.
        auto_discover: Search public data archives when network is enabled.
        discovery_providers: Archive names to search (default: all enabled).
        discovery_limit: Maximum candidate hits per archive.
        extract_figures: Read chart images through the cloud chart API.
        run_id: Optional run id; a new one is generated when empty.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON summary with counts, blocking questions and the package directory.
    """
    try:
        workspace = _workspace(config)
        contract = contract_from_request(_payload(request_json, what="request_json"))
        figures = tuple(
            FigureInput(
                figure_id=str(item.get("figure_id") or f"figure-{index + 1}"),
                payload=item,
                source_id=str(item.get("source_id") or ""),
                path=str(item.get("path") or ""),
            )
            for index, item in enumerate(
                json.loads(figures_json) if figures_json.strip() else []
            )
        )
        extra = tuple(
            json.loads(extra_sources_json) if extra_sources_json.strip() else ()
        )
        answers = dict(json.loads(answers_json) if answers_json.strip() else {})
        overrides = _object_list(column_overrides_json)
        entity_overrides = _object_list(entity_overrides_json)
        options = WorkflowOptions(
            allow_network=bool(allow_network),
            allowed_hosts=tuple(str(host) for host in (allowed_hosts or ())),
            auto_discover=bool(auto_discover),
            discovery_providers=tuple(
                str(name) for name in (discovery_providers or ())
            ),
            discovery_limit=int(discovery_limit),
            extract_figures=bool(extract_figures),
            figure_inputs=figures,
            extra_sources=extra,
            column_overrides=overrides,
            entity_overrides=entity_overrides,
            answers=answers,
        )
        result = run_integration(
            workspace=workspace,
            contract=contract,
            inputs=_paths(list(inputs or ()), workspace),
            options=options,
            run_id=run_id or None,
        )
        return _result(status="ok", **_run_summary(result))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


def _run_summary(result: Any) -> dict[str, Any]:
    summary = result.summary()
    queue = result.queue
    package = result.package
    return {
        "summary": summary,
        "package_directory": package.directory if package else "",
        "package_files": sorted(package.files) if package else [],
        "open_questions": [
            {
                "request_id": request.request_id,
                "code": str(request.code),
                "question": request.question,
                "options": list(request.options),
                "recommended": request.recommended,
                "blocking": request.blocking,
            }
            for request in (queue.open_requests if queue is not None else ())
        ],
        "limitations": [
            "Digitised points carry pixel-level uncertainty and are marked digitized.",
            "Missing values keep an explicit reason and are never imputed.",
            "Automatically repaired issues are listed in repair_log.csv with before/after hashes.",
        ],
    }


def _discover_archives(
    contract: Any,
    catalog: SourceCatalog,
    *,
    limit: int,
    providers: list[str] | None,
    query: str = "",
) -> dict[str, Any]:
    """Search the public data archives and report only the new candidates."""

    before = set(catalog.sources)
    settings = resolve_provider_settings(None)
    hosts = tuple(
        filter(
            None,
            (
                urlsplit(url).hostname
                for url in (
                    settings.zenodo_endpoint,
                    settings.dataverse_endpoint,
                    settings.openalex_endpoint,
                )
            ),
        )
    )
    report = discover_data_sources(
        contract,
        plugin=plugin_for_contract(contract),
        providers=tuple(str(name) for name in (providers or ())) or None,
        limit=int(limit),
        allow_network=True,
        catalog=catalog,
        settings=settings,
        query=query,
        policy=AcquisitionPolicy.from_contract(
            contract, allow_network=True, allowed_hosts=hosts
        ),
    )
    payload = report.to_dict()
    payload["found"] = [
        record.to_dict()
        for source_id, record in catalog.sources.items()
        if source_id not in before
    ]
    return payload


@tool(parse_docstring=True)
def sci_data_status(run_id: str, config: RunnableConfig = None) -> str:
    """Report the stage receipts and counts of an existing run.

    Args:
        run_id: Run identifier returned by sci_data_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with stage receipts, counts and remaining open questions.
    """
    try:
        _directory, ledger = _run_or_error(_workspace(config), run_id)
        pending = tuple(issue for issue in ledger.issues.values() if issue.blocking)
        return _result(
            status="ok",
            run_id=run_id,
            counts=ledger.summary_counts(),
            stages={
                name: {"state": str(receipt.state), "counts": dict(receipt.counts)}
                for name, receipt in ledger.stages.items()
            },
            blocking_issues=len(pending),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_issues(
    run_id: str,
    severity: str = "",
    state: str = "",
    code: str = "",
    config: RunnableConfig = None,
) -> str:
    """List detected data quality issues, optionally filtered.

    Use this to explain to the user what was found: missing values, duplicate
    rows, unit inconsistencies, cross-source conflicts, unreadable axes and so
    on, each with the evidence and the proposed action.

    Args:
        run_id: Run identifier returned by sci_data_run.
        severity: Optional filter: error, warning or info.
        state: Optional filter: detected, awaiting_human, revalidated, ...
        code: Optional issue code filter, e.g. cross_source_conflict.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of issues with evidence, repair mode and state.
    """
    try:
        _directory, ledger = _run_or_error(_workspace(config), run_id)
        selected = [
            issue
            for issue in ledger.issues.values()
            if (not severity or str(issue.severity) == severity)
            and (not state or str(issue.state) == state)
            and (not code or str(issue.code) == code)
        ]
        selected.sort(key=lambda item: (str(item.severity), item.issue_id))
        return _result(
            status="ok",
            run_id=run_id,
            total=len(selected),
            issues=[_issue_payload(issue) for issue in selected[:_MAX_LISTED_ISSUES]],
            truncated=len(selected) > _MAX_LISTED_ISSUES,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_answer(
    run_id: str, answers_json: str, config: RunnableConfig = None
) -> str:
    """Record reviewer decisions and rewrite the package accordingly.

    Send a JSON object mapping question ids (from the previous run) to one of
    the offered options.  The data only changes where the answer says so; the
    decision is logged in repair_log.csv with the reviewer as actor.

    Args:
        run_id: Run identifier whose questions are being answered.
        answers_json: JSON object of question id to chosen option.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON summary of the updated run and the rewritten package.
    """
    try:
        workspace = _workspace(config)
        answers = dict(_payload(answers_json, what="answers_json"))
        result = apply_human_answers(
            workspace=workspace,
            run_id=run_id,
            answers={str(key): str(value) for key, value in answers.items()},
        )
        return _result(status="ok", **_run_summary(result))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_digitise(figure_json: str, config: RunnableConfig = None) -> str:
    """Turn a plotted series into numbers and verify the reading.

    The side-car JSON holds the axis tick positions and the series marker
    pixels.  The tool fits each axis, converts pixel positions into data values
    with propagated uncertainty, then projects the points back onto the image to
    report the round-trip error.  Every recovered value is marked digitized.

    Args:
        figure_json: JSON side-car with ticks, scale, series points.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with digitised records, overlay residual report and axis issues.
    """
    try:
        payload = _payload(figure_json, what="figure_json")
        calibration = calibration_from_payload(payload)
        figure_id = str(payload.get("figure_id") or "figure")
        observable = str(payload.get("observable") or "value")
        entity = str(payload.get("entity") or "unlabelled")
        records: list[Any] = []
        for series in series_from_payload(payload):
            records.extend(
                digitise_points(
                    series,
                    calibration,
                    entity_id=entity,
                    observable=observable,
                    unit=str(payload.get("unit") or ""),
                    time_system=str(payload.get("time_system") or "mjd"),
                    path=str(payload.get("path") or ""),
                )
            )
        report = overlay_report(records, calibration)
        issue = axis_issue(calibration, figure_id=figure_id)
        mismatch = overlay_issue(report, figure_id=figure_id)
        return _result(
            status="ok",
            figure_id=figure_id,
            calibration=calibration.to_dict(),
            records=[
                {
                    "record_uid": record.record_uid,
                    "entity_id": record.entity_id,
                    "observable": record.observable,
                    "value": record.value,
                    "unit": record.unit,
                    "time": record.time,
                    "uncertainty": record.uncertainty,
                    "value_kind": str(record.value_kind),
                    "value_raw": record.value_raw,
                }
                for record in records[:_MAX_LISTED_RECORDS]
            ],
            overlay=report.to_dict(),
            issues=[
                _issue_payload(item) for item in (issue, mismatch) if item is not None
            ],
            verification_note=(
                "Axis ticks are fitted by least squares, digitised values carry "
                "pixel-level uncertainty, and the overlay report re-projects "
                "every point to prove the calibration."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_package(run_id: str, config: RunnableConfig = None) -> str:
    """List the deliverable files of a run with their hashes.

    Args:
        run_id: Run identifier returned by sci_data_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of package files, digests and row counts.
    """
    try:
        directory, ledger = _run_or_error(_workspace(config), run_id)
        package_dir = directory / "package"
        manifest_path = package_dir / "run_manifest.json"
        if not manifest_path.is_file():
            return _result(
                status="ok", run_id=run_id, package_directory=str(package_dir), files=[]
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _result(
            status="ok",
            run_id=run_id,
            package_directory=str(package_dir),
            artifacts=manifest.get("artifacts") or [],
            counts=manifest.get("counts") or {},
            summary=ledger.summary_counts(),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


def _search_summaries(searches: list) -> list[dict]:
    """Bound status evidence, including correction views of immutable receipts."""
    summaries = []
    for row in searches[:3]:
        summary = {"query": str(row.get("query", ""))[:1000]}
        for key in ("discovery", "data_discovery", "web"):
            value = row.get(key)
            if not isinstance(value, dict):
                continue
            status = value.get("status")
            issues = {
                str(i.get("subject", {}).get("status", ""))
                for i in value.get("issues", [])
                if isinstance(i, dict)
            } - {""}
            corrected = (
                next(
                    (
                        s
                        for s in ("policy_blocked", "unavailable", "failed", "error")
                        if s in issues
                    ),
                    status,
                )
                if status == "no_results"
                else status
            )
            detail = {
                "status": corrected,
                "note": str(value.get("note", ""))[:200],
                "issue_statuses": sorted(issues)[:8],
            }
            if corrected != status:
                detail.update(
                    recorded_status=status,
                    note="Historical empty-result label conflicts with preserved provider failure; original receipt is unchanged.",
                )
            provider = value.get("provider_status", {})
            if isinstance(provider, dict) and provider:
                detail["provider_status"] = {}
                if provider.get("source") is not None:
                    detail["provider_status"]["source"] = str(provider["source"])[:80]
                names = provider.get("providers_queried")
                if isinstance(names, (list, dict)):
                    detail["provider_status"]["providers_queried"] = [
                        str(x)[:80] for x in list(names)[:4]
                    ]
            summary[key] = detail
        summaries.append(summary)
    return summaries


def _search_outcome(searches: list, *, candidates: bool, allow_network: bool) -> str:
    if not allow_network:
        return "policy_blocked"
    if candidates:
        return "candidates_found"
    statuses = [
        v.get("status")
        for row in _search_summaries(searches)
        for k, v in row.items()
        if k != "query" and isinstance(v, dict)
    ]
    bad = {"unavailable", "failed", "error", "policy_blocked"}
    if statuses and set(statuses) == {"policy_blocked"}:
        return "policy_blocked"
    if statuses and all(s in bad for s in statuses):
        return "unavailable"
    if any(s in bad or s == "partial" for s in statuses):
        return "partial"
    return "no_usable_candidates"


@tool(parse_docstring=True)
def sci_data_runs(
    kind: str = "all",
    receipt_path: str = "",
    offset: int = 0,
    limit: int = 2,
    item_offset: int = 0,
    item_limit: int = 3,
    content_offset: int = 0,
    content_limit: int = 6000,
    inspection_query: str = "",
    config: RunnableConfig = None,
) -> str:
    """Recover this task's saved discovery, acquisition and integration work.

    Use on continuation before repeating network searches. Discovery candidates
    remain unverified; acquisition results retain their actual success/failure
    status. Results are paginated to avoid inaccessible large-tool offloads.

    Args:
        kind: all, discovery, acquisition, integration, inspection, or handoff.
        receipt_path: Optional exact saved discovery/acquisition receipt from this workspace, for targeted recovery.
        offset: Zero-based receipt page offset, up to 10000.
        limit: Receipts per kind, 1..5, default 2.
        item_offset: Zero-based result/candidate offset within each receipt.
        item_limit: Results/candidates per receipt, 1..5, default 3.
        content_offset: Handoff report or saved inspection preview character offset, zero-based.
        content_limit: Text page length, 256..12000 characters; total returned text is capped at 12000 across receipts.
        inspection_query: Optional literal case-insensitive filter over saved inspection metadata/text, at most 200 characters; no new original inspection or network call.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with integration runs, saved discovery candidates and acquisition receipts.
    """
    try:
        if kind not in {
            "all",
            "discovery",
            "acquisition",
            "integration",
            "inspection",
            "handoff",
        }:
            raise ValueError("unsupported receipt kind")
        if not (
            0 <= offset <= 10000
            and 0 <= item_offset <= 10000
            and 1 <= limit <= 5
            and 1 <= item_limit <= 5
        ):
            raise ValueError("offsets must be 0..10000 and page limits 1..5")
        workspace = _workspace(config)
        selected = (
            _paths([receipt_path], workspace)[0].resolve() if receipt_path else None
        )
        root = run_dirs(workspace)
        if not 0 <= content_offset <= 1_000_000 or not 256 <= content_limit <= 12000:
            raise ValueError("invalid handoff content page bounds")
        if len(inspection_query) > 200:
            raise ValueError("inspection_query must be at most 200 characters")
        page_length = min(content_limit, 12000 // limit)
        if kind == "handoff":
            from jw.scientific_data.handoff import read_reply

            paths = sorted(
                (workspace / "scientific_data/handoffs").glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if selected:
                paths = [p for p in paths if p.resolve() == selected]
            entries = []
            for handoff_path in paths[offset : offset + limit]:
                saved, text = read_reply(workspace, str(handoff_path))
                entries.append(
                    {
                        **saved,
                        "content_offset": content_offset,
                        "content": text[content_offset : content_offset + page_length],
                        "next_content_offset": min(
                            len(text), content_offset + page_length
                        ),
                        "content_total_chars": len(text),
                    }
                )
            return _result(
                status="ok",
                handoff_receipts=entries,
                pagination={"total": len(paths), "offset": offset, "limit": limit},
            )
        runs: list[dict[str, Any]] = []
        acquisitions: list[dict[str, Any]] = []

        def read_saved(path: Path) -> dict[str, Any] | None:
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(workspace.resolve())
                or not resolved.is_file()
            ):
                return None
            if resolved.stat().st_size > 4_000_000:
                return None
            try:
                value = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return value if isinstance(value, dict) else None

        if kind == "inspection":
            paths = sorted(
                root.glob("*/inspections/inspection-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if selected:
                paths = [p for p in paths if p.resolve() == selected]
            found = []
            for path in paths[:1000]:
                if path.stat().st_size > 512000:
                    continue
                saved = read_saved(path)
                if saved is None or (
                    inspection_query
                    and inspection_query.casefold()
                    not in json.dumps(saved, ensure_ascii=False).casefold()
                ):
                    continue
                found.append((path, saved))
            if selected and not found:
                raise ValueError(
                    "saved inspection not found in this task or did not match inspection_query"
                )
            entries = []
            for path, saved in found[offset : offset + limit]:
                text = str(saved.get("preview", ""))
                entries.append(
                    {
                        **{
                            k: saved[k]
                            for k in (
                                "status",
                                "source_id",
                                "path",
                                "sha256",
                                "url",
                                "final_url",
                                "size",
                                "content_type",
                                "selection",
                            )
                            if k in saved
                        },
                        "receipt_path": str(path),
                        "preview": text[content_offset : content_offset + page_length],
                        "content_offset": content_offset,
                        "next_content_offset": min(
                            len(text), content_offset + page_length
                        ),
                        "content_total_chars": len(text),
                        "evidence_kind": "saved_inspection_not_a_new_original_check",
                    }
                )
            return _result(
                status="ok",
                inspection_receipts=entries,
                pagination={
                    "total": len(found),
                    "offset": offset,
                    "limit": limit,
                    "inventory_truncated": len(paths) > 1000,
                },
            )

        discoveries = []
        discovery_root = workspace / "scientific_data" / "discovery"
        discovery_paths = sorted(
            discovery_root.glob("discovery-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if selected:
            discovery_paths = [
                path for path in discovery_paths if path.resolve() == selected
            ]
        discovery_total = len(discovery_paths)
        for path in (
            discovery_paths[offset : offset + limit]
            if kind in {"all", "discovery"}
            else []
        ):
            saved = read_saved(path)
            if saved is None:
                continue
            discoveries.append(
                {
                    "receipt_path": str(path),
                    "search_status": _search_outcome(
                        saved.get("searches", []),
                        candidates=bool(saved.get("candidates")),
                        allow_network=saved.get("search_status") != "policy_blocked",
                    )
                    if saved.get("searches")
                    else saved.get("search_status"),
                    "recorded_search_status": saved.get("search_status"),
                    "searches": _search_summaries(saved.get("searches", [])),
                    "queries": [s.get("query") for s in saved.get("searches", [])],
                    "candidate_total": len(saved.get("candidates", [])),
                    "item_offset": item_offset,
                    "candidates": [
                        {
                            key: candidate[key]
                            for key in (
                                "source_id",
                                "title",
                                "url",
                                "role",
                                "provider",
                                "identifier",
                                "license",
                                "independence",
                            )
                            if key in candidate
                        }
                        for candidate in saved.get("candidates", [])[
                            item_offset : item_offset + item_limit
                        ]
                    ],
                    "evidence_kind": "discovery_candidates_not_acquired_originals",
                }
            )
        if root.is_dir():
            acquisition_paths = []
            for entry in sorted(root.iterdir()):
                versions = list(entry.glob("acquisition-*.json"))
                acquisition_paths.extend(versions or [entry / "acquisition.json"])
            acquisition_paths = sorted(
                (path for path in acquisition_paths if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if selected:
                acquisition_paths = [
                    path for path in acquisition_paths if path.resolve() == selected
                ]
            acquisition_total = len(acquisition_paths)
            for path in (
                acquisition_paths[offset : offset + limit]
                if kind in {"all", "acquisition"}
                else []
            ):
                acquisition = read_saved(path)
                if acquisition is not None:
                    acquisitions.append(
                        {
                            "run_id": path.parent.name,
                            "receipt_path": str(path),
                            "result_total": len(acquisition.get("results", [])),
                            "item_offset": item_offset,
                            "results": acquisition.get("results", [])[
                                item_offset : item_offset + item_limit
                            ],
                        }
                    )
            for entry in sorted(root.iterdir()):
                if kind not in {"all", "integration"} or selected:
                    break
                ledger_path = entry / "ledger.json"
                if not ledger_path.is_file():
                    continue
                try:
                    ledger = load_ledger(ledger_path)
                except (OSError, ValueError):
                    continue
                runs.append(
                    {
                        "run_id": ledger.run_id,
                        "goal": ledger.contract.research_goal,
                        "domain": ledger.contract.domain_plugin,
                        "counts": ledger.summary_counts(),
                        "directory": str(entry),
                    }
                )
        if not root.is_dir():
            acquisition_total = 0
        if selected and not discoveries and not acquisitions:
            raise ValueError(
                "receipt not found in selected kind/page; use offset=0 with its matching kind"
            )
        return _result(
            status="ok",
            runs=runs[offset : offset + limit],
            discovery_receipts=discoveries,
            acquisition_receipts=acquisitions,
            pagination={
                "offset": offset,
                "limit": limit,
                "item_offset": item_offset,
                "item_limit": item_limit,
                "discovery_total": discovery_total,
                "acquisition_total": acquisition_total,
                "integration_total": len(runs),
                "next_offset": offset + limit,
                "note": "Use kind and receipt_path to recover a specific receipt; item_offset pages candidates/results. Candidate summaries omit long provider metadata; saved originals and receipts are unchanged.",
            },
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_acquire_only(
    request_json: str,
    urls_json: str,
    allow_network: bool = False,
    allowed_hosts: list[str] | None = None,
    run_id: str = "",
    config: RunnableConfig = None,
) -> str:
    """Fetch discovered sources under policy without integrating them yet.

    Useful when the agent has already found candidate URLs and wants to show
    the user what would be downloaded, what was refused by policy, and what
    failed, before committing to a full run.

    Args:
        request_json: JSON research request (see sci_data_contract).
        urls_json: JSON list of {"url": ..., "role": ..., "title": ...} entries.
        allow_network: Whether remote sources may be fetched.
        allowed_hosts: Hostnames permitted when network access is enabled.
        run_id: Optional run id for the download directory.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON acquisition report with per-source status and refusals.
    """
    try:
        workspace = _workspace(config)
        contract = contract_from_request(_payload(request_json, what="request_json"))
        catalog = SourceCatalog()
        for item in json.loads(urls_json) if urls_json.strip() else []:
            url = str(item.get("url") or "")
            if urlsplit(url).scheme in {"file", ""}:
                from jw.scientific_data.acquisition import _local_path

                _paths([str(_local_path(url))], workspace)
            catalog.add(
                url=url,
                title=str(item.get("title") or ""),
                role=str(item.get("role") or "unknown"),
                provider=str(item.get("provider") or "discovery"),
                identifier=str(item.get("identifier") or ""),
                license=str(item.get("license") or ""),
                discovered_via=str(item.get("discovered_via") or ""),
                notes=tuple(str(note) for note in item.get("notes", [])),
            )
        policy = AcquisitionPolicy.from_contract(
            contract,
            allow_network=bool(allow_network),
            allowed_hosts=tuple(str(host) for host in (allowed_hosts or ())),
        )
        target = workspace_run_dir(workspace, run_id or "acquire-preview")
        report = acquire(
            catalog,
            policy=policy,
            workdir=target / "sources",
            contract=contract,
        )
        receipt = {
            **report.to_dict(),
            "results": [item.to_dict() for item in report.results],
            "catalog": [source.to_dict() for source in report.catalog.to_records()],
            "independence": "unverified; inspect originals and observation attribution",
        }
        historical = target / f"acquisition-{uuid4().hex[:12]}.json"
        write_receipt(historical, receipt)
        write_receipt(target / "acquisition.json", receipt)
        return _result(
            status="ok",
            stats=dict(report.stats),
            results=[item.to_dict() for item in report.results],
            issues=[_issue_payload(issue) for issue in report.issues],
            receipt_path=str(historical),
            run_id=target.name,
            catalogue=receipt["catalog"],
            independence=receipt["independence"],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_inspect(
    run_id: str,
    source_id: str,
    receipt_path: str = "",
    max_chars: int = 12000,
    pdf_start_page: int = 1,
    pdf_page_count: int = 3,
    search_text: str = "",
    archive_member: str = "",
    member_start: int = 0,
    config: RunnableConfig = None,
) -> str:
    """Inspect an acquired original and find candidate attachment/data links.

    This reads saved bytes, verifies their hash and performs no network calls.
    PDF inspection reads a bounded page span and URI annotations; it is
    not OCR, figure extraction or a scientific measurement parser.

    Args:
        run_id: Acquisition run id returned by sci_data_acquire_only.
        source_id: Acquired source id in that run's receipt.
        receipt_path: Optional exact historical acquisition receipt path.
        max_chars: Bounded text preview length, at most 20000.
        pdf_start_page: First PDF page to inspect, one-based.
        pdf_page_count: PDF pages to scan, 1..50, default 3.
        search_text: Optional literal case-insensitive search, at most 200 chars; PDF search covers only the requested page span and returns one context per matching page.
        archive_member: Exact relative TAR/ZIP member name to read as raw text, at most 128000 expanded bytes; no extraction or execution.
        member_start: Zero-based inventory offset, 0..10000; inventory returns at most 100 members, TAR scan stops within 1000 additional entries or 50 MB expanded bytes.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with original hash, text preview, candidate links and saved receipt.
    """
    try:
        workspace = _workspace(config)
        path = (
            _paths([receipt_path], workspace)[0]
            if receipt_path
            else workspace_run_dir(workspace, run_id) / "acquisition.json"
        )
        from pypdf.errors import PyPdfError

        try:
            inspection = inspect_original(
                workspace,
                path,
                source_id,
                max_chars=max_chars,
                pdf_start_page=pdf_start_page,
                pdf_page_count=pdf_page_count,
                search_text=search_text,
                archive_member=archive_member,
                member_start=member_start,
            )
        except PyPdfError as exc:
            raise ValueError(f"PDF inspection failed: {type(exc).__name__}") from exc
        return _result(**inspection)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_discover(
    request_json: str,
    limit: int = 5,
    allow_network: bool = False,
    extra_urls_json: str = "",
    providers: list[str] | None = None,
    queries: list[str] | None = None,
    search_kind: str = "literature",
    literature_source: str = "openalex",
    config: RunnableConfig = None,
) -> str:
    """Find candidate sources for a research request before downloading anything.

    Discovery never invents a reference: it asks the literature search and, when
    network access is allowed, the public data archives (Zenodo, Dataverse,
    OpenAlex) for hits. It reports whether each search ran, failed, was refused
    by policy, or came back empty. Nothing is fetched here — pass the returned
    URLs to sci_data_acquire_only or sci_data_run once the user has seen them.

    Args:
        request_json: JSON research request (see sci_data_contract).
        limit: Maximum number of hits to consider per search.
        allow_network: Whether discovery may reach the network.
        extra_urls_json: JSON list of URLs the user supplied directly.
        providers: Up to two explicit archive names; default searches only the first enabled archive.
        queries: Up to three explicit search strings; use synonyms or narrow a missing requirement.
        search_kind: catalogues (literature and repositories), literature, repositories, web, or all.
        literature_source: One literature provider name, default openalex; all is refused to avoid hidden fanout.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON discovery report with candidate sources and per-query status.
    """
    try:
        workspace = _workspace(config)
        contract = contract_from_request(_payload(request_json, what="request_json"))
        if not literature_source.strip() or literature_source.casefold() == "all":
            raise ValueError("choose one explicit literature_source, not all")
        if providers is not None and not 1 <= len(providers) <= 2:
            raise ValueError("choose one or two archive providers per call")
        if not 1 <= limit <= 5:
            raise ValueError("limit must be 1..5; use narrow queries before expanding")
        selected_providers = providers
        if selected_providers is None:
            selected_providers = list(resolve_provider_settings().providers[:1])
        if search_kind not in {
            "catalogues",
            "literature",
            "repositories",
            "web",
            "all",
        }:
            raise ValueError("unsupported search_kind")
        from jw.scientific_data.discovery import discovery_query

        texts = list(
            dict.fromkeys(
                str(q).strip()
                for q in (queries or [discovery_query(contract)])
                if str(q).strip()
            )
        )
        if not texts or len(texts) > 3 or any(len(q) > 1000 for q in texts):
            raise ValueError(
                "supply one to three search strings of at most 1000 characters"
            )
        extra = json.loads(extra_urls_json) if extra_urls_json.strip() else []
        searches = []
        catalog = SourceCatalog()
        for url in extra:
            catalog.add(
                url=str(url),
                title=str(url),
                role="unknown",
                provider="user_url",
                discovered_via="user_input",
            )
        for query in texts:
            entry = {"query": query}
            if search_kind in {"catalogues", "literature", "all"}:
                report = discover_sources(
                    contract,
                    limit=limit,
                    allow_network=bool(allow_network),
                    catalog=catalog,
                    extra_urls=[str(item) for item in extra],
                    query=query,
                    searcher=(
                        lambda text, count: literature_searcher(
                            text, count, source=literature_source
                        )
                    )
                    if allow_network
                    else None,
                )
                entry["discovery"] = report.to_dict()
            if search_kind in {"catalogues", "repositories", "all"} and allow_network:
                entry["data_discovery"] = _discover_archives(
                    contract,
                    catalog,
                    limit=limit,
                    providers=selected_providers,
                    query=query,
                )
            if search_kind in {"web", "all"}:
                entry["web"] = (
                    web_candidates(query, workspace=workspace, limit=limit)
                    if allow_network
                    else {"status": "policy_blocked", "found": []}
                )
                for hit in entry["web"]["found"]:
                    catalog.add(
                        url=hit["url"],
                        title=hit["title"],
                        role="unknown",
                        provider="web",
                        discovered_via=hit["discovered_via"],
                        notes=(hit["snippet"], "independence_unverified"),
                    )
            searches.append(entry)
        first = searches[0]
        payload = dict(
            status="ok",
            search_status=_search_outcome(
                searches,
                candidates=bool(len(catalog)),
                allow_network=bool(allow_network),
            ),
            discovery=first.get("discovery", {"status": "not_requested", "found": []}),
            data_discovery=first.get("data_discovery"),
            searches=searches,
            urls=[record.url for record in catalog.to_records()],
            candidates=[record.to_dict() for record in catalog.to_records()],
            independence="unverified; titles, URL count and distinct hashes do not establish independent observations",
            suggested_next_step="Acquire promising candidates under an explicit host allowlist, inspect saved originals, follow evidenced links and search unmet requirements. Preserve failed and excluded candidates.",
        )
        path = (
            workspace
            / "scientific_data"
            / "discovery"
            / f"discovery-{uuid4().hex[:12]}.json"
        )
        payload["receipt_path"] = str(path)
        write_receipt(path, payload)
        # Keep the complete provider evidence on disk, but never return its
        # duplicate nested catalogues to a restricted agent as one huge result.
        compact = {
            "status": payload["status"],
            "search_status": payload["search_status"],
            "receipt_path": str(path),
            "candidate_total": len(payload["candidates"]),
            "discovery": {
                "status": payload["discovery"]["status"],
                "found": [],
            },
            "searches": _search_summaries(searches),
            "candidates": [],
            "urls": [],
            "independence": payload["independence"],
            "read_more": "Use sci_data_runs(kind='discovery', receipt_path=this receipt_path, item_offset=..., item_limit=...) offline; do not repeat the network query.",
            "provider_scope": {
                "literature_source": literature_source
                if allow_network and search_kind in {"literature", "catalogues", "all"}
                else None,
                "repositories": selected_providers
                if allow_network
                and search_kind in {"repositories", "catalogues", "all"}
                else [],
                "web": bool(allow_network and search_kind in {"web", "all"}),
            },
        }
        for candidate in payload["candidates"][:5]:
            projected = {
                key: candidate[key]
                for key in (
                    "source_id",
                    "title",
                    "url",
                    "role",
                    "provider",
                    "identifier",
                    "discovered_via",
                )
                if key in candidate
            }
            if (
                len(
                    json.dumps(
                        {
                            **compact,
                            "candidates": [*compact["candidates"], projected],
                            "discovery": {
                                **compact["discovery"],
                                "found": [*compact["discovery"]["found"], projected],
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                > 12000
            ):
                break
            compact["candidates"].append(projected)
            compact["discovery"]["found"].append(projected)
            compact["urls"].append(candidate["url"])
        compact["candidates_truncated"] = len(compact["candidates"]) < len(
            payload["candidates"]
        )
        return _result(**compact)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True, return_direct=True)
def sci_data_solar_monthly(
    start_month: str,
    end_month: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    allow_network: bool = False,
    output_dir: str = "",
    config: RunnableConfig = None,
) -> Command:
    """Acquire and integrate official SILSO and NOAA monthly solar observations.

    Use for monthly total sunspot number plus observed F10.7, with no preloaded
    input required. The maintained official catalogue resolves source addresses;
    this is not a literature search. Uses the shared acquisition policy with two
    official hosts and two downloads. Preserves raw files, per-value provenance,
    missing flags and full precision; conflicts fail instead of selecting values.
    Returns merged_monthly.csv, sources.json, quality_report.json, findings.md
    and replay.py. Only enable network when the user requested public retrieval.

    Args:
        start_month: First month, YYYY-MM, inclusive.
        end_month: Last month, YYYY-MM, inclusive.
        tool_call_id: Runtime-injected identifier for the tool receipt.
        allow_network: User has authorized downloading official public data.
        output_dir: Optional new directory relative to the task workspace.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with actual status, row counts, quality findings and output paths.
    """
    from uuid import uuid4

    from jw.scientific_data.solar_monthly import (
        describe_result,
        integrate_solar_monthly,
    )

    try:
        root = _workspace(config)
        directory = _paths(
            [output_dir or f"scientific_data/solar-monthly-{uuid4().hex[:12]}"], root
        )[0]
        result = integrate_solar_monthly(
            directory, start_month, end_month, allow_network=allow_network
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"status": "error", "error": str(exc)}
    receipt = dict(
        kind="solar_monthly_integration", summary=describe_result(result), **result
    )
    return Command(
        update={
            "messages": [
                ToolMessage(content=_result(**receipt), tool_call_id=tool_call_id)
            ],
            "structured_response": receipt,
        }
    )


SCIENTIFIC_DATA_TOOLS = [
    sci_data_skill,
    sci_data_inspect,
    sci_data_solar_monthly,
    sci_data_capabilities,
    sci_data_contract,
    sci_data_plan,
    sci_data_run,
    sci_data_status,
    sci_data_issues,
    sci_data_answer,
    sci_data_digitise,
    sci_data_package,
    sci_data_runs,
    sci_data_acquire_only,
    sci_data_discover,
]

register_tool_bundle("scientific-data", SCIENTIFIC_DATA_TOOLS, include_in_main=False)
