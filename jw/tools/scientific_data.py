"""Tool surface for multi-source scientific data integration.

The agent asks for a research goal; these tools compile it into a data
requirement contract, plan and fetch sources under policy, extract and align the
tables, detect problems, repair only what is provably reversible, and write a
data package with record-level provenance.  Anything the kernel refuses to
decide lands in a numbered question queue instead of being guessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from jw.scientific_data.acquisition import acquire
from jw.scientific_data.contracts import IssueCode, default_repair_mode
from jw.scientific_data.discovery import discover_sources
from jw.scientific_data.domain import get_plugin, list_plugins
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
                "sci_data_acquire_only: download candidates under policy",
                "sci_data_run: acquire, parse, align, repair and package in one run",
            ],
            column_overrides=(
                "sci_data_run(column_overrides_json=[{source, column, observable, unit?}]) "
                "maps a named column explicitly after the user explains it"
            ),
            supported_formats=[
                "csv/tsv/txt/dat (delimiter, encoding and header sniffed)",
                "xlsx (each worksheet, cached values only)",
                "json/jsonl (nested payloads flattened)",
                "html (rowspan/colspan grid)",
                "markdown pipe tables",
                "pdf (text-layer table reconstruction, low confidence, flagged)",
                "figures (axis-calibrated point digitisation with overlay check)",
            ],
            network_default="disabled; enable only with an explicit host allowlist",
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
        plugin = get_plugin(contract.domain_plugin)
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
    allow_network: bool = False,
    allowed_hosts: list[str] | None = None,
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

    ``column_overrides_json`` is a JSON list of explicit column decisions, each
    ``{"source": "table.csv", "column": "SNID", "observable": "object",
    "unit": "mag"}`` (``unit`` optional).  Use it after the package reports
    ``merge_key_absent`` or ``field_unmapped`` for a source the user explained:
    it maps that column deterministically instead of guessing.  The source may
    be the basename or the source id; only the named columns change.

    Args:
        request_json: JSON research request (see sci_data_contract).
        inputs: Workspace paths of tabulated sources to integrate.
        figures_json: JSON list of figure digitisation side-cars.
        extra_sources_json: JSON list of discovered sources (url/title/role).
        answers_json: JSON object mapping question ids to chosen answers.
        column_overrides_json: JSON list of explicit source-column mappings.
        allow_network: Whether remote sources may be fetched.
        allowed_hosts: Hostnames permitted when network access is enabled.
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
        overrides = tuple(
            item
            for item in (
                json.loads(column_overrides_json)
                if column_overrides_json.strip()
                else []
            )
            if isinstance(item, dict)
        )
        options = WorkflowOptions(
            allow_network=bool(allow_network),
            allowed_hosts=tuple(str(host) for host in (allowed_hosts or ())),
            figure_inputs=figures,
            extra_sources=extra,
            column_overrides=overrides,
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


@tool(parse_docstring=True)
def sci_data_runs(config: RunnableConfig = None) -> str:
    """List the integration runs recorded in this task workspace.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of run ids with contract goals and record counts.
    """
    try:
        workspace = _workspace(config)
        root = run_dirs(workspace)
        runs: list[dict[str, Any]] = []
        if root.is_dir():
            for entry in sorted(root.iterdir()):
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
        return _result(status="ok", runs=runs)
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
            catalog.add(
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                role=str(item.get("role") or "unknown"),
                provider=str(item.get("provider") or "discovery"),
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
        return _result(
            status="ok",
            stats=dict(report.stats),
            results=[item.to_dict() for item in report.results],
            issues=[_issue_payload(issue) for issue in report.issues],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def sci_data_discover(
    request_json: str,
    limit: int = 5,
    allow_network: bool = False,
    extra_urls_json: str = "",
    config: RunnableConfig = None,
) -> str:
    """Find candidate sources for a research request before downloading anything.

    Discovery never invents a reference: it asks the literature search for hits
    and reports whether the search ran, failed, was refused by policy, or came
    back empty. Nothing is fetched here — pass the returned URLs to
    sci_data_acquire_only or sci_data_run once the user has seen them.

    Args:
        request_json: JSON research request (see sci_data_contract).
        limit: Maximum number of literature hits to consider.
        allow_network: Whether the literature search may reach the network.
        extra_urls_json: JSON list of URLs the user supplied directly.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON discovery report with candidate sources and per-query status.
    """
    try:
        workspace = _workspace(config)
        del workspace
        contract = contract_from_request(_payload(request_json, what="request_json"))
        extra = json.loads(extra_urls_json) if extra_urls_json.strip() else []
        report = discover_sources(
            contract,
            limit=limit,
            allow_network=bool(allow_network),
            extra_urls=[str(item) for item in extra],
        )
        return _result(
            status="ok",
            discovery=report.to_dict(),
            urls=[record.url for record in report.found],
            suggested_next_step=(
                "Show these candidates to the user, then call sci_data_run with "
                "extra_sources_json, or sci_data_acquire_only to download first."
            ),
        )
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
