"""Tool surface for research influence analysis and bias explanation.

The agent gets a research question and a set of papers; these tools build or load
a frozen cohort, parse the papers it can legally obtain, fit an interval model,
explain each factor with a delta and a confidence interval, audit ten bias routes,
and write a bundle in which every number can be traced back to a file.

Two things are refused on purpose: a prediction without a reference population,
and a demographic inference from author names.  Both refusals are part of the
result, not an error the caller should work around.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.research_influence.access import (
    AccessPolicy,
    acquisition_request,
    candidates_from_openalex,
    decide_access,
    rank_candidates,
)
from jw.research_influence.bias import AUDIT_LABELS, AUDITS
from jw.research_influence.cohort import CohortSnapshot, synthetic_cohort
from jw.research_influence.paper import build_paper
from jw.research_influence.providers.base import ProviderPolicy
from jw.research_influence.providers.openalex import fetch_work, search_works
from jw.research_influence.transport import PoliteTransport, UrllibTransport
from jw.research_influence.workflow import (
    RunOptions,
    load_run,
    paper_from_upload,
    run_influence,
)
from jw.workspaces import workspace_root_from_config

from .registry import register_tool_bundle

COHORT_DIR = "cohorts"
_MAX_LISTED = 25


def _result(**payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _workspace(config: RunnableConfig | None) -> Path:
    return workspace_root_from_config(config)


def _root(workspace: Path) -> Path:
    path = workspace / "research_influence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cohort_dir(workspace: Path) -> Path:
    path = _root(workspace) / COHORT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(value: str, workspace: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    target = candidate.resolve()
    root = workspace.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path escaped the task workspace: {value}")
    return target


def _snapshot_path(workspace: Path, name: str) -> Path:
    cohort_root = _cohort_dir(workspace)
    candidate = Path(name)
    if candidate.suffix == ".json":
        if candidate.is_absolute():
            return _resolve(str(candidate), workspace)
        inside = cohort_root / candidate.name
        if inside.is_file():
            return inside
        return _resolve(str(candidate), workspace)
    target = cohort_root / f"{name}.json"
    if target.is_file():
        return target
    return _resolve(name, workspace)


def _apply_reference_year(
    snapshot: CohortSnapshot, reference_year: int
) -> CohortSnapshot:
    if not reference_year or reference_year == snapshot.reference_year:
        return snapshot
    return CohortSnapshot(
        cohort_id=snapshot.cohort_id,
        papers=snapshot.papers,
        query=snapshot.query,
        source=snapshot.source,
        created_at=snapshot.created_at,
        reference_year=reference_year,
        filters=dict(snapshot.filters),
        notes=snapshot.notes,
    )


def _run_dir(workspace: Path, run_id: str) -> Path:
    directory = _root(workspace) / run_id
    if not (directory / "run.json").is_file():
        raise ValueError(f"unknown run: {run_id}")
    return directory


def _transport(allow_network: bool, *, user_agent: str = "") -> UrllibTransport | None:
    if not allow_network:
        return None
    inner = UrllibTransport(**({"user_agent": user_agent} if user_agent else {}))
    return PoliteTransport(inner, min_interval=1.0)


def _transport_or_refuse(allow_network: bool) -> tuple[Any, str]:
    transport = _transport(allow_network)
    if transport is None:
        return None, (
            "network_disabled: no request was sent. Pass allow_network=true only "
            "when the operator has agreed to online lookups."
        )
    return transport, ""


@tool(parse_docstring=True)
def infl_capabilities(config: RunnableConfig = None) -> str:
    """Describe what the influence pipeline can and cannot do.

    Use this first. It lists the acquisition ladder, the model, the explanation
    methods, the ten bias audits (including the two that are deliberately not
    attempted) and the files the bundle contains.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON describing the ladder, the model, the audits and the outputs.
    """
    try:
        del config
        return _result(
            status="ok",
            stages=["load", "features", "model", "explain", "bias", "report"],
            ladder=[
                "user_supplied",
                "open_access_gold",
                "open_access_green",
                "free_to_read",
                "preprint",
                "publisher_tdm",
                "institutional_proxy",
                "abstract_only",
                "metadata_only",
                "acquisition_requested",
            ],
            refusals=[
                "No shadow-library mirrors (Sci-Hub, LibGen, Z-Library) are ever "
                "contacted; the host list is refused by policy before any request.",
                "Credentials are read from the operator's environment for their own "
                "subscriptions; they are never shared, guessed or stored.",
                "Gender, ethnicity and career age are never inferred from names.",
                "A prediction without a frozen reference cohort is refused.",
            ],
            model={
                "target": "citations within N years of publication (default 3)",
                "kind": "three quantile regressors (0.1/0.5/0.9)",
                "output": "80% interval plus median, never a single score",
                "guards": [
                    "right-censored papers are excluded from training",
                    "quantile crossings are repaired and counted",
                    "training-set range is reported so extrapolation is visible",
                ],
            },
            explanations=[
                "ablation delta vs cohort reference value, with bootstrap 95% CI",
                "permutation importance (rank correlation drop) over several seeds",
                "partial dependence on a quantile grid",
                "nearest-neighbour comparators in feature space",
                "tail calibration by observed-count bucket",
            ],
            audits=[
                {"name": name, "label": AUDIT_LABELS.get(name, name)} for name in AUDITS
            ],
            not_attempted={
                "author_demographics": "name-based inference is unreliable",
                "self_citation": "needs author-level reference ownership, absent here",
            },
            output_files=[
                "papers.csv",
                "predictions.csv",
                "factors.csv",
                "comparators.csv",
                "importance.csv",
                "bias_audit.csv",
                "bias_audit.md",
                "model_card.json",
                "metrics.json",
                "provenance.jsonl",
                "report.md",
                "README.md",
                "manifest.json",
            ],
            suggested_flow=[
                "infl_cohorts / infl_snapshot_synthetic / infl_snapshot_search: get a cohort",
                "infl_fetch_paper or infl_parse_pdf: add papers the operator supplied",
                "infl_run: fit, explain, audit and write the bundle",
                "infl_status / infl_report / infl_bias / infl_explain: report back",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_cohorts(config: RunnableConfig = None) -> str:
    """List the cohort snapshots stored in this workspace.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of snapshot files with paper counts, source and fingerprint.
    """
    try:
        workspace = _workspace(config)
        directory = _cohort_dir(workspace)
        items = []
        for path in sorted(directory.glob("*.json")):
            try:
                snapshot = CohortSnapshot.load(path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                items.append({"name": path.stem, "error": str(exc)})
                continue
            items.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "papers": len(snapshot.papers),
                    "source": snapshot.source,
                    "query": snapshot.query,
                    "reference_year": snapshot.reference_year,
                    "synthetic": snapshot.is_synthetic,
                    "fingerprint": snapshot.fingerprint(),
                }
            )
        return _result(status="ok", cohorts=items, count=len(items))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_snapshot_synthetic(
    name: str = "demo",
    count: int = 150,
    seed: int = 4,
    reference_year: int = 0,
    config: RunnableConfig = None,
) -> str:
    """Freeze a reproducible synthetic cohort for testing the pipeline offline.

    The generator encodes known effects (latent quality driving venue, authors,
    references, abstract length and data statements). Metrics computed on it
    measure the pipeline, not the astrophysics; say that when reporting.

    Args:
        name: Snapshot name stored under ``cohorts/<name>.json``.
        count: Number of papers to generate.
        seed: Generator seed; identical seed gives an identical snapshot.
        reference_year: Year the citation window is measured at (0 = current).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the snapshot path, fingerprint and paper count.
    """
    try:
        workspace = _workspace(config)
        snapshot = synthetic_cohort(
            count=int(count), seed=int(seed), reference_year=int(reference_year or 0)
        )
        path = _cohort_dir(workspace) / f"{name.strip() or 'demo'}.json"
        snapshot.write(path)
        return _result(
            status="ok",
            path=str(path),
            name=path.stem,
            papers=len(snapshot.papers),
            reference_year=snapshot.reference_year,
            fingerprint=snapshot.fingerprint(),
            honesty_note=(
                "Synthetic cohort: it has no field-level provenance and no real "
                "bibliometrics. Report its numbers as pipeline validation only."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_snapshot_search(
    query: str,
    name: str = "",
    limit: int = 40,
    reference_year: int = 0,
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Search OpenAlex and freeze the result as a cohort snapshot.

    Needs network access, which is off by default. Every paper keeps its route
    (metadata only here: no full text is downloaded) and its field provenance.

    Args:
        query: Topic or title query, e.g. "supernova Ia light curve cosmology".
        name: Snapshot name (defaults to a slug of the query).
        limit: Maximum papers to freeze.
        reference_year: Year the citation window is measured at (0 = current).
        allow_network: Must be true for this tool to make any request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the snapshot path or the reason no request was made.
    """
    try:
        workspace = _workspace(config)
        transport, refusal = _transport_or_refuse(bool(allow_network))
        if transport is None:
            return _result(status="error", error=refusal)
        policy = ProviderPolicy.from_environment()
        result = search_works(
            query, transport=transport, policy=policy, per_page=min(int(limit), 200)
        )
        if not result.ok:
            return _result(
                status="error",
                error=f"openalex_{result.status.value}",
                detail=result.note,
                url=result.url,
            )
        payload = result.payload or {}
        records = []
        for item in (payload.get("results") or [])[: int(limit)]:
            parsed = dict(item)
            records.append(parsed)
        papers = []
        for parsed in records:
            work = _parse_work(parsed)
            if not work:
                continue
            doi = str(work.get("doi") or "")
            access = decide_access(
                doi,
                rank_candidates(
                    doi,
                    candidates_from_openalex(work.get("locations") or ()),
                    policy=AccessPolicy(allow_network=False),
                ),
                has_abstract=bool(work.get("abstract")),
                has_metadata=True,
                policy=AccessPolicy(allow_network=False),
            )
            paper_id = (
                f"paper:{doi}" if doi else f"paper:openalex:{work.get('work_id')}"
            )
            papers.append(
                build_paper(paper_id=paper_id, doi=doi, openalex=work, access=access)
            )
        snapshot = CohortSnapshot(
            cohort_id=f"cohort:{name.strip() or _slug(query)}",
            papers=tuple(sorted(papers, key=lambda paper: paper.paper_id)),
            query=query,
            source="openalex",
            reference_year=int(reference_year or 0),
            filters={"limit": int(limit)},
            notes=("metadata_only_snapshot",),
        )
        path = _cohort_dir(workspace) / f"{name.strip() or _slug(query)}.json"
        snapshot.write(path)
        return _result(
            status="ok",
            path=str(path),
            papers=len(snapshot.papers),
            reference_year=snapshot.reference_year,
            fingerprint=snapshot.fingerprint(),
            missing={
                field: sum(1 for paper in snapshot.papers if paper.missing.get(field))
                for field in ("title", "abstract", "year", "doi", "venue", "authors")
            },
            honesty_note="Abstracts and metadata only: no full text was fetched.",
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_fetch_paper(
    doi: str,
    name: str = "",
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Resolve one DOI through the ladder and append it to a cohort.

    Tries OpenAlex for bibliometrics and records the access decision: which rungs
    were tried, what was refused and what remains. When nothing readable is
    obtainable it returns the human acquisition steps instead of pretending.

    Args:
        doi: DOI of the paper to add.
        name: Cohort snapshot to append to (created when it does not exist).
        allow_network: Must be true for this tool to make any request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the access decision, the stored paper id and any refusal notes.
    """
    try:
        workspace = _workspace(config)
        transport, refusal = _transport_or_refuse(bool(allow_network))
        if transport is None:
            return _result(status="error", error=refusal)
        policy = ProviderPolicy.from_environment()
        result = fetch_work(doi, transport=transport, policy=policy)
        if not result.ok:
            return _result(
                status="error",
                error=f"openalex_{result.status.value}",
                detail=result.note,
                acquisition=acquisition_request_placeholder(doi),
            )
        work = _parse_work(result.payload or {})
        access = decide_access(
            doi,
            rank_candidates(
                doi,
                candidates_from_openalex(work.get("locations") or ()),
                policy=AccessPolicy(allow_network=False),
            ),
            has_abstract=bool(work.get("abstract")),
            has_metadata=True,
            policy=AccessPolicy(allow_network=False),
        )
        paper = build_paper(
            paper_id=f"paper:{work.get('doi') or doi}",
            doi=doi,
            openalex=work,
            access=access,
        )
        cohort_name = name.strip() or "manual"
        path = _cohort_dir(workspace) / f"{cohort_name}.json"
        existing = CohortSnapshot.load(path) if path.is_file() else None
        papers = list(existing.papers) if existing is not None else []
        papers = [item for item in papers if item.paper_id != paper.paper_id]
        papers.append(paper)
        snapshot = CohortSnapshot(
            cohort_id=(existing.cohort_id if existing else f"cohort:{cohort_name}"),
            papers=tuple(sorted(papers, key=lambda item: item.paper_id)),
            query=(existing.query if existing else ""),
            source="manual",
            reference_year=(existing.reference_year if existing else 0),
            filters=dict(existing.filters) if existing else {},
            notes=("manual_additions",),
        )
        snapshot.write(path)
        return _result(
            status="ok",
            paper_id=paper.paper_id,
            cohort=path.stem,
            papers=len(snapshot.papers),
            access=access.to_dict(),
            coverage=paper.coverage(),
            missing=paper.missing_fields(),
            full_text_note=(
                "No readable full text: full-text level features (sections, data "
                "statement, readability) are absent and recorded as such."
                if not paper.has_full_text
                else "Full text is available."
            ),
            acquisition_request=acquisition_request(paper),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_parse_pdf(
    paths: list[str] | None = None,
    name: str = "",
    config: RunnableConfig = None,
) -> str:
    """Parse operator-supplied PDFs and append them to a cohort snapshot.

    This is the top rung of the ladder: the operator already has the file. The
    text layer supplies sections, the data statement and reference candidates;
    nothing is invented for fields the PDF does not contain.

    Args:
        paths: PDF paths inside the task workspace.
        name: Cohort snapshot to append to (defaults to ``uploads``).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with each paper's route, sections found and missing fields.
    """
    try:
        workspace = _workspace(config)
        if not paths:
            raise ValueError("paths must list at least one PDF")
        cohort_name = name.strip() or "uploads"
        path = _cohort_dir(workspace) / f"{cohort_name}.json"
        existing = CohortSnapshot.load(path) if path.is_file() else None
        store = _root(workspace) / "store"
        papers = list(existing.papers) if existing is not None else []
        parsed = []
        for value in paths:
            target = _resolve(value, workspace)
            paper = paper_from_upload(target, directory=store)
            papers = [item for item in papers if item.paper_id != paper.paper_id]
            papers.append(paper)
            parsed.append(
                {
                    "paper_id": paper.paper_id,
                    "path": str(target),
                    "pages": paper.fulltext_pages,
                    "sections": sorted(paper.sections),
                    "open_data": [record.url for record in paper.open_data],
                    "references": len(paper.references),
                    "missing": paper.missing_fields(),
                }
            )
        snapshot = CohortSnapshot(
            cohort_id=(existing.cohort_id if existing else f"cohort:{cohort_name}"),
            papers=tuple(sorted(papers, key=lambda item: item.paper_id)),
            query=(existing.query if existing else ""),
            source="user_supplied",
            reference_year=(existing.reference_year if existing else 0),
            filters=dict(existing.filters) if existing else {},
            notes=("user_supplied_full_text",),
        )
        snapshot.write(path)
        return _result(
            status="ok",
            cohort=path.stem,
            path=str(path),
            papers=len(snapshot.papers),
            parsed=parsed,
            honesty_note=(
                "A user-supplied PDF has no citation history until it is matched to "
                "an indexed record, so it can be explained but not scored."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_run(
    cohort: str = "",
    uploads: list[str] | None = None,
    reference_year: int = 0,
    seed: int = 0,
    subject_ids: list[str] | None = None,
    audits: list[str] | None = None,
    horizon: int = 3,
    max_subjects: int = 5,
    run_id: str = "",
    config: RunnableConfig = None,
) -> str:
    """Fit, explain, audit and write the bundle for one cohort.

    Args:
        cohort: Cohort name or snapshot path (see infl_cohorts).
        uploads: Extra PDF paths to parse and explain in this run.
        reference_year: Year the citation window is measured at (0 = snapshot's).
        seed: Random seed for the model and the bootstrap.
        subject_ids: Papers to explain; defaults to the most-cited ones.
        audits: Subset of audit names (defaults to all ten).
        horizon: Citation window length in years.
        max_subjects: Maximum papers to explain in one run.
        run_id: Explicit run id (defaults to a timestamped id).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON summary: metrics, audit counts, package directory and notes.
    """
    try:
        workspace = _workspace(config)
        if not cohort.strip():
            raise ValueError(
                "cohort is required: a prediction needs a frozen reference "
                "population, otherwise the number is not comparable to anything."
            )
        snapshot = CohortSnapshot.load(_snapshot_path(workspace, cohort.strip()))
        snapshot = _apply_reference_year(snapshot, int(reference_year or 0))
        options = RunOptions(
            reference_year=int(reference_year or 0),
            horizon=int(horizon),
            seed=int(seed),
            max_subjects=int(max_subjects),
            subject_ids=tuple(subject_ids or ()),
            audits=tuple(audits) if audits else AUDITS,
        )
        paths = tuple(_resolve(value, workspace) for value in (uploads or ()))
        result = run_influence(
            workspace=workspace,
            snapshot=snapshot,
            uploads=paths,
            options=options,
            run_id=run_id.strip(),
        )
        return _result(
            status="ok",
            **result.summary(),
            package_dir=result.package_dir,
            report=str(Path(result.package_dir) / "report.md"),
            metrics_summary={
                key: result.metrics.get(key)
                for key in (
                    "spearman",
                    "top_decile_lift",
                    "interval_coverage",
                    "median_absolute_error_log",
                    "quantile_crossings",
                    "labelled",
                )
                if key in result.metrics
            },
            calibration=result.metrics.get("decile_calibration", []),
            next_steps=[
                "Read report.md for the interval, the factors and the audit.",
                "Use infl_explain for one paper's factors and comparators.",
                "Quote the interval width, not just the median.",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_status(run_id: str, config: RunnableConfig = None) -> str:
    """Read a finished run's stage receipts.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the stages, their counts and the run's package directory.
    """
    try:
        workspace = _workspace(config)
        record = load_run(_run_dir(workspace, run_id))
        return _result(status="ok", run=record)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_report(run_id: str, config: RunnableConfig = None) -> str:
    """Return the written report of a run.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the report path and its full text.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "report.md"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no report.md")
        return _result(
            status="ok", path=str(path), report=path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_bias(run_id: str, status: str = "", config: RunnableConfig = None) -> str:
    """List the bias audit findings of a run, optionally filtered by status.

    Args:
        run_id: Run identifier returned by infl_run.
        status: One of detected / not_detected / insufficient_evidence / cannot_assess.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of findings with method, evidence, conclusion and severity.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "bias_audit.csv"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no bias audit")
        wanted = status.strip().casefold()
        rows = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if wanted and row.get("status") != wanted:
                    continue
                rows.append(row)
        return _result(
            status="ok",
            findings=rows[:_MAX_LISTED],
            count=len(rows),
            detected=[row["label"] for row in rows if row.get("status") == "detected"],
            cannot_assess=[
                row["label"] for row in rows if row.get("status") == "cannot_assess"
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_explain(run_id: str, paper_id: str = "", config: RunnableConfig = None) -> str:
    """Return one paper's factor breakdown and comparators from a run.

    Args:
        run_id: Run identifier returned by infl_run.
        paper_id: Paper to read; defaults to the first explained paper.
        config: Runtime-injected task workspace configuration.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        package = directory / "package"
        factor_path = package / "factors.csv"
        if not factor_path.is_file():
            raise ValueError(f"run {run_id} has no explanations")
        rows = _read_csv(factor_path)
        subject = paper_id.strip() or (rows[0]["paper_id"] if rows else "")
        factors = [row for row in rows if row["paper_id"] == subject]
        comparators = [
            row
            for row in _read_csv(package / "comparators.csv")
            if row.get("paper_id") == subject
        ]
        prediction = next(
            (
                row
                for row in _read_csv(package / "predictions.csv")
                if row.get("paper_id") == subject
            ),
            {},
        )
        return _result(
            status="ok",
            paper_id=subject,
            prediction=prediction,
            factors=factors,
            comparators=comparators[:_MAX_LISTED],
            reading_hint=(
                "A factor is a delta: set it to the cohort reference value and see "
                "how the estimate moves. When the 95% CI crosses zero, the factor "
                "is not a usable explanation."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_package(run_id: str, config: RunnableConfig = None) -> str:
    """Return the bundle manifest of a run: files and their sha256 digests.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "manifest.json"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no manifest")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return _result(
            status="ok",
            directory=str(path.parent),
            manifest=manifest,
            verify_hint=(
                "Each artefact's sha256 is in the manifest, so a modified CSV is "
                "detectable without trusting the run."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_acquisition_request(
    doi: str = "", title: str = "", config: RunnableConfig = None
) -> str:
    """Describe how a human can legitimately obtain a paper the run could not read.

    Use this instead of giving up silently: it lists interlibrary loan, author
    request, library copy and purchase, in that order.

    Args:
        doi: DOI of the paper.
        title: Human-readable title for the request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the citation line and the numbered steps.
    """
    try:
        del config
        label = title.strip() or doi.strip() or "the paper"
        return _result(
            status="ok",
            title=label,
            doi=doi.strip(),
            steps=acquisition_request_placeholder(doi, title=label),
            refused=[
                "shadow-library mirrors",
                "borrowed or shared credentials",
                "scraping behind a paywall against the publisher's terms",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


def acquisition_request_placeholder(doi: str, *, title: str = "") -> dict[str, Any]:
    """The four legitimate routes a human can take, in order of effort."""
    label = title.strip() or doi.strip() or "the paper"
    return {
        "title": label,
        "doi": doi.strip(),
        "steps": [
            {
                "order": 1,
                "route": "interlibrary_loan",
                "detail": "Ask your library to borrow it.",
            },
            {
                "order": 2,
                "route": "author_request",
                "detail": "Email the corresponding author for a copy.",
            },
            {
                "order": 3,
                "route": "library_copy",
                "detail": "Use the institutional subscription you already have.",
            },
            {
                "order": 4,
                "route": "publisher_purchase",
                "detail": "Buy the article from the publisher.",
            },
        ],
        "note": (
            "Supply the file with infl_parse_pdf once obtained; the run will then "
            "use its text layer."
        ),
    }


def _parse_work(payload: dict[str, Any]) -> dict[str, Any]:
    from jw.research_influence.providers.openalex import parse_work

    return parse_work(payload)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _slug(text: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "-" for character in text.strip().lower()
    )
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts)[:48] or "cohort"


INFLUENCE_TOOLS = [
    infl_capabilities,
    infl_cohorts,
    infl_snapshot_synthetic,
    infl_snapshot_search,
    infl_fetch_paper,
    infl_parse_pdf,
    infl_run,
    infl_status,
    infl_report,
    infl_bias,
    infl_explain,
    infl_package,
    infl_acquisition_request,
]

register_tool_bundle("research-influence", INFLUENCE_TOOLS, include_in_main=False)

__all__ = ["INFLUENCE_TOOLS"]
